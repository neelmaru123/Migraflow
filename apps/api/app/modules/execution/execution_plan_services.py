"""
Execution Plan & Step Services
Manages the durable DAG execution plan, granular steps, dependencies, step claiming with row locks,
retry policies, authoritative checkpoints, and stale step recovery.
"""

from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Set, Tuple
import uuid


from sqlalchemy import and_, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.logging import logger
from app.core.state import (
    ExecutionEventType,
    ExecutionLifecycle,
    ExecutionPlanLifecycle,
    ExecutionStepLifecycle,
    ExecutionStepType,
    FailureCategory,
    InvalidStateTransitionError,
    RecoveryDecisionType,
)
from app.modules.execution.execution_models import (
    AgentRun,
    ExecutionCheckpoint,
    ExecutionEvent,
    MigrationExecutionPlan,
    MigrationExecutionStep,
    MigrationJob,
    UserIntervention,
)
from app.modules.execution.failure_taxonomy import ClassifiedFailure, FailureClassifier
from app.modules.execution.recovery_router import recovery_router
from app.modules.execution.retry_policy import DEFAULT_RETRY_POLICY, RetryPolicy
from app.modules.migration_plans.migration_plans_models import MigrationPlan


class ExecutionPlanService:
    """
    Authoritative service governing the durable execution DAG, step dependencies,
    authoritative database checkpoints, and resilient step recovery.
    """

    @classmethod
    async def create_execution_plan_for_job(
        cls,
        session: AsyncSession,
        job: MigrationJob,
        plan: MigrationPlan,
        concurrency_limit: int = 2,
    ) -> MigrationExecutionPlan:
        """
        Derives a durable MigrationExecutionPlan and its ordered DAG of MigrationExecutionSteps
        from an approved MigrationPlan. Does NOT modify the AI-generated MigrationPlan AST.
        """
        now = datetime.now(timezone.utc)
        plan_data: Dict[str, Any] = plan.plan_data or {}
        table_mappings: List[Dict[str, Any]] = plan_data.get("table_mappings", [])
        pre_ddl: List[str] = plan_data.get("pre_migration_ddl", [])
        post_ddl: List[str] = plan_data.get("post_migration_ddl", [])

        exec_plan = MigrationExecutionPlan(
            id=uuid.uuid4(),
            migration_job_id=job.id,
            migration_plan_id=plan.id,
            migration_plan_version_id=None,
            status=ExecutionPlanLifecycle.PENDING.value,
            concurrency_limit=max(1, concurrency_limit),
            created_at=now,
        )
        session.add(exec_plan)
        await session.flush()

        steps: List[MigrationExecutionStep] = []

        # 1. Step: PREFLIGHT
        step_preflight = MigrationExecutionStep(
            id=uuid.uuid4(),
            execution_plan_id=exec_plan.id,
            step_key="preflight",
            step_type=ExecutionStepType.PREFLIGHT.value,
            sequence=1,
            dependencies=[],
            status=ExecutionStepLifecycle.PENDING.value,
            attempt_count=0,
            max_attempts=3,
            input_definition={
                "target_config": plan.target_config or {},
                "is_dry_run": job.is_dry_run,
            },
            created_at=now,
            updated_at=now,
        )
        steps.append(step_preflight)

        # 2. Step: PRE_DDL
        step_pre_ddl = MigrationExecutionStep(
            id=uuid.uuid4(),
            execution_plan_id=exec_plan.id,
            step_key="pre_ddl",
            step_type=ExecutionStepType.PRE_DDL.value,
            sequence=2,
            dependencies=["preflight"],
            status=ExecutionStepLifecycle.PENDING.value,
            attempt_count=0,
            max_attempts=3,
            input_definition={
                "ddl_statements": pre_ddl,
                "truncate_target": job.truncate_target,
                "is_dry_run": job.is_dry_run,
            },
            created_at=now,
            updated_at=now,
        )
        steps.append(step_pre_ddl)

        # 3. Table Load Steps
        table_step_keys: List[str] = []
        for idx, mapping in enumerate(table_mappings):
            target_tbl = mapping.get("target_table_name") or f"table_{idx + 1}"
            step_key = f"load:{target_tbl}"
            table_step_keys.append(step_key)

            step_table = MigrationExecutionStep(
                id=uuid.uuid4(),
                execution_plan_id=exec_plan.id,
                step_key=step_key,
                step_type=ExecutionStepType.LOAD.value,
                sequence=3 + idx,
                dependencies=["pre_ddl"],
                status=ExecutionStepLifecycle.PENDING.value,
                attempt_count=0,
                max_attempts=3,
                input_definition={
                    "target_table": target_tbl,
                    "table_mapping": mapping,
                    "is_dry_run": job.is_dry_run,
                },
                created_at=now,
                updated_at=now,
            )
            steps.append(step_table)

        # 4. Step: POST_DDL
        post_ddl_deps = table_step_keys if table_step_keys else ["pre_ddl"]
        step_post_ddl = MigrationExecutionStep(
            id=uuid.uuid4(),
            execution_plan_id=exec_plan.id,
            step_key="post_ddl",
            step_type=ExecutionStepType.POST_DDL.value,
            sequence=3 + len(table_mappings),
            dependencies=post_ddl_deps,
            status=ExecutionStepLifecycle.PENDING.value,
            attempt_count=0,
            max_attempts=3,
            input_definition={
                "ddl_statements": post_ddl,
                "is_dry_run": job.is_dry_run,
            },
            created_at=now,
            updated_at=now,
        )
        steps.append(step_post_ddl)

        # 5. Step: VERIFY
        step_verify = MigrationExecutionStep(
            id=uuid.uuid4(),
            execution_plan_id=exec_plan.id,
            step_key="verify",
            step_type=ExecutionStepType.VERIFY.value,
            sequence=4 + len(table_mappings),
            dependencies=["post_ddl"],
            status=ExecutionStepLifecycle.PENDING.value,
            attempt_count=0,
            max_attempts=3,
            input_definition={
                "tables": [m.get("target_table_name") for m in table_mappings if m.get("target_table_name")],
                "is_dry_run": job.is_dry_run,
            },
            created_at=now,
            updated_at=now,
        )
        steps.append(step_verify)

        session.add_all(steps)
        await session.flush()

        # Emit EXECUTION_PLAN_CREATED event
        event = ExecutionEvent(
            id=uuid.uuid4(),
            event_id=uuid.uuid4(),
            migration_job_id=job.id,
            migration_plan_id=plan.id,
            event_type=ExecutionEventType.EXECUTION_PLAN_CREATED.value,
            actor_type="system",
            actor_id="execution_engine",
            timestamp=now,
            payload={
                "execution_plan_id": str(exec_plan.id),
                "total_steps": len(steps),
                "step_keys": [s.step_key for s in steps],
                "concurrency_limit": exec_plan.concurrency_limit,
            },
            schema_version=1,
        )
        session.add(event)

        logger.info(
            f"Derived MigrationExecutionPlan '{exec_plan.id}' with {len(steps)} steps for job '{job.id}'."
        )
        return exec_plan

    @classmethod
    async def claim_next_step(
        cls,
        session: AsyncSession,
        execution_plan_id: uuid.UUID,
        agent_id: uuid.UUID,
        agent_run_id: uuid.UUID,
    ) -> Optional[MigrationExecutionStep]:
        """
        Claims the next eligible execution step whose dependencies are satisfied,
        enforcing concurrency limits and utilizing row-level locks (SKIP LOCKED).
        """
        # Fetch execution plan
        # Fetch execution plan with plan for tenant isolation
        stmt_plan = (
            select(MigrationExecutionPlan)
            .options(
                selectinload(MigrationExecutionPlan.job),
                selectinload(MigrationExecutionPlan.plan),
            )
            .where(MigrationExecutionPlan.id == execution_plan_id)
            .with_for_update()
        )
        res_plan = await session.execute(stmt_plan)
        exec_plan = res_plan.scalar_one_or_none()
        if not exec_plan or exec_plan.status in (
            ExecutionPlanLifecycle.COMPLETED.value,
            ExecutionPlanLifecycle.FAILED.value,
            ExecutionPlanLifecycle.CANCELLED.value,
        ):
            return None

        # Verify tenant isolation: agent must belong to the same user as the migration plan
        if agent_id:
            from app.modules.agents.agents_models import Agent
            agent_obj = await session.get(Agent, agent_id)
            if agent_obj and exec_plan.plan and agent_obj.user_id != exec_plan.plan.user_id:
                logger.warning(
                    f"Agent '{agent_id}' (user '{agent_obj.user_id}') unauthorized to claim step "
                    f"for plan '{execution_plan_id}' (user '{exec_plan.plan.user_id}')."
                )
                return None

        # Fetch all steps for dependency evaluation
        stmt_all_steps = (
            select(MigrationExecutionStep)
            .where(MigrationExecutionStep.execution_plan_id == execution_plan_id)
            .order_by(MigrationExecutionStep.sequence.asc())
        )
        res_all = await session.execute(stmt_all_steps)
        all_steps = list(res_all.scalars().all())

        # Check concurrency limit against active running steps
        running_steps = [s for s in all_steps if s.status == ExecutionStepLifecycle.RUNNING.value]
        if len(running_steps) >= exec_plan.concurrency_limit:
            logger.info(
                f"ExecutionPlan '{execution_plan_id}' at concurrency limit ({len(running_steps)}/{exec_plan.concurrency_limit})."
            )
            return None

        completed_keys: Set[str] = {
            s.step_key for s in all_steps if s.status == ExecutionStepLifecycle.COMPLETED.value
        }

        now = datetime.now(timezone.utc)

        # Find the next eligible step
        for step in all_steps:
            if step.status not in (
                ExecutionStepLifecycle.PENDING.value,
                ExecutionStepLifecycle.RETRYING.value,
            ):
                continue

            # Verify all dependencies are completed
            deps = step.dependencies or []
            if all(dep in completed_keys for dep in deps):
                # Claim this step
                step.status = ExecutionStepLifecycle.RUNNING.value
                step.agent_run_id = agent_run_id
                step.started_at = now
                step.updated_at = now
                step.attempt_count += 1

                if exec_plan.status == ExecutionPlanLifecycle.PENDING.value:
                    exec_plan.status = ExecutionPlanLifecycle.RUNNING.value

                # Emit STEP_CLAIMED and STEP_STARTED event
                event = ExecutionEvent(
                    id=uuid.uuid4(),
                    event_id=uuid.uuid4(),
                    agent_run_id=agent_run_id,
                    migration_job_id=exec_plan.migration_job_id,
                    migration_plan_id=exec_plan.migration_plan_id,
                    event_type=ExecutionEventType.STEP_CLAIMED.value,
                    actor_type="agent",
                    actor_id=str(agent_id),
                    timestamp=now,
                    payload={
                        "step_id": str(step.id),
                        "step_key": step.step_key,
                        "step_type": step.step_type,
                        "attempt_count": step.attempt_count,
                    },
                    schema_version=1,
                )
                session.add(event)

                logger.info(
                    f"Agent '{agent_id}' claimed step '{step.step_key}' (Attempt {step.attempt_count}) in plan '{execution_plan_id}'."
                )
                return step

        return None

    @classmethod
    async def save_checkpoint(
        cls,
        session: AsyncSession,
        step_id: uuid.UUID,
        source_identifier: str,
        source_table: str,
        target_table: str,
        cursor_offset: int,
        rows_processed: int,
        source_position: Optional[Dict[str, Any]] = None,
    ) -> ExecutionCheckpoint:
        """
        Atomically saves or updates an authoritative execution checkpoint in the database.
        Increments checkpoint_version and emits a durable CHECKPOINT_SAVED event.
        """
        now = datetime.now(timezone.utc)
        stmt = (
            select(ExecutionCheckpoint)
            .where(
                ExecutionCheckpoint.execution_step_id == step_id,
                ExecutionCheckpoint.source_identifier == source_identifier,
                ExecutionCheckpoint.source_table == source_table,
                ExecutionCheckpoint.target_table == target_table,
            )
            .with_for_update()
        )
        res = await session.execute(stmt)
        checkpoint = res.scalar_one_or_none()

        if checkpoint:
            # Monotonic regression guard: protect against stale, out-of-order checkpoint payloads
            if cursor_offset >= checkpoint.cursor_offset:
                checkpoint.cursor_offset = cursor_offset
                checkpoint.rows_processed = max(rows_processed, checkpoint.rows_processed)
                if source_position is not None:
                    checkpoint.source_position = source_position
                checkpoint.checkpoint_version += 1
                checkpoint.updated_at = now
            else:
                logger.warning(
                    f"Ignored stale checkpoint regression for step '{step_id}': "
                    f"existing offset={checkpoint.cursor_offset}, incoming offset={cursor_offset}."
                )
        else:
            checkpoint = ExecutionCheckpoint(
                id=uuid.uuid4(),
                execution_step_id=step_id,
                source_identifier=source_identifier,
                source_table=source_table,
                target_table=target_table,
                cursor_offset=cursor_offset,
                rows_processed=rows_processed,
                source_position=source_position,
                checkpoint_version=1,
                created_at=now,
                updated_at=now,
            )
            session.add(checkpoint)

        await session.flush()

        # Emit CHECKPOINT_SAVED event
        event = ExecutionEvent(
            id=uuid.uuid4(),
            event_id=uuid.uuid4(),
            event_type=ExecutionEventType.CHECKPOINT_SAVED.value,
            actor_type="agent",
            actor_id="checkpoint_manager",
            timestamp=now,
            payload={
                "checkpoint_id": str(checkpoint.id),
                "step_id": str(step_id),
                "source_identifier": source_identifier,
                "source_table": source_table,
                "target_table": target_table,
                "cursor_offset": cursor_offset,
                "rows_processed": rows_processed,
                "checkpoint_version": checkpoint.checkpoint_version,
            },
            schema_version=1,
        )
        session.add(event)

        logger.info(
            f"Saved authoritative checkpoint for step '{step_id}' ({source_identifier}.{source_table} -> {target_table}): "
            f"offset={cursor_offset}, rows={rows_processed}, version={checkpoint.checkpoint_version}."
        )
        return checkpoint

    @classmethod
    async def get_checkpoints_for_step(
        cls,
        session: AsyncSession,
        step_id: uuid.UUID,
    ) -> List[ExecutionCheckpoint]:
        """Fetches all authoritative checkpoints associated with an execution step."""
        stmt = (
            select(ExecutionCheckpoint)
            .where(ExecutionCheckpoint.execution_step_id == step_id)
            .order_by(ExecutionCheckpoint.created_at.asc())
        )
        res = await session.execute(stmt)
        return list(res.scalars().all())

    @classmethod
    async def complete_step(
        cls,
        session: AsyncSession,
        step_id: uuid.UUID,
        output_summary: Optional[Dict[str, Any]] = None,
    ) -> MigrationExecutionStep:
        """
        Marks an execution step as COMPLETED. If all steps in the execution plan are COMPLETED,
        advances the execution plan and parent job to COMPLETED status.
        """
        stmt = (
            select(MigrationExecutionStep)
            .options(selectinload(MigrationExecutionStep.execution_plan))
            .where(MigrationExecutionStep.id == step_id)
            .with_for_update()
        )
        res = await session.execute(stmt)
        step = res.scalar_one_or_none()
        if not step:
            raise ValueError(f"Execution step '{step_id}' not found.")

        now = datetime.now(timezone.utc)
        step.status = ExecutionStepLifecycle.COMPLETED.value
        step.finished_at = now
        step.updated_at = now
        if output_summary:
            step.output_summary = output_summary

        # Emit STEP_COMPLETED event
        event = ExecutionEvent(
            id=uuid.uuid4(),
            event_id=uuid.uuid4(),
            agent_run_id=step.agent_run_id,
            migration_job_id=step.execution_plan.migration_job_id if step.execution_plan else None,
            migration_plan_id=step.execution_plan.migration_plan_id if step.execution_plan else None,
            event_type=ExecutionEventType.STEP_COMPLETED.value,
            actor_type="agent",
            actor_id=str(step.agent_run_id or "system"),
            timestamp=now,
            payload={
                "step_id": str(step.id),
                "step_key": step.step_key,
                "step_type": step.step_type,
                "output_summary": step.output_summary,
            },
            schema_version=1,
        )
        session.add(event)

        # Check if all steps in the plan are now completed
        if step.execution_plan_id:
            stmt_all = select(MigrationExecutionStep).where(
                MigrationExecutionStep.execution_plan_id == step.execution_plan_id
            )
            res_all = await session.execute(stmt_all)
            all_steps = list(res_all.scalars().all())

            if all(s.status == ExecutionStepLifecycle.COMPLETED.value for s in all_steps):
                exec_plan = step.execution_plan
                if exec_plan:
                    exec_plan.status = ExecutionPlanLifecycle.COMPLETED.value
                    exec_plan.finalized_at = now

                    # Emit EXECUTION_PLAN_COMPLETED event
                    plan_event = ExecutionEvent(
                        id=uuid.uuid4(),
                        event_id=uuid.uuid4(),
                        migration_job_id=exec_plan.migration_job_id,
                        migration_plan_id=exec_plan.migration_plan_id,
                        event_type=ExecutionEventType.EXECUTION_PLAN_COMPLETED.value,
                        actor_type="system",
                        actor_id="execution_engine",
                        timestamp=now,
                        payload={"execution_plan_id": str(exec_plan.id)},
                        schema_version=1,
                    )
                    session.add(plan_event)

        # Phase 4 Verification Hook: On verify step, run deterministic verification
        if step.step_type == ExecutionStepType.VERIFY.value or step.step_key == "verify":
            from app.modules.execution.verification_services import VerificationCoordinator, VerificationStatus
            exec_plan = step.execution_plan
            if exec_plan and not exec_plan.job.is_dry_run if exec_plan.job else False:
                v_verdict, v_results = await VerificationCoordinator.run_plan_verification(
                    session=session,
                    job_id=exec_plan.migration_job_id,
                    step_id=step.id,
                    verification_data=output_summary,
                )
                if v_verdict == VerificationStatus.FAILED:
                    step.status = ExecutionStepLifecycle.FAILED.value
                    logger.warning(f"Verify step '{step.step_key}' failed verification checks.")

        logger.info(f"Step '{step.step_key}' ({step.id}) completed successfully.")
        return step

    @classmethod
    async def fail_step(
        cls,
        session: AsyncSession,
        step_id: uuid.UUID,
        error_type: Optional[str] = None,
        error_message: Optional[str] = None,
        retry_policy: Optional[RetryPolicy] = None,
    ) -> MigrationExecutionStep:
        """
        Handles execution step failure via centralized FailureClassifier & RecoveryRouter.
        Determines deterministic outcome: RETRY, RECOVER, REPLAN, ASK_USER, or FAIL.
        """
        stmt = (
            select(MigrationExecutionStep)
            .options(selectinload(MigrationExecutionStep.execution_plan))
            .where(MigrationExecutionStep.id == step_id)
            .with_for_update()
        )
        res = await session.execute(stmt)
        step = res.scalar_one_or_none()
        if not step:
            raise ValueError(f"Execution step '{step_id}' not found.")

        now = datetime.now(timezone.utc)
        step.error_type = error_type
        step.error_message = error_message
        step.updated_at = now

        exec_plan = step.execution_plan
        job_id = exec_plan.migration_job_id if exec_plan else None
        plan_id = exec_plan.migration_plan_id if exec_plan else None

        # 1. Unified Failure Classification
        classified_failure = FailureClassifier.classify_error_payload(
            error_type=error_type,
            error_message=error_message or "",
            context={"step_key": step.step_key, "step_type": step.step_type},
        )
        step.failure_category = classified_failure.category.value
        step.failure_code = classified_failure.code

        # 2. Deterministic Recovery Decision
        decision = recovery_router.evaluate(
            failure=classified_failure,
            step_type=step.step_type,
            step_key=step.step_key,
            attempt_count=step.attempt_count,
            replan_count=step.replan_count,
            recovery_count=step.recovery_count,
            table_name=step.input_definition.get("target_table_name"),
        )

        logger.info(
            f"[ExecutionPlanService] Step '{step.step_key}' decision: action='{decision.action.value}', reason='{decision.reason}'"
        )

        # 3. Apply Decision
        if decision.action == RecoveryDecisionType.RETRY:
            step.status = ExecutionStepLifecycle.RETRYING.value
            event_type = ExecutionEventType.STEP_RETRYING.value

        elif decision.action == RecoveryDecisionType.RECOVER:
            step.status = ExecutionStepLifecycle.RETRYING.value
            step.recovery_count += 1
            if exec_plan:
                exec_plan.recovery_count += 1
            event_type = ExecutionEventType.RECOVERY_STARTED.value

        elif decision.action == RecoveryDecisionType.ASK_USER:
            step.status = ExecutionStepLifecycle.ASK_USER.value
            if exec_plan:
                exec_plan.status = ExecutionPlanLifecycle.ASK_USER.value
            if job_id:
                stmt_job = select(MigrationJob).where(MigrationJob.id == job_id)
                res_job = await session.execute(stmt_job)
                job = res_job.scalar_one_or_none()
                if job:
                    job.status = ExecutionLifecycle.ASK_USER.value
                    job.error_message = decision.reason

            # Create UserIntervention record
            prompt_data = decision.user_prompt or {}
            intervention = UserIntervention(
                id=uuid.uuid4(),
                migration_job_id=job_id,
                step_id=step.id,
                failure_category=classified_failure.category.value,
                failure_code=classified_failure.code,
                question=prompt_data.get("question", classified_failure.message),
                suggested_action=prompt_data.get("suggested_action", "user_intervention"),
                options=prompt_data.get("options", []),
                context_data=classified_failure.to_dict(),
                status="pending",
                created_at=now,
            )
            session.add(intervention)
            event_type = ExecutionEventType.USER_INTERVENTION_REQUESTED.value

        elif decision.action == RecoveryDecisionType.REPLAN:
            step.status = ExecutionStepLifecycle.FAILED.value
            step.finished_at = now
            event_type = ExecutionEventType.REPLAN_REQUESTED.value

            # Trigger automated replan via AgenticReplanService
            if job_id:
                try:
                    from app.modules.execution.agentic_replan_services import AgenticReplanService
                    await AgenticReplanService.replan_execution_failure(
                        session=session,
                        job_id=job_id,
                        step_id=step.id,
                        failure=classified_failure,
                    )
                except Exception as replan_err:
                    logger.error(f"Failed to trigger agentic replanning for step '{step.step_key}': {replan_err}")

        else:  # FAIL
            step.status = ExecutionStepLifecycle.FAILED.value
            step.finished_at = now
            event_type = ExecutionEventType.STEP_FAILED.value

            if exec_plan:
                exec_plan.status = ExecutionPlanLifecycle.FAILED.value
                exec_plan.finalized_at = now

                plan_event = ExecutionEvent(
                    id=uuid.uuid4(),
                    event_id=uuid.uuid4(),
                    migration_job_id=job_id,
                    migration_plan_id=plan_id,
                    event_type=ExecutionEventType.EXECUTION_PLAN_FAILED.value,
                    actor_type="system",
                    actor_id="execution_engine",
                    timestamp=now,
                    payload={
                        "execution_plan_id": str(exec_plan.id),
                        "failed_step_key": step.step_key,
                        "failure_category": classified_failure.category.value,
                        "failure_code": classified_failure.code,
                        "error_message": error_message,
                    },
                    schema_version=1,
                )
                session.add(plan_event)

                if job_id:
                    stmt_job = select(MigrationJob).where(MigrationJob.id == job_id)
                    res_job = await session.execute(stmt_job)
                    job = res_job.scalar_one_or_none()
                    if job:
                        job.status = ExecutionLifecycle.FAILED.value
                        job.completed_at = now
                        job.error_message = f"Step '{step.step_key}' failed: {error_message}"

        # Emit Step Audit Event
        event = ExecutionEvent(
            id=uuid.uuid4(),
            event_id=uuid.uuid4(),
            agent_run_id=step.agent_run_id,
            migration_job_id=job_id,
            migration_plan_id=plan_id,
            event_type=event_type,
            actor_type="agent",
            actor_id=str(step.agent_run_id or "system"),
            timestamp=now,
            payload={
                "step_id": str(step.id),
                "step_key": step.step_key,
                "attempt_count": step.attempt_count,
                "failure_category": classified_failure.category.value,
                "failure_code": classified_failure.code,
                "decision": decision.action.value,
                "error_type": error_type,
                "error_message": error_message,
            },
            schema_version=1,
        )
        session.add(event)
        return step

    @classmethod
    async def create_user_intervention(
        cls,
        session: AsyncSession,
        job_id: uuid.UUID,
        step_id: Optional[uuid.UUID],
        failure_category: str,
        failure_code: str,
        question: str,
        options: List[Dict[str, Any]],
        suggested_action: Optional[str] = None,
        context_data: Optional[Dict[str, Any]] = None,
    ) -> UserIntervention:
        """Explicitly creates a durable UserIntervention record and sets job to ask_user."""
        now = datetime.now(timezone.utc)
        intervention = UserIntervention(
            id=uuid.uuid4(),
            migration_job_id=job_id,
            step_id=step_id,
            failure_category=failure_category,
            failure_code=failure_code,
            question=question,
            suggested_action=suggested_action,
            options=options,
            context_data=context_data or {},
            status="pending",
            created_at=now,
        )
        session.add(intervention)

        stmt_job = select(MigrationJob).where(MigrationJob.id == job_id)
        res_job = await session.execute(stmt_job)
        job = res_job.scalar_one_or_none()
        if job:
            job.status = ExecutionLifecycle.ASK_USER.value
            job.error_message = question

        if step_id:
            stmt_step = select(MigrationExecutionStep).where(MigrationExecutionStep.id == step_id)
            res_step = await session.execute(stmt_step)
            step = res_step.scalar_one_or_none()
            if step:
                step.status = ExecutionStepLifecycle.ASK_USER.value

        event = ExecutionEvent(
            id=uuid.uuid4(),
            event_id=uuid.uuid4(),
            migration_job_id=job_id,
            event_type=ExecutionEventType.USER_INTERVENTION_REQUESTED.value,
            actor_type="system",
            actor_id="ExecutionPlanService",
            timestamp=now,
            payload={"question": question, "suggested_action": suggested_action},
            schema_version=1,
        )
        session.add(event)
        await session.commit()
        await session.refresh(intervention)
        return intervention

    @classmethod
    async def list_user_interventions(
        cls,
        session: AsyncSession,
        job_id: uuid.UUID,
    ) -> List[UserIntervention]:
        """Lists all user interventions for a migration job."""
        stmt = (
            select(UserIntervention)
            .where(UserIntervention.migration_job_id == job_id)
            .order_by(UserIntervention.created_at.desc())
        )
        res = await session.execute(stmt)
        return list(res.scalars().all())

    @classmethod
    async def resolve_user_intervention(
        cls,
        session: AsyncSession,
        intervention_id: uuid.UUID,
        user_id: uuid.UUID,
        action: str,
        response_data: Optional[Dict[str, Any]] = None,
    ) -> UserIntervention:
        """
        Resolves a pending UserIntervention and resumes or redirects execution:
        - 'retry' / 'truncate_and_proceed': Resumes execution
        - 'replan': Invokes replanning
        - 'fail' / 'abort': Permanently fails the job
        """
        stmt = (
            select(UserIntervention)
            .where(UserIntervention.id == intervention_id)
            .with_for_update()
        )
        res = await session.execute(stmt)
        intervention = res.scalar_one_or_none()
        if not intervention:
            raise ValueError(f"User intervention '{intervention_id}' not found.")

        now = datetime.now(timezone.utc)
        intervention.status = "resolved"
        intervention.user_response = {"action": action, "data": response_data or {}}
        intervention.resolved_by_user_id = user_id
        intervention.resolved_at = now

        job_id = intervention.migration_job_id
        step_id = intervention.step_id

        # Fetch job and plan
        stmt_job = (
            select(MigrationJob)
            .options(
                selectinload(MigrationJob.execution_plan).selectinload(MigrationExecutionPlan.steps),
                selectinload(MigrationJob.plan),
            )
            .where(MigrationJob.id == job_id)
        )
        res_job = await session.execute(stmt_job)
        job = res_job.scalar_one_or_none()

        action_norm = action.strip().lower()

        if action_norm in {"retry", "truncate_and_proceed", "proceed"}:
            if action_norm == "truncate_and_proceed" and job:
                job.truncate_target = True

            if job:
                job.status = ExecutionLifecycle.RUNNING.value
            if job and job.execution_plan:
                job.execution_plan.status = ExecutionPlanLifecycle.RUNNING.value

            if step_id and job and job.execution_plan:
                for s in job.execution_plan.steps:
                    if s.id == step_id:
                        s.status = ExecutionStepLifecycle.RETRYING.value
                        s.updated_at = now
                        break

            event = ExecutionEvent(
                id=uuid.uuid4(),
                event_id=uuid.uuid4(),
                migration_job_id=job_id,
                event_type=ExecutionEventType.USER_INTERVENTION_RESOLVED.value,
                actor_type="user",
                actor_id=str(user_id),
                timestamp=now,
                payload={"action": action, "response_data": response_data},
                schema_version=1,
            )
            session.add(event)

        elif action_norm in {"replan", "manual_edit"}:
            classified_failure = FailureClassifier.classify_error_payload(
                error_type=intervention.failure_category,
                error_message=intervention.question,
            )
            from app.modules.execution.agentic_replan_services import AgenticReplanService
            await AgenticReplanService.replan_execution_failure(
                session=session,
                job_id=job_id,
                step_id=step_id,
                failure=classified_failure,
            )

        elif action_norm in {"fail", "abort", "cancel"}:
            if job:
                job.status = ExecutionLifecycle.FAILED.value
                job.completed_at = now
                job.error_message = f"Aborted by user during intervention: {intervention.question}"
            if job and job.execution_plan:
                job.execution_plan.status = ExecutionPlanLifecycle.FAILED.value
                job.execution_plan.finalized_at = now
            if step_id and job and job.execution_plan:
                for s in job.execution_plan.steps:
                    if s.id == step_id:
                        s.status = ExecutionStepLifecycle.FAILED.value
                        s.finished_at = now
                        break

            event = ExecutionEvent(
                id=uuid.uuid4(),
                event_id=uuid.uuid4(),
                migration_job_id=job_id,
                event_type=ExecutionEventType.JOB_FAILED.value,
                actor_type="user",
                actor_id=str(user_id),
                timestamp=now,
                payload={"action": action, "reason": "Aborted by user via intervention."},
                schema_version=1,
            )
            session.add(event)

        await session.commit()
        await session.refresh(intervention)
        return intervention

    @classmethod
    async def recover_stale_steps(
        cls,
        session: AsyncSession,
        stale_threshold_seconds: int = 300,
    ) -> int:
        """
        Detects running steps whose heartbeat or agent timed out (>stale_threshold_seconds).
        Marks previous agent_run failed and transitions step to RETRYING (if attempts remain),
        making the step available for another agent to claim and resume from durable checkpoints.
        Does NOT restart the entire migration.
        """
        cutoff = datetime.now(timezone.utc) - timedelta(seconds=stale_threshold_seconds)
        stmt = (
            select(MigrationExecutionStep)
            .options(selectinload(MigrationExecutionStep.execution_plan))
            .where(
                MigrationExecutionStep.status == ExecutionStepLifecycle.RUNNING.value,
                MigrationExecutionStep.updated_at < cutoff,
            )
            .with_for_update()
        )
        res = await session.execute(stmt)
        stale_steps = list(res.scalars().all())

        recovered_count = 0
        now = datetime.now(timezone.utc)

        for step in stale_steps:
            # Mark previous AgentRun as failed due to timeout
            if step.agent_run_id:
                stmt_run = select(AgentRun).where(AgentRun.id == step.agent_run_id)
                res_run = await session.execute(stmt_run)
                run = res_run.scalar_one_or_none()
                if run and run.status not in (ExecutionLifecycle.COMPLETED.value, ExecutionLifecycle.FAILED.value):
                    run.status = ExecutionLifecycle.FAILED.value
                    run.failure_reason = "Agent heartbeat timed out during step execution."
                    run.finished_at = now

            if step.attempt_count < step.max_attempts:
                step.status = ExecutionStepLifecycle.RETRYING.value
                step.error_type = "AGENT_TIMEOUT"
                step.error_message = (
                    f"Agent execution stalled for >{stale_threshold_seconds}s. Step made recoverable."
                )
                step.updated_at = now
                recovered_count += 1
                logger.warning(
                    f"Watchdog recovered stale step '{step.step_key}' (Attempt {step.attempt_count}) in plan '{step.execution_plan_id}'."
                )
            else:
                step.status = ExecutionStepLifecycle.FAILED.value
                step.error_type = "AGENT_TIMEOUT_EXHAUSTED"
                step.error_message = f"Step failed after {step.attempt_count} attempts due to agent timeouts."
                step.finished_at = now
                step.updated_at = now
                logger.error(f"Step '{step.step_key}' failed after exhausting max retry attempts.")

        return recovered_count

    @classmethod
    async def get_execution_plan_by_job_id(
        cls,
        session: AsyncSession,
        job_id: uuid.UUID,
    ) -> Optional[MigrationExecutionPlan]:
        """Fetches a MigrationExecutionPlan with all its steps and checkpoints eagerly loaded."""
        stmt = (
            select(MigrationExecutionPlan)
            .options(
                selectinload(MigrationExecutionPlan.steps).selectinload(
                    MigrationExecutionStep.checkpoints
                )
            )
            .where(MigrationExecutionPlan.migration_job_id == job_id)
        )
        res = await session.execute(stmt)
        return res.scalar_one_or_none()

    @classmethod
    async def list_verification_results(
        cls,
        session: AsyncSession,
        job_id: uuid.UUID,
    ) -> List[Any]:
        """Fetches all durable verification results for an execution job, ordered by created_at."""
        from app.modules.execution.execution_models import VerificationResult
        stmt = (
            select(VerificationResult)
            .where(VerificationResult.migration_job_id == job_id)
            .order_by(VerificationResult.created_at.asc())
        )
        res = await session.execute(stmt)
        return list(res.scalars().all())

    @classmethod
    async def run_job_verification(
        cls,
        session: AsyncSession,
        job_id: uuid.UUID,
        step_id: Optional[uuid.UUID] = None,
        policy: Optional[Any] = None,
        verification_data: Optional[Dict[str, Any]] = None,
    ) -> Tuple[Any, List[Any]]:
        """Executes verification suite for an execution job and persists results."""
        from app.modules.execution.verification_services import VerificationCoordinator
        return await VerificationCoordinator.run_plan_verification(
            session=session,
            job_id=job_id,
            step_id=step_id,
            policy=policy,
            verification_data=verification_data,
        )
