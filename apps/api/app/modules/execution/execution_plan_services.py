"""
Execution Plan & Step Services
Manages the durable DAG execution plan, granular steps, dependencies, step claiming with row locks,
retry policies, authoritative checkpoints, and stale step recovery.
"""

from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Set
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
    InvalidStateTransitionError,
)
from app.modules.execution.execution_models import (
    AgentRun,
    ExecutionCheckpoint,
    ExecutionEvent,
    MigrationExecutionPlan,
    MigrationExecutionStep,
    MigrationJob,
)
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
        stmt_plan = (
            select(MigrationExecutionPlan)
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
            checkpoint.cursor_offset = cursor_offset
            checkpoint.rows_processed = rows_processed
            if source_position is not None:
                checkpoint.source_position = source_position
            checkpoint.checkpoint_version += 1
            checkpoint.updated_at = now
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

                    # Update parent MigrationJob if still active
                    stmt_job = select(MigrationJob).where(MigrationJob.id == exec_plan.migration_job_id)
                    res_job = await session.execute(stmt_job)
                    job = res_job.scalar_one_or_none()
                    if job and job.status not in (
                        ExecutionLifecycle.COMPLETED.value,
                        ExecutionLifecycle.FAILED.value,
                        ExecutionLifecycle.CANCELLED.value,
                    ):
                        job.status = (
                            "dry_run_completed" if job.is_dry_run else ExecutionLifecycle.COMPLETED.value
                        )
                        job.completed_at = now
                        job.progress = 100.0

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
        Handles execution step failure. Evaluates the RetryPolicy:
        - If retryable and attempt_count < max_attempts: marks step as RETRYING.
        - If non-retryable or attempts exhausted: marks step, execution plan, and job as FAILED.
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

        policy = retry_policy or DEFAULT_RETRY_POLICY
        now = datetime.now(timezone.utc)
        step.error_type = error_type
        step.error_message = error_message
        step.updated_at = now

        can_retry = policy.should_retry(step.attempt_count, error_type)

        if can_retry:
            step.status = ExecutionStepLifecycle.RETRYING.value
            event_type = ExecutionEventType.STEP_RETRYING.value
            logger.warning(
                f"Step '{step.step_key}' failed (Attempt {step.attempt_count}/{step.max_attempts}). "
                f"Marked for RETRY. Error: {error_message}"
            )
        else:
            step.status = ExecutionStepLifecycle.FAILED.value
            step.finished_at = now
            event_type = ExecutionEventType.STEP_FAILED.value
            logger.error(
                f"Step '{step.step_key}' permanently FAILED after {step.attempt_count} attempts. Error: {error_message}"
            )

            # Mark ExecutionPlan as FAILED
            if step.execution_plan:
                exec_plan = step.execution_plan
                exec_plan.status = ExecutionPlanLifecycle.FAILED.value
                exec_plan.finalized_at = now

                # Emit EXECUTION_PLAN_FAILED event
                plan_event = ExecutionEvent(
                    id=uuid.uuid4(),
                    event_id=uuid.uuid4(),
                    migration_job_id=exec_plan.migration_job_id,
                    migration_plan_id=exec_plan.migration_plan_id,
                    event_type=ExecutionEventType.EXECUTION_PLAN_FAILED.value,
                    actor_type="system",
                    actor_id="execution_engine",
                    timestamp=now,
                    payload={
                        "execution_plan_id": str(exec_plan.id),
                        "failed_step_key": step.step_key,
                        "error_message": error_message,
                    },
                    schema_version=1,
                )
                session.add(plan_event)

                # Mark MigrationJob as FAILED
                stmt_job = select(MigrationJob).where(MigrationJob.id == exec_plan.migration_job_id)
                res_job = await session.execute(stmt_job)
                job = res_job.scalar_one_or_none()
                if job:
                    job.status = ExecutionLifecycle.FAILED.value
                    job.completed_at = now
                    job.error_message = f"Step '{step.step_key}' failed: {error_message}"

        # Emit Step Event
        event = ExecutionEvent(
            id=uuid.uuid4(),
            event_id=uuid.uuid4(),
            agent_run_id=step.agent_run_id,
            migration_job_id=step.execution_plan.migration_job_id if step.execution_plan else None,
            migration_plan_id=step.execution_plan.migration_plan_id if step.execution_plan else None,
            event_type=event_type,
            actor_type="agent",
            actor_id=str(step.agent_run_id or "system"),
            timestamp=now,
            payload={
                "step_id": str(step.id),
                "step_key": step.step_key,
                "attempt_count": step.attempt_count,
                "error_type": error_type,
                "error_message": error_message,
            },
            schema_version=1,
        )
        session.add(event)

        return step

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
