"""
Agentic Replanning Service.
Connects execution failures back to LangGraph AI planning engine with sanitized context.
Enforces security rules (zero credentials, zero raw table rows to LLM), bounded loop guards,
and approval invalidation on replan.
"""

from datetime import datetime, timezone
import logging
import re
from typing import Any, Dict, Optional
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.state import (
    ExecutionEventType,
    MigrationPlanLifecycle,
)
from app.modules.execution.execution_models import (
    ExecutionEvent,
    MigrationExecutionPlan,
    MigrationExecutionStep,
    MigrationJob,
)
from app.modules.execution.failure_taxonomy import ClassifiedFailure
from app.modules.migration_plans.migration_plans_models import (
    MigrationPlan,
    MigrationPlanVersion,
)
from app.modules.migration_plans.migration_plans_services import MigrationPlanService
from app.modules.users.users_models import User

logger = logging.getLogger(__name__)


class AgenticReplanService:
    """
    Automates closed-loop replanning for execution failures via LangGraph.
    """

    @classmethod
    def sanitize_error_context(
        cls,
        failure: ClassifiedFailure,
        step_key: Optional[str] = None,
        table_name: Optional[str] = None,
    ) -> str:
        """
        Strips credentials, connection URIs, tokens, and raw table row data.
        Returns a clean, security-hardened instruction string for LLM refinement.
        """
        msg = failure.message or "Execution step failed."

        # Redact common credential patterns (password=..., user:pass@host, Bearer tokens, etc.)
        msg = re.sub(r'(?i)(password|secret|token|api_key|pwd)\s*=\s*[\S]+', r'\1=***REDACTED***', msg)
        msg = re.sub(r'://([^:]+):([^@]+)@', r'://\1:***REDACTED***@', msg)
        msg = re.sub(r'Bearer\s+[A-Za-z0-9\-._~+/]+=*', 'Bearer ***REDACTED***', msg)

        instructions = (
            f"Execution step '{step_key or 'unknown'}' failed during migration of table '{table_name or 'N/A'}'.\n"
            f"Error Category: {failure.category.value}\n"
            f"Error Code: {failure.code}\n"
            f"Diagnostic Message: {msg}\n\n"
            f"Action Required:\n"
            f"Update the TransformationPlan AST blueprint to eliminate this failure. "
            f"Adjust column mappings, data types, type conversions (e.g. type_cast, json_flatten), "
            f"or target DDL constraints to ensure seamless execution without data loss."
        )
        return instructions

    @classmethod
    async def replan_execution_failure(
        cls,
        session: AsyncSession,
        job_id: uuid.UUID,
        step_id: Optional[uuid.UUID],
        failure: ClassifiedFailure,
        user: Optional[User] = None,
    ) -> MigrationPlan:
        """
        Executes an agentic replan for a failed job/step:
        1. Sanitizes context
        2. Invokes LangGraph refinement to create a new plan version
        3. Invalidates previous approval (sets status to 'awaiting_approval')
        4. Increments replan counters and emits audit events.
        """
        # 1. Fetch job with plan, execution plan, and step
        stmt = (
            select(MigrationJob)
            .where(MigrationJob.id == job_id)
            .options(
                selectinload(MigrationJob.plan).selectinload(MigrationPlan.agent),
                selectinload(MigrationJob.execution_plan).selectinload(MigrationExecutionPlan.steps),
            )
        )
        res = await session.execute(stmt)
        job = res.scalar_one_or_none()
        if not job or not job.plan:
            raise ValueError(f"Job '{job_id}' or associated MigrationPlan not found.")

        plan = job.plan
        exec_plan = job.execution_plan

        # 2. Identify step key and table name
        step_key = None
        table_name = None
        if step_id and exec_plan:
            for s in exec_plan.steps:
                if s.id == step_id:
                    step_key = s.step_key
                    table_name = s.input_definition.get("target_table_name")
                    s.replan_count += 1
                    break

        if exec_plan:
            exec_plan.replan_count += 1

        # 3. Build sanitized prompt instructions
        sanitized_prompt = cls.sanitize_error_context(
            failure=failure, step_key=step_key, table_name=table_name
        )

        now = datetime.now(timezone.utc)

        # 4. Emit REPLAN_REQUESTED event
        event_req = ExecutionEvent(
            id=uuid.uuid4(),
            event_id=uuid.uuid4(),
            migration_job_id=job.id,
            migration_plan_id=plan.id,
            event_type=ExecutionEventType.REPLAN_REQUESTED.value,
            actor_type="system",
            actor_id="AgenticReplanService",
            timestamp=now,
            payload={
                "action": "replan_requested",
                "step_key": step_key,
                "failure_category": failure.category.value,
                "failure_code": failure.code,
                "replan_count": exec_plan.replan_count if exec_plan else 1,
            },
            schema_version=1,
        )
        session.add(event_req)
        await session.flush()

        # 5. Run LangGraph plan refinement
        try:
            refined_plan = await MigrationPlanService.refine_plan(
                session=session,
                plan=plan,
                user_feedback=sanitized_prompt,
                ignore_job_id=job.id,
            )
        except Exception as replan_err:
            logger.error(f"LangGraph replanning failed for job '{job_id}': {replan_err}")
            event_fail = ExecutionEvent(
                id=uuid.uuid4(),
                event_id=uuid.uuid4(),
                migration_job_id=job.id,
                migration_plan_id=plan.id,
                event_type=ExecutionEventType.REPLAN_FAILED.value,
                actor_type="system",
                actor_id="AgenticReplanService",
                timestamp=datetime.now(timezone.utc),
                payload={"error": str(replan_err)},
                schema_version=1,
            )
            session.add(event_fail)
            await session.commit()
            raise replan_err

        # 6. APPROVAL INVALIDATION:
        # A structural replan requires fresh approval before new execution can commence.
        from app.modules.execution.safety_services import DestructiveApprovalManager

        plan.status = MigrationPlanLifecycle.AWAITING_APPROVAL.value
        plan.approved_version_number = None
        plan.approved_by_user_id = None
        plan.approved_at = None

        await DestructiveApprovalManager.invalidate_all_for_plan(
            session, plan.id, "Plan replanned due to execution failure"
        )

        event_revoked = ExecutionEvent(
            id=uuid.uuid4(),
            event_id=uuid.uuid4(),
            migration_job_id=job.id,
            migration_plan_id=plan.id,
            event_type=ExecutionEventType.PLAN_APPROVAL_REVOKED.value,
            actor_type="system",
            actor_id="AgenticReplanService",
            timestamp=datetime.now(timezone.utc),
            payload={
                "reason": "Replanned due to execution failure; previous approval invalidated.",
                "replan_attempt": exec_plan.replan_count if exec_plan else 1,
            },
            schema_version=1,
        )
        from sqlalchemy import func
        stmt_ver_num = (
            select(func.coalesce(func.max(MigrationPlanVersion.version_number), 1))
            .where(MigrationPlanVersion.migration_plan_id == plan.id)
        )
        new_version_number = (await session.execute(stmt_ver_num)).scalar_one()

        event_completed = ExecutionEvent(
            id=uuid.uuid4(),
            event_id=uuid.uuid4(),
            migration_job_id=job.id,
            migration_plan_id=plan.id,
            event_type=ExecutionEventType.REPLAN_COMPLETED.value,
            actor_type="system",
            actor_id="AgenticReplanService",
            timestamp=datetime.now(timezone.utc),
            payload={
                "plan_id": str(plan.id),
                "new_version": new_version_number,
            },
            schema_version=1,
        )
        session.add_all([event_revoked, event_completed])
        await session.commit()

        logger.info(
            f"Agentic replanning succeeded for plan '{plan.id}'. Status transitioned to 'awaiting_approval' (Approval revoked)."
        )
        return plan
