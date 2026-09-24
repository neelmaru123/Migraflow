"""
Execution State Machine Service
Enforces legal state transitions, manages AgentRun identities, and appends durable ExecutionEvents.
"""

from datetime import datetime, timezone
from typing import Any, Dict, Optional, Set, Union
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import logger
from app.core.state import (
    ExecutionEventType,
    ExecutionLifecycle,
    InvalidStateTransitionError,
)
from app.modules.execution.execution_models import AgentRun, ExecutionEvent, MigrationJob


class ExecutionStateMachine:
    """
    Authoritative state machine governing MigrationJob and AgentRun lifecycle transitions.
    Rejects illegal state transitions with InvalidStateTransitionError and emits durable
    append-only ExecutionEvents on every valid transition.
    """

    # Central transition graph
    LEGAL_TRANSITIONS: Dict[ExecutionLifecycle, Set[ExecutionLifecycle]] = {
        ExecutionLifecycle.QUEUED: {
            ExecutionLifecycle.CLAIMED,
            ExecutionLifecycle.PREPARING,
            ExecutionLifecycle.CANCELLED,
            ExecutionLifecycle.FAILED,
        },
        ExecutionLifecycle.CLAIMED: {
            ExecutionLifecycle.PREPARING,
            ExecutionLifecycle.RUNNING,
            ExecutionLifecycle.CANCELLED,
            ExecutionLifecycle.FAILED,
        },
        ExecutionLifecycle.PREPARING: {
            ExecutionLifecycle.RUNNING,
            ExecutionLifecycle.VERIFYING,
            ExecutionLifecycle.CANCELLED,
            ExecutionLifecycle.FAILED,
        },
        ExecutionLifecycle.RUNNING: {
            ExecutionLifecycle.PAUSED,
            ExecutionLifecycle.RECOVERING,
            ExecutionLifecycle.VERIFYING,
            ExecutionLifecycle.COMPLETED,
            ExecutionLifecycle.FAILED,
            ExecutionLifecycle.CANCELLED,
        },
        ExecutionLifecycle.PAUSED: {
            ExecutionLifecycle.RUNNING,
            ExecutionLifecycle.CANCELLED,
            ExecutionLifecycle.FAILED,
        },
        ExecutionLifecycle.RECOVERING: {
            ExecutionLifecycle.RUNNING,
            ExecutionLifecycle.FAILED,
            ExecutionLifecycle.CANCELLED,
        },
        ExecutionLifecycle.VERIFYING: {
            ExecutionLifecycle.COMPLETED,
            ExecutionLifecycle.FAILED,
            ExecutionLifecycle.RECOVERING,
            ExecutionLifecycle.CANCELLED,
        },
        ExecutionLifecycle.COMPLETED: set(),  # Terminal state
        ExecutionLifecycle.FAILED: {
            ExecutionLifecycle.RECOVERING,   # Recovery/retry can resurrect failed jobs into recovering
        },
        ExecutionLifecycle.CANCELLED: set(),  # Terminal state
    }

    # Backward compatibility mapping for legacy status strings
    LEGACY_STATUS_MAP: Dict[str, ExecutionLifecycle] = {
        "dry_run_completed": ExecutionLifecycle.COMPLETED,
        "ddl_executing": ExecutionLifecycle.PREPARING,
        "data_streaming": ExecutionLifecycle.RUNNING,
    }

    @classmethod
    def normalize_state(cls, state: Union[str, ExecutionLifecycle]) -> ExecutionLifecycle:
        """Coerces any string or enum to normalized ExecutionLifecycle."""
        if isinstance(state, ExecutionLifecycle):
            return state
        raw = str(state).strip().lower()
        if raw in cls.LEGACY_STATUS_MAP:
            return cls.LEGACY_STATUS_MAP[raw]
        return ExecutionLifecycle.from_str(raw)

    @classmethod
    def validate_transition(
        cls,
        current_state: Union[str, ExecutionLifecycle],
        target_state: Union[str, ExecutionLifecycle],
    ) -> bool:
        """
        Validates if transition from current_state to target_state is legal.
        Raises InvalidStateTransitionError if illegal.
        Self-transitions (current == target) are considered valid (idempotent no-op).
        """
        curr = cls.normalize_state(current_state)
        tgt = cls.normalize_state(target_state)

        if curr == tgt:
            return True

        allowed = cls.LEGAL_TRANSITIONS.get(curr, set())
        if tgt not in allowed:
            raise InvalidStateTransitionError(
                from_state=curr.value,
                to_state=tgt.value,
                entity_type="MigrationJob",
                reason=f"Permitted next states from '{curr.value}': {[s.value for s in allowed]}",
            )
        return True

    @classmethod
    def determine_event_type(
        cls,
        from_state: ExecutionLifecycle,
        to_state: ExecutionLifecycle,
    ) -> ExecutionEventType:
        """Maps lifecycle transition to corresponding ExecutionEventType."""
        if to_state == ExecutionLifecycle.CLAIMED:
            return ExecutionEventType.JOB_CLAIMED
        elif to_state == ExecutionLifecycle.PREPARING:
            return ExecutionEventType.JOB_STARTED
        elif to_state == ExecutionLifecycle.RUNNING:
            if from_state == ExecutionLifecycle.PAUSED:
                return ExecutionEventType.JOB_RESUMED
            return ExecutionEventType.JOB_STARTED
        elif to_state == ExecutionLifecycle.PAUSED:
            return ExecutionEventType.JOB_PAUSED
        elif to_state == ExecutionLifecycle.RECOVERING:
            return ExecutionEventType.RECOVERY_STARTED
        elif to_state == ExecutionLifecycle.VERIFYING:
            return ExecutionEventType.VERIFICATION_STARTED
        elif to_state == ExecutionLifecycle.COMPLETED:
            return ExecutionEventType.JOB_COMPLETED
        elif to_state == ExecutionLifecycle.FAILED:
            return ExecutionEventType.JOB_FAILED
        elif to_state == ExecutionLifecycle.CANCELLED:
            return ExecutionEventType.JOB_CANCELLED
        return ExecutionEventType.JOB_STARTED

    @classmethod
    async def create_agent_run(
        cls,
        session: AsyncSession,
        job: MigrationJob,
        agent_id: Optional[uuid.UUID] = None,
        agent_version: Optional[str] = None,
        execution_engine_version: Optional[str] = None,
        status: Union[str, ExecutionLifecycle] = ExecutionLifecycle.PREPARING,
        actor_type: str = "agent",
        actor_id: Optional[str] = None,
    ) -> AgentRun:
        """
        Instantiates a new AgentRun attempt for a MigrationJob.
        Sets job.current_run_id and records an append-only ExecutionEvent.
        """
        now = datetime.now(timezone.utc)
        norm_status = cls.normalize_state(status)

        run = AgentRun(
            id=uuid.uuid4(),
            migration_job_id=job.id,
            agent_id=agent_id or job.agent_id,
            status=norm_status.value,
            started_at=now,
            agent_version=agent_version,
            execution_engine_version=execution_engine_version,
            created_at=now,
            updated_at=now,
        )
        session.add(run)
        job.current_run_id = run.id

        # Record event
        event_type = (
            ExecutionEventType.JOB_CLAIMED
            if norm_status == ExecutionLifecycle.CLAIMED
            else ExecutionEventType.JOB_STARTED
        )
        event = ExecutionEvent(
            id=uuid.uuid4(),
            event_id=uuid.uuid4(),
            agent_run_id=run.id,
            migration_job_id=job.id,
            migration_plan_id=job.migration_plan_id,
            event_type=event_type.value,
            actor_type=actor_type,
            actor_id=actor_id or (str(agent_id) if agent_id else "system"),
            timestamp=now,
            payload={
                "action": "agent_run_created",
                "agent_run_id": str(run.id),
                "agent_id": str(agent_id or job.agent_id),
                "status": norm_status.value,
                "agent_version": agent_version,
                "execution_engine_version": execution_engine_version,
            },
            schema_version=1,
        )
        session.add(event)

        logger.info(
            f"Created AgentRun '{run.id}' for job '{job.id}' (Agent: {agent_id or job.agent_id}, status: {norm_status.value})"
        )
        return run

    @classmethod
    async def transition_job(
        cls,
        session: AsyncSession,
        job: MigrationJob,
        target_state: Union[str, ExecutionLifecycle],
        actor_type: str = "system",
        actor_id: Optional[str] = None,
        reason: Optional[str] = None,
        payload_extra: Optional[Dict[str, Any]] = None,
    ) -> ExecutionEvent:
        """
        Executes a validated state transition for a MigrationJob and its active AgentRun.
        Updates timestamps, status fields, and writes a durable ExecutionEvent.
        """
        from_state = cls.normalize_state(job.status)
        to_state = cls.normalize_state(target_state)

        # Validate transition legality
        cls.validate_transition(from_state, to_state)

        now = datetime.now(timezone.utc)
        job.status = to_state.value
        job.updated_at = now

        # Manage temporal properties
        if to_state == ExecutionLifecycle.RUNNING and job.started_at is None:
            job.started_at = now
        elif to_state in (
            ExecutionLifecycle.COMPLETED,
            ExecutionLifecycle.FAILED,
            ExecutionLifecycle.CANCELLED,
        ):
            job.completed_at = now

        if to_state == ExecutionLifecycle.CANCELLED:
            job.current_stage = "cancelled"
            if reason:
                job.error_message = reason
        elif to_state == ExecutionLifecycle.FAILED and reason:
            job.error_message = reason

        # Synchronize active AgentRun if bound
        current_run: Optional[AgentRun] = None
        if job.current_run_id:
            stmt_run = select(AgentRun).where(AgentRun.id == job.current_run_id)
            res_run = await session.execute(stmt_run)
            current_run = res_run.scalar_one_or_none()

            if current_run:
                current_run.status = to_state.value
                current_run.updated_at = now
                if to_state in (
                    ExecutionLifecycle.COMPLETED,
                    ExecutionLifecycle.FAILED,
                    ExecutionLifecycle.CANCELLED,
                ):
                    current_run.finished_at = now
                if to_state == ExecutionLifecycle.FAILED and reason:
                    current_run.failure_reason = reason
                elif to_state == ExecutionLifecycle.CANCELLED and reason:
                    current_run.failure_reason = reason

        # Construct append-only ExecutionEvent
        event_type = cls.determine_event_type(from_state, to_state)
        payload = {
            "from_state": from_state.value,
            "to_state": to_state.value,
            "job_id": str(job.id),
            "current_run_id": str(job.current_run_id) if job.current_run_id else None,
            "reason": reason,
        }
        if payload_extra:
            payload.update(payload_extra)

        event = ExecutionEvent(
            id=uuid.uuid4(),
            event_id=uuid.uuid4(),
            agent_run_id=job.current_run_id,
            migration_job_id=job.id,
            migration_plan_id=job.migration_plan_id,
            event_type=event_type.value,
            actor_type=actor_type,
            actor_id=actor_id,
            timestamp=now,
            payload=payload,
            schema_version=1,
        )
        session.add(event)

        logger.info(
            f"State transition: Job '{job.id}' transitioned from '{from_state.value}' "
            f"to '{to_state.value}' by {actor_type}:{actor_id}. Event: {event_type.value}"
        )
        return event
