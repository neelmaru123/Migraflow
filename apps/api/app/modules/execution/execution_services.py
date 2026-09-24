"""
Execution Domain Business Services
"""

import asyncio
import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
from fastapi import HTTPException, status
from sqlalchemy import select, update, and_, or_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.db import AsyncSessionLocal
from app.core.state import (
    ExecutionEventType,
    ExecutionLifecycle,
    InvalidStateTransitionError,
)
from app.modules.agents.agents_models import Agent
from app.modules.execution.execution_models import (
    AgentRun,
    ExecutionCheckpoint,
    ExecutionEvent,
    MigrationExecutionPlan,
    MigrationExecutionStep,
    MigrationJob,
)
from app.modules.execution.execution_plan_services import ExecutionPlanService
from app.modules.execution.execution_state_machine import ExecutionStateMachine
from app.modules.execution.execution_schemas import (
    ExecutionProgressUpdate,
    ExecutionStartRequest,
)
from app.modules.migration_plans.migration_plans_models import MigrationPlan
from app.modules.sources.sources_models import DataSource
from app.modules.metadata.metadata_services import MetadataService
from app.core.config import settings
from app.core.logging import logger
from app.core.websocket_manager import manager


class ExecutionService:
    """Business logic for Migration Execution Jobs lifecycle."""

    @staticmethod
    async def check_target_tables_existing_data(
        session: AsyncSession, plan: MigrationPlan
    ) -> list[dict]:
        """
        Preflight check querying the target data source via the agent's most recent
        metadata snapshot (without live DB connection from the API) to check if any of
        the plan's target tables already contain rows.
        """
        if not plan.agent_id or not plan.plan_data:
            return []

        target_table_names: list[str] = []
        if isinstance(plan.plan_data, dict):
            for tm in plan.plan_data.get("table_mappings", []):
                tbl = tm.get("target_table_name")
                if tbl and tbl not in target_table_names:
                    target_table_names.append(tbl)

        if not target_table_names:
            return []

        # Find target DataSource(s) for the agent
        target_config = plan.target_config if isinstance(plan.target_config, dict) else {}
        target_identifier = target_config.get("identifier")

        stmt_ds = select(DataSource).where(
            DataSource.agent_id == plan.agent_id,
            or_(
                DataSource.role.in_(["target", "both"]),
                DataSource.identifier == target_identifier if target_identifier else False,
            ),
        )
        res_ds = await session.execute(stmt_ds)
        target_data_sources = list(res_ds.scalars().all())

        # If none marked target/both, fallback to matching target_db_type or checking all agent data sources
        if not target_data_sources:
            target_db_type = (target_config.get("database_type") or "").lower()
            stmt_all = select(DataSource).where(DataSource.agent_id == plan.agent_id)
            res_all = await session.execute(stmt_all)
            all_ds = list(res_all.scalars().all())
            for ds in all_ds:
                if target_db_type and ds.type.lower() == target_db_type:
                    target_data_sources.append(ds)

        target_tables_with_existing_data: list[dict] = []
        seen_tables = set()
        target_tables_norm = {t.lower(): t for t in target_table_names}

        for ds in target_data_sources:
            snapshot = await MetadataService.get_latest_snapshot_for_source(session, ds.id)
            if not snapshot or not snapshot.schemas:
                continue
            for schema in snapshot.schemas:
                for table in (schema.tables or []):
                    table_norm = table.table_name.lower()
                    if table_norm in target_tables_norm and table.row_count > 0:
                        original_name = target_tables_norm[table_norm]
                        if original_name not in seen_tables:
                            seen_tables.add(original_name)
                            target_tables_with_existing_data.append({
                                "table_name": original_name,
                                "existing_row_count": table.row_count,
                            })

        return target_tables_with_existing_data

    @staticmethod
    async def create_execution_job(
        session: AsyncSession,
        user_id: uuid.UUID,
        plan_id: uuid.UUID,
        is_dry_run: bool = False,
        truncate_target: bool = False,
        idempotency_key: Optional[str] = None,
    ) -> MigrationJob:
        # Check plan existence and ownership
        stmt_plan = select(MigrationPlan).where(
            MigrationPlan.id == plan_id, MigrationPlan.user_id == user_id
        )
        res_plan = await session.execute(stmt_plan)
        plan = res_plan.scalar_one_or_none()
        if not plan:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Migration plan not found or access denied.",
            )

        if not plan.is_valid:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Cannot execute invalid plan! Fix schema feasibility errors first.",
            )

        if plan.status != "completed" and plan.status != "approved":
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Cannot execute unapproved migration plan. Plan status is '{plan.status}'. User approval is required.",
            )

        # Idempotency check: if an idempotency key is provided, return existing job if already submitted
        clean_idempotency_key = idempotency_key.strip() if idempotency_key else None
        if clean_idempotency_key:
            stmt_idem = (
                select(MigrationJob)
                .join(MigrationPlan, MigrationJob.migration_plan_id == MigrationPlan.id)
                .where(
                    MigrationJob.idempotency_key == clean_idempotency_key,
                    MigrationPlan.user_id == user_id,
                )
            )
            res_idem = await session.execute(stmt_idem)
            existing_job = res_idem.scalar_one_or_none()
            if existing_job:
                logger.info(
                    f"Idempotent execution request matched existing job '{existing_job.id}' for key '{clean_idempotency_key}'."
                )
                return existing_job

        # Check for active running job for this plan
        stmt_active = select(MigrationJob).where(
            MigrationJob.migration_plan_id == plan_id,
            MigrationJob.status.in_(["queued", "preparing", "running"]),
        )
        res_active = await session.execute(stmt_active)
        if res_active.scalar_one_or_none():
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="An active execution job is already in progress for this plan.",
            )

        # Check assigned Docker Agent online status before queuing execution
        if plan.agent_id:
            stmt_agent = select(Agent).where(Agent.id == plan.agent_id)
            res_agent = await session.execute(stmt_agent)
            agent = res_agent.scalar_one_or_none()

            now_utc = datetime.now(timezone.utc)

            # Adaptive cutoff based on agent power/heartbeat mode:
            # Active mode (heartbeat every 20s): cutoff is 60s
            # Standby mode (idle 5+ min, heartbeat every 300s): cutoff is 360s
            is_standby = False
            if agent and agent.idle_since is not None:
                idle_since_aware = (
                    agent.idle_since
                    if agent.idle_since.tzinfo
                    else agent.idle_since.replace(tzinfo=timezone.utc)
                )
                if (now_utc - idle_since_aware).total_seconds() >= 300:
                    is_standby = True

            effective_threshold_sec = 360 if is_standby else 60
            cutoff_utc = now_utc - timedelta(seconds=effective_threshold_sec)
            cutoff_naive_utc = now_utc.replace(tzinfo=None) - timedelta(seconds=effective_threshold_sec)
            cutoff_naive_local = datetime.now() - timedelta(seconds=effective_threshold_sec)

            is_offline = False
            if not agent or not agent.last_seen_at:
                is_offline = True
            elif agent.status in ("error", "offline"):
                is_offline = True
            elif agent.last_seen_at.tzinfo is not None and agent.last_seen_at < cutoff_utc:
                is_offline = True
            elif agent.last_seen_at.tzinfo is None and agent.last_seen_at < cutoff_naive_utc and agent.last_seen_at < cutoff_naive_local:
                is_offline = True

            if is_offline:
                agent_name = agent.name if agent else "Assigned Agent"
                status_detail = f" (status: '{agent.status}')" if agent and agent.status else ""
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail=f"Cannot execute plan: {agent_name} is currently offline or unreachable{status_detail}. Ensure the agent Docker container is actively running on the host.",
                )

        # Reset idle_since: agent is now active with a new job
        if plan.agent_id:
            stmt_reset_idle = select(Agent).where(Agent.id == plan.agent_id)
            res_reset_idle = await session.execute(stmt_reset_idle)
            agent_for_reset = res_reset_idle.scalar_one_or_none()
            if agent_for_reset:
                agent_for_reset.idle_since = None

        # Check if target tables already contain rows (preflight check via snapshot)
        existing_data_warnings = await ExecutionService.check_target_tables_existing_data(
            session=session, plan=plan
        )
        if existing_data_warnings:
            logger.warning(
                f"Preflight alert for plan '{plan_id}': Target tables already contain rows: {existing_data_warnings}"
            )

        now = datetime.now(timezone.utc)
        job = MigrationJob(
            migration_plan_id=plan_id,
            agent_id=plan.agent_id,
            idempotency_key=clean_idempotency_key,
            status=ExecutionLifecycle.QUEUED.value,
            is_dry_run=is_dry_run,
            truncate_target=truncate_target,
            total_rows=0,
            processed_rows=0,
            successful_rows=0,
            failed_rows=0,
            progress=0.0,
            current_stage="queued",
            created_at=now,
            updated_at=now,
        )
        session.add(job)
        await session.flush()

        # Derive durable MigrationExecutionPlan and DAG steps
        await ExecutionPlanService.create_execution_plan_for_job(
            session=session, job=job, plan=plan
        )

        # Emit durable append-only JOB_CREATED event
        event = ExecutionEvent(
            id=uuid.uuid4(),
            event_id=uuid.uuid4(),
            migration_job_id=job.id,
            migration_plan_id=plan_id,
            event_type=ExecutionEventType.JOB_CREATED.value,
            actor_type="user",
            actor_id=str(user_id),
            timestamp=now,
            payload={
                "action": "job_created",
                "job_id": str(job.id),
                "is_dry_run": is_dry_run,
                "truncate_target": truncate_target,
                "idempotency_key": clean_idempotency_key,
            },
            schema_version=1,
        )
        session.add(event)
        await session.commit()
        await session.refresh(job)

        # Attach preflight warnings for response serialization
        setattr(job, "target_tables_with_existing_data", existing_data_warnings)

        logger.info(
            f"Created execution job '{job.id}' for plan '{plan_id}' (Agent ID: {plan.agent_id}, IdempotencyKey: {clean_idempotency_key})."
        )
        return job

    @staticmethod
    async def get_pending_tasks_for_agent(
        session: AsyncSession, agent_id: uuid.UUID
    ) -> List[MigrationJob]:
        """
        Returns queued or stale preparing migration jobs assigned to a specific Docker Agent,
        atomically claiming them with row-level locking (SKIP LOCKED) and transitioning
        status to 'preparing' within the same transaction to prevent double execution.
        """
        cutoff_preparing = datetime.now(timezone.utc) - timedelta(seconds=30)
        stmt_select = (
            select(MigrationJob.id)
            .where(
                MigrationJob.agent_id == agent_id,
                or_(
                    MigrationJob.status == "queued",
                    and_(
                        MigrationJob.status == "preparing",
                        MigrationJob.updated_at < cutoff_preparing,
                    ),
                ),
            )
            .order_by(MigrationJob.created_at.asc())
            .with_for_update(skip_locked=True)
        )
        res_ids = await session.execute(stmt_select)
        job_ids = list(res_ids.scalars().all())

        if not job_ids:
            return []

        now = datetime.now(timezone.utc)
        stmt_update = (
            update(MigrationJob)
            .where(
                MigrationJob.id.in_(job_ids),
                or_(
                    MigrationJob.status == "queued",
                    and_(
                        MigrationJob.status == "preparing",
                        MigrationJob.updated_at < cutoff_preparing,
                    ),
                ),
            )
            .values(status="preparing", updated_at=now)
        )
        res_update = await session.execute(stmt_update)
        if res_update.rowcount == 0:
            await session.rollback()
            return []

        await session.commit()

        stmt_fetch = (
            select(MigrationJob)
            .options(selectinload(MigrationJob.execution_plan))
            .where(MigrationJob.id.in_(job_ids))
        )
        res_fetch = await session.execute(stmt_fetch)
        jobs = list(res_fetch.scalars().all())

        for job in jobs:
            # Instantiate first-class AgentRun execution attempt and emit event
            await ExecutionStateMachine.create_agent_run(
                session=session,
                job=job,
                agent_id=agent_id,
                status=ExecutionLifecycle.PREPARING,
                actor_type="agent",
                actor_id=str(agent_id),
            )
            if job.execution_plan:
                setattr(job, "execution_plan_id", job.execution_plan.id)
        await session.commit()
        return jobs

    @staticmethod
    async def check_stale_jobs(
        session: AsyncSession, stale_threshold_seconds: int = 300
    ) -> int:
        """
        Backend Watchdog: Detects execution jobs stuck in 'running' or 'preparing'
        with no progress update for longer than stale_threshold_seconds (default 5 minutes).
        Transitions stuck jobs to 'failed' with an explicit error message.
        """
        cutoff = datetime.now(timezone.utc) - timedelta(seconds=stale_threshold_seconds)
        stmt = select(MigrationJob).where(
            MigrationJob.status.in_(["queued", "running", "preparing"]),
            MigrationJob.updated_at < cutoff,
        )
        res = await session.execute(stmt)
        stale_jobs = list(res.scalars().all())

        if not stale_jobs:
            return 0

        for job in stale_jobs:
            err = (
                f"Migration job stalled: no progress updates received from agent for over {stale_threshold_seconds} seconds."
            )
            await ExecutionStateMachine.transition_job(
                session=session,
                job=job,
                target_state=ExecutionLifecycle.FAILED,
                actor_type="watchdog",
                actor_id="watchdog",
                reason=err,
            )
            logger.error(
                f"Watchdog failed stale job '{job.id}' (last updated: {job.updated_at})."
            )
            if job.agent_id:
                await manager.broadcast_to_agent(
                    str(job.agent_id),
                    {
                        "event": "JOB_FAILED",
                        "agent_id": str(job.agent_id),
                        "job_id": str(job.id),
                        "status": "failed",
                        "error_message": job.error_message,
                    },
                )

        # Recover stale steps across all active execution plans
        await ExecutionPlanService.recover_stale_steps(
            session=session, stale_threshold_seconds=stale_threshold_seconds
        )

        await session.commit()
        return len(stale_jobs)

    @staticmethod
    async def get_job_by_id(
        session: AsyncSession, user_id: uuid.UUID, job_id: uuid.UUID
    ) -> MigrationJob:
        """
        Fetches a MigrationJob by ID, verifying user ownership via attached MigrationPlan.
        """
        await ExecutionService.check_stale_jobs(session)
        stmt = (
            select(MigrationJob)
            .join(MigrationPlan, MigrationJob.migration_plan_id == MigrationPlan.id)
            .where(MigrationJob.id == job_id, MigrationPlan.user_id == user_id)
        )
        res = await session.execute(stmt)
        job = res.scalar_one_or_none()
        if not job:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Execution job '{job_id}' not found or access denied.",
            )
        return job

    @staticmethod
    async def cancel_execution_job(
        session: AsyncSession,
        user_id: uuid.UUID,
        job_id: uuid.UUID,
        reason: Optional[str] = None,
    ) -> MigrationJob:
        """
        Cancels an active ('queued', 'preparing', 'running') execution job.
        Frees the migration plan for subsequent runs and resets the assigned Docker Agent status.
        """
        stmt = (
            select(MigrationJob)
            .join(MigrationPlan, MigrationJob.migration_plan_id == MigrationPlan.id)
            .where(MigrationJob.id == job_id, MigrationPlan.user_id == user_id)
        )
        res = await session.execute(stmt)
        job = res.scalar_one_or_none()
        if not job:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Execution job '{job_id}' not found or access denied.",
            )

        # Enforce legal transition to CANCELLED via state machine
        try:
            await ExecutionStateMachine.transition_job(
                session=session,
                job=job,
                target_state=ExecutionLifecycle.CANCELLED,
                actor_type="user",
                actor_id=str(user_id),
                reason=reason or "Execution cancelled by user.",
            )
        except InvalidStateTransitionError as err:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Cannot cancel execution job with status '{job.status}'. {err}",
            )

        now = datetime.now(timezone.utc)
        # Reset assigned Docker Agent status to online
        if job.agent_id:
            stmt_agent = select(Agent).where(Agent.id == job.agent_id)
            res_agent = await session.execute(stmt_agent)
            agent_obj = res_agent.scalar_one_or_none()
            if agent_obj:
                if agent_obj.status in ["busy", "offline", "error"]:
                    agent_obj.status = "online"
                agent_obj.idle_since = now

            # Broadcast cancellation event via WebSocket
            await manager.broadcast_to_agent(
                str(job.agent_id),
                {
                    "event_type": "EXECUTION_PROGRESS",
                    "data": {
                        "job_id": str(job.id),
                        "status": "cancelled",
                        "progress": job.progress,
                        "processed_rows": job.processed_rows,
                        "successful_rows": job.successful_rows,
                        "failed_rows": job.failed_rows,
                        "current_stage": "cancelled",
                        "error_message": job.error_message,
                    },
                    "job_id": str(job.id),
                    "status": "cancelled",
                    "error_message": job.error_message,
                },
            )

        await session.commit()
        await session.refresh(job)
        logger.info(f"Execution job '{job.id}' was cancelled by user '{user_id}'. Reason: {job.error_message}")
        return job

    @staticmethod
    async def list_jobs_for_user(
        session: AsyncSession, user_id: uuid.UUID
    ) -> List[MigrationJob]:
        """
        Lists all execution jobs for a user across all migration plans.
        """
        await ExecutionService.check_stale_jobs(session)
        stmt = (
            select(MigrationJob)
            .join(MigrationPlan, MigrationJob.migration_plan_id == MigrationPlan.id)
            .where(MigrationPlan.user_id == user_id)
            .order_by(MigrationJob.created_at.desc())
        )
        res = await session.execute(stmt)
        return list(res.scalars().all())

    @staticmethod
    async def update_job_progress(
        session: AsyncSession,
        job_id: uuid.UUID,
        update: ExecutionProgressUpdate,
        agent_id: Optional[uuid.UUID] = None,
    ) -> MigrationJob:
        """
        Updates live metrics and status for a MigrationJob from Docker Agent progress payload.
        Ensures the updating agent is the one assigned to the job.
        Transitions state through ExecutionStateMachine.
        """
        stmt = select(MigrationJob).where(MigrationJob.id == job_id)
        if agent_id:
            stmt = stmt.where(MigrationJob.agent_id == agent_id)

        res = await session.execute(stmt)
        job = res.scalar_one_or_none()
        if not job:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Execution job '{job_id}' not found or access denied for this agent.",
            )

        # If job was already cancelled by the user, return cancelled status immediately without overwriting
        if job.status == "cancelled":
            return job

        # Synchronize run identity if provided by agent
        if update.agent_run_id and job.current_run_id != update.agent_run_id:
            job.current_run_id = update.agent_run_id

        # Normalize and transition through state machine if state has changed
        norm_target_state = ExecutionStateMachine.normalize_state(update.status)
        norm_curr_state = ExecutionStateMachine.normalize_state(job.status)

        if norm_target_state != norm_curr_state:
            # If agent directly reports RUNNING or COMPLETED from QUEUED, advance through intermediate states
            if norm_curr_state == ExecutionLifecycle.QUEUED and norm_target_state in (
                ExecutionLifecycle.RUNNING,
                ExecutionLifecycle.COMPLETED,
            ):
                await ExecutionStateMachine.transition_job(
                    session=session,
                    job=job,
                    target_state=ExecutionLifecycle.PREPARING,
                    actor_type="agent",
                    actor_id=str(agent_id or job.agent_id or "agent"),
                    reason="Auto-advance on progress update",
                )
                norm_curr_state = ExecutionLifecycle.PREPARING

            if norm_curr_state in (
                ExecutionLifecycle.CLAIMED,
                ExecutionLifecycle.PREPARING,
            ) and norm_target_state == ExecutionLifecycle.COMPLETED:
                await ExecutionStateMachine.transition_job(
                    session=session,
                    job=job,
                    target_state=ExecutionLifecycle.RUNNING,
                    actor_type="agent",
                    actor_id=str(agent_id or job.agent_id or "agent"),
                    reason="Auto-advance to running before completion",
                )
                norm_curr_state = ExecutionLifecycle.RUNNING

            try:
                await ExecutionStateMachine.transition_job(
                    session=session,
                    job=job,
                    target_state=norm_target_state,
                    actor_type="agent",
                    actor_id=str(agent_id or job.agent_id or "agent"),
                    reason=update.error_message,
                    payload_extra={
                        "progress": update.progress,
                        "processed_rows": update.processed_rows,
                        "successful_rows": update.successful_rows,
                        "failed_rows": update.failed_rows,
                        "current_table": update.current_table,
                        "current_stage": update.current_stage,
                    },
                )
            except InvalidStateTransitionError as err:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=str(err),
                )

        now = datetime.now(timezone.utc)
        job.progress = update.progress
        if update.total_rows > 0:
            job.total_rows = update.total_rows
        if update.processed_rows > 0 or job.processed_rows is None:
            job.processed_rows = update.processed_rows
        if update.successful_rows > 0 or job.successful_rows is None:
            job.successful_rows = update.successful_rows
        if update.failed_rows > 0 or job.failed_rows is None:
            job.failed_rows = update.failed_rows
        if update.current_table:
            job.current_table = update.current_table
        if update.current_stage:
            job.current_stage = update.current_stage
        if update.error_message:
            job.error_message = update.error_message
        elif norm_target_state == ExecutionLifecycle.COMPLETED:
            job.error_message = None

        # Preserve explicit dry_run_completed status
        if update.status == "dry_run_completed" or (job.is_dry_run and norm_target_state == ExecutionLifecycle.COMPLETED):
            job.status = "dry_run_completed"

        # Refresh agent last_seen_at, status, and idle_since to prevent heartbeat starvation during ETL execution
        if job.agent_id:
            stmt_agent = select(Agent).where(Agent.id == job.agent_id)
            res_agent = await session.execute(stmt_agent)
            agent_obj = res_agent.scalar_one_or_none()
            if agent_obj:
                agent_obj.last_seen_at = now
                if norm_target_state == ExecutionLifecycle.RUNNING:
                    agent_obj.status = "busy"
                    agent_obj.idle_since = None   # Actively running — clear idle marker
                elif norm_target_state == ExecutionLifecycle.COMPLETED:
                    agent_obj.status = "online"
                    agent_obj.idle_since = now    # Job done — start idle tracking for Option C/A
                elif norm_target_state == ExecutionLifecycle.FAILED:
                    agent_obj.status = "error"
                    agent_obj.last_error = f"Migration job '{job.id}' failed: {update.error_message or 'Fatal execution failure.'}"
                    agent_obj.error_category = "JOB_EXECUTION_FAILURE"
                    agent_obj.last_error_at = now
                    agent_obj.idle_since = now

        await session.commit()
        await session.refresh(job)

        # Auto-trigger AI diagnosis in background with fresh session (Fix #1 & #7)
        if update.status == "failed" and job.error_message and not job.ai_diagnosis:
            asyncio.create_task(_run_diagnosis_background(job.id))

        # Broadcast progress via WebSocket
        if job.agent_id:
            await manager.broadcast_to_agent(
                str(job.agent_id),
                {
                    "event_type": "EXECUTION_PROGRESS",
                    "data": {
                        "job_id": str(job.id),
                        "status": job.status,
                        "progress": job.progress,
                        "processed_rows": job.processed_rows,
                        "successful_rows": job.successful_rows,
                        "failed_rows": job.failed_rows,
                        "current_table": job.current_table,
                        "current_stage": job.current_stage,
                        "ai_diagnosis": job.ai_diagnosis,
                    },
                },
            )

        return job

    @staticmethod
    async def list_jobs_for_plan(
        session: AsyncSession, plan_id: uuid.UUID, user_id: uuid.UUID
    ) -> List[MigrationJob]:
        """Fetch all execution jobs for a migration plan owned by user, ordered by newest first (Fix #8)."""
        stmt = (
            select(MigrationJob)
            .join(MigrationPlan, MigrationJob.migration_plan_id == MigrationPlan.id)
            .where(
                MigrationJob.migration_plan_id == plan_id,
                MigrationPlan.user_id == user_id,
            )
            .order_by(MigrationJob.created_at.desc())
        )
        res = await session.execute(stmt)
        return list(res.scalars().all())

    @staticmethod
    async def diagnose_job_failure(
        session: AsyncSession, job_id: uuid.UUID
    ) -> MigrationJob:
        """Synthesizes raw job error traceback into plain-English diagnosis & remediation actions using LLM."""
        # Atomic lock & guard: only process if ai_diagnosis is NULL (Fix #2)
        stmt = (
            select(MigrationJob)
            .where(MigrationJob.id == job_id)
            .with_for_update(skip_locked=True)
            .options(
                selectinload(MigrationJob.agent).selectinload(Agent.data_sources),
                selectinload(MigrationJob.plan),
            )
        )
        res = await session.execute(stmt)
        job = res.scalar_one_or_none()
        if not job:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Execution job '{job_id}' not found.",
            )

        # Early guard: AI diagnosis only available for failed jobs (Fix #6)
        if job.status != "failed":
            if job.ai_diagnosis:
                return job
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"AI diagnosis is only available for failed execution jobs. Current status is '{job.status}'.",
            )

        # Return existing diagnosis if already populated (idempotent)
        if job.ai_diagnosis:
            return job

        err_msg = job.error_message or "Unknown execution error."
        stage = job.current_stage or "ETL processing"
        tbl = job.current_table or "N/A"

        # Heuristic & LLM Failure Synthesizer
        diag_summary = f"Migration failed during '{stage}' stage on table '{tbl}'."
        category = "UNKNOWN_ERROR"
        user_env_issue = False
        fix_steps: List[str] = []
        copyable_cmd: Optional[str] = None

        err_lower = err_msg.lower()

        # Strict Network Error Detection Patterns & Port Regex (Fix #4)
        network_patterns = [
            "connection refused",
            "connectionrefused",
            "could not connect to server",
            "could not connect to host",
            "no route to host",
            "network is unreachable",
            "connection timed out",
            "name or service not known",
            "getaddrinfofailed",
            "failed to connect",
        ]
        port_re = re.compile(r':(5\d{3}|27017|3306|5432|3307)\b')
        is_network_err = any(p in err_lower for p in network_patterns) or bool(port_re.search(err_msg))

        if is_network_err:
            category = "NETWORK_CONNECTION_FAILED"
            user_env_issue = True
            diag_summary = f"The agent could not connect to database on host/port specified during stage '{stage}'."
            fix_steps = [
                "Verify database host and port mapping (e.g. use host.docker.internal:5435 instead of localhost:5432).",
                "Ensure database container is running and accepting incoming connections.",
                "Verify --add-host=host.docker.internal:host-gateway flag is included in docker run."
            ]
        elif "notnullviolation" in err_lower or "null value in column" in err_lower:
            category = "NOT_NULL_CONSTRAINT_VIOLATION"
            user_env_issue = False
            diag_summary = f"Target table '{tbl}' rejected insertion due to a NULL primary key or NOT NULL column."
            fix_steps = [
                "Re-run the updated agent container image (data-migration-agent:latest) which auto-populates UUID primary keys.",
                "Or edit the transformation blueprint in UI to explicitly map source columns to all NOT NULL target columns."
            ]
        elif "nosuchmoduleerror" in err_lower or "sqlalchemy.dialects:mongodb" in err_lower:
            category = "NOSQL_DIALECT_MISMATCH"
            user_env_issue = False
            diag_summary = "Relational DDL statement execution was attempted on a MongoDB document database."
            fix_steps = [
                "Re-run the updated agent container image (data-migration-agent:latest) which skips relational DDL for MongoDB.",
                "MongoDB target collections are created automatically upon document streaming."
            ]
        elif "access denied" in err_lower or "authentication failed" in err_lower or "password" in err_lower:
            category = "DATABASE_AUTHENTICATION_FAILED"
            user_env_issue = True
            diag_summary = "Database rejected authentication credentials provided in agent environment variables."
            fix_steps = [
                "Verify database usernames and passwords in container environment variables.",
                "Ensure password placeholders like <SRC_SRC_DB_1_PASSWORD> are replaced with valid credentials."
            ]
        elif "undefinedtable" in err_lower or ("relation" in err_lower and "does not exist" in err_lower):
            category = "TARGET_TABLE_MISSING"
            user_env_issue = False
            diag_summary = f"Target table '{tbl}' does not exist in target database because pre-migration DDL table creation failed or was skipped."
            fix_steps = [
                "Verify target DDL statements use valid SQL syntax for the target database engine (e.g. gen_random_uuid() on PostgreSQL).",
                "Ensure pre-migration DDL statements execute without errors before starting data streaming.",
                "Re-run migration with the updated agent image (data-migration-agent:latest) which auto-heals DDL function compatibility."
            ]
        elif "undefinedfunction" in err_lower or "function uuid_v4() does not exist" in err_lower or "function does not exist" in err_lower:
            category = "SQL_DIALECT_FUNCTION_ERROR"
            user_env_issue = False
            diag_summary = f"Pre-migration DDL referenced an unsupported SQL function (e.g. uuid_v4() on PostgreSQL)."
            fix_steps = [
                "Use native PostgreSQL gen_random_uuid() or uuid_generate_v4() instead of uuid_v4().",
                "Update agent container to auto-heal dialect functions and re-run the execution job."
            ]
        elif "error rate exceeded" in err_lower:
            category = "HIGH_ROW_ERROR_RATE"
            user_env_issue = False
            diag_summary = f"ETL pipeline aborted for table '{tbl}' because over 50% of records failed insertion."
            fix_steps = [
                "Check target database schema constraints, data types, and primary key definitions.",
                "Review agent logs for underlying database driver notices and errors (e.g. missing target table or column type mismatch).",
                "Ensure target tables are created prior to streaming and re-run the job."
            ]
        elif "cannot cast 'object' type" in err_lower or "computerror: cannot cast" in err_lower:
            category = "SCHEMA_POLARS_TYPE_ERROR"
            user_env_issue = False
            diag_summary = f"Polars columnar transformation encountered unconverted Python Object/UUID columns in table '{tbl}'."
            fix_steps = [
                "Ensure source database driver columns (UUID/JSON) are sanitized to string before Polars cast operations.",
                "Re-run migration using updated Docker Agent image (data-migration-agent:latest)."
            ]
        else:
            diag_summary = f"ETL pipeline encountered an unexpected error during stage '{stage}': {err_msg[:200]}"
            fix_steps = [
                "Review target database constraints and column mapping specifications.",
                "Verify source data quality and re-run execution job."
            ]

        # Build complete copyable docker run command with ALL registered data sources (Fix #3)
        if job.agent:
            ag = job.agent
            cmd_lines = [
                "docker run -d \\",
                f"  --name {ag.agent_identifier or 'data-agent'} \\",
                "  --restart unless-stopped \\",
                "  --add-host=host.docker.internal:host-gateway \\",
                '  -e BACKEND_URL="http://host.docker.internal:8000" \\',
                f'  -e AGENT_TOKEN="AG_TOKEN_PLACEHOLDER" \\',
                f'  -e AGENT_ID="{ag.id}" \\',
                f'  -e AGENT_IDENTIFIER="{ag.agent_identifier}" \\',
            ]
            if ag.version:
                cmd_lines.append(f'  -e AGENT_VERSION="{ag.version}" \\')

            if ag.data_sources:
                src_i = 1
                dst_i = 1
                for ds in ag.data_sources:
                    role_str = (ds.role or "").lower().strip()
                    ds_type = (ds.type or "postgresql").lower().strip()
                    if role_str in ("source", "src"):
                        cmd_lines.append(f'  -e SRC_SRC_DB_{src_i}_TYPE="{ds_type}" \\')
                        cmd_lines.append(f'  -e SRC_SRC_DB_{src_i}_URL="{ds_type}://user:<SRC_SRC_DB_{src_i}_PASSWORD>@host.docker.internal:port/{ds.identifier}" \\')
                        src_i += 1
                    elif role_str in ("destination", "target", "dest"):
                        cmd_lines.append(f'  -e DEST_DST_DB_{dst_i}_TYPE="{ds_type}" \\')
                        cmd_lines.append(f'  -e DEST_DST_DB_{dst_i}_URL="{ds_type}://user:<DEST_DST_DB_{dst_i}_PASSWORD>@host.docker.internal:port/{ds.identifier}" \\')
                        cmd_lines.append(f'  -e DEST_DB_URL="{ds_type}://user:<DEST_DST_DB_{dst_i}_PASSWORD>@host.docker.internal:port/{ds.identifier}" \\')
                        dst_i += 1

            cmd_lines.append(f"  {settings.AGENT_DOCKER_IMAGE}")
            copyable_cmd = "\n".join(cmd_lines)

        job.ai_diagnosis = {
            "summary": diag_summary,
            "root_cause_category": category,
            "is_user_environment_issue": user_env_issue,
            "fix_steps": fix_steps,
            "copyable_fix_command": copyable_cmd,
            "raw_error_snippet": err_msg[:500],
        }

        await session.commit()
        await session.refresh(job)
        return job

    @staticmethod
    async def list_runs_for_job(
        session: AsyncSession, job_id: uuid.UUID, user_id: uuid.UUID
    ) -> List[AgentRun]:
        """Fetch all AgentRun attempts for a MigrationJob owned by the user."""
        stmt = (
            select(AgentRun)
            .join(MigrationJob, AgentRun.migration_job_id == MigrationJob.id)
            .join(MigrationPlan, MigrationJob.migration_plan_id == MigrationPlan.id)
            .where(AgentRun.migration_job_id == job_id, MigrationPlan.user_id == user_id)
            .order_by(AgentRun.created_at.asc())
        )
        res = await session.execute(stmt)
        return list(res.scalars().all())

    @staticmethod
    async def list_events_for_job(
        session: AsyncSession, job_id: uuid.UUID, user_id: uuid.UUID
    ) -> List[ExecutionEvent]:
        """Fetch all append-only ExecutionEvents for a MigrationJob owned by the user."""
        stmt = (
            select(ExecutionEvent)
            .join(MigrationJob, ExecutionEvent.migration_job_id == MigrationJob.id)
            .join(MigrationPlan, MigrationJob.migration_plan_id == MigrationPlan.id)
            .where(ExecutionEvent.migration_job_id == job_id, MigrationPlan.user_id == user_id)
            .order_by(ExecutionEvent.timestamp.asc())
        )
        res = await session.execute(stmt)
        return list(res.scalars().all())

    @staticmethod
    async def get_execution_plan_for_job(
        session: AsyncSession, job_id: uuid.UUID, user_id: Optional[uuid.UUID] = None
    ) -> Optional[MigrationExecutionPlan]:
        """Fetch the derived MigrationExecutionPlan with steps and checkpoints for a job."""
        stmt = (
            select(MigrationExecutionPlan)
            .options(
                selectinload(MigrationExecutionPlan.steps).selectinload(
                    MigrationExecutionStep.checkpoints
                )
            )
            .join(MigrationJob, MigrationExecutionPlan.migration_job_id == MigrationJob.id)
        )
        if user_id:
            stmt = stmt.join(MigrationPlan, MigrationJob.migration_plan_id == MigrationPlan.id).where(
                MigrationPlan.user_id == user_id
            )
        stmt = stmt.where(MigrationExecutionPlan.migration_job_id == job_id)
        res = await session.execute(stmt)
        return res.scalar_one_or_none()

    @staticmethod
    async def claim_next_step(
        session: AsyncSession,
        execution_plan_id: uuid.UUID,
        agent_id: uuid.UUID,
        agent_run_id: uuid.UUID,
    ) -> Optional[MigrationExecutionStep]:
        """Claims the next available step for an active agent run."""
        step = await ExecutionPlanService.claim_next_step(
            session=session,
            execution_plan_id=execution_plan_id,
            agent_id=agent_id,
            agent_run_id=agent_run_id,
        )
        if step:
            await session.commit()
            await session.refresh(step)
        return step

    @staticmethod
    async def save_step_checkpoint(
        session: AsyncSession,
        step_id: uuid.UUID,
        source_identifier: str,
        source_table: str,
        target_table: str,
        cursor_offset: int,
        rows_processed: int,
        source_position: Optional[dict] = None,
    ) -> ExecutionCheckpoint:
        """Persists an authoritative checkpoint to PostgreSQL/SQLite."""
        checkpoint = await ExecutionPlanService.save_checkpoint(
            session=session,
            step_id=step_id,
            source_identifier=source_identifier,
            source_table=source_table,
            target_table=target_table,
            cursor_offset=cursor_offset,
            rows_processed=rows_processed,
            source_position=source_position,
        )
        await session.commit()
        await session.refresh(checkpoint)
        return checkpoint

    @staticmethod
    async def get_step_checkpoints(
        session: AsyncSession, step_id: uuid.UUID
    ) -> List[ExecutionCheckpoint]:
        """Fetches all checkpoints for an execution step."""
        return await ExecutionPlanService.get_checkpoints_for_step(session, step_id)

    @staticmethod
    async def complete_step(
        session: AsyncSession,
        step_id: uuid.UUID,
        output_summary: Optional[dict] = None,
    ) -> MigrationExecutionStep:
        """Marks a step completed and checks for whole plan completion."""
        step = await ExecutionPlanService.complete_step(
            session=session, step_id=step_id, output_summary=output_summary
        )
        await session.commit()
        await session.refresh(step)
        return step

    @staticmethod
    async def fail_step(
        session: AsyncSession,
        step_id: uuid.UUID,
        error_type: Optional[str] = None,
        error_message: Optional[str] = None,
    ) -> MigrationExecutionStep:
        """Handles step failure and applies retry policy."""
        step = await ExecutionPlanService.fail_step(
            session=session,
            step_id=step_id,
            error_type=error_type,
            error_message=error_message or "Execution step failed.",
        )
        await session.commit()
        await session.refresh(step)
        return step

    @staticmethod
    async def list_user_interventions(
        session: AsyncSession,
        job_id: uuid.UUID,
    ) -> List[Any]:
        """Lists pending and resolved user interventions for a migration job."""
        return await ExecutionPlanService.list_user_interventions(session, job_id)

    @staticmethod
    async def resolve_user_intervention(
        session: AsyncSession,
        intervention_id: uuid.UUID,
        user_id: uuid.UUID,
        action: str,
        response_data: Optional[Dict[str, Any]] = None,
    ) -> Any:
        """Resolves an ASK_USER intervention and resumes or redirects execution."""
        return await ExecutionPlanService.resolve_user_intervention(
            session=session,
            intervention_id=intervention_id,
            user_id=user_id,
            action=action,
            response_data=response_data,
        )


async def _run_diagnosis_background(job_id: uuid.UUID):
    """Background coroutine running AI failure diagnosis with fresh AsyncSession (Fix #1)."""
    async with AsyncSessionLocal() as fresh_session:
        try:
            await ExecutionService.diagnose_job_failure(fresh_session, job_id)
        except Exception as diag_err:
            logger.warning(f"Background AI failure diagnosis failed for job '{job_id}': {diag_err}")
