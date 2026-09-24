"""
Unit Tests for Phase 1: Explicit State Machines, Agent Run Identities, Durable Events, and Idempotency.
"""

from contextlib import asynccontextmanager
from datetime import datetime, timezone
import uuid
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.db import Base
from app.core.state import (
    AgentLifecycle,
    ExecutionEventType,
    ExecutionLifecycle,
    ExecutionStepLifecycle,
    InvalidStateTransitionError,
    MigrationPlanLifecycle,
)
from app.modules.agents.agents_models import Agent
from app.modules.execution.execution_models import AgentRun, ExecutionEvent, MigrationJob
from app.modules.execution.execution_services import ExecutionService
from app.modules.execution.execution_state_machine import ExecutionStateMachine
from app.modules.migration_plans.migration_plans_models import MigrationPlan
from app.modules.users.users_models import User


@asynccontextmanager
async def setup_test_context():
    """Provides an isolated in-memory SQLite database session and seeded entities."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
    async with session_factory() as session:
        user = User(
            id=uuid.uuid4(),
            email="dev_phase1@example.com",
            password_hash="hash123",
            name="Phase1 Dev",
        )
        agent = Agent(
            id=uuid.uuid4(),
            user_id=user.id,
            name="Phase1 Agent",
            agent_identifier="agent_phase1",
            api_token_hash="token_hash_phase1",
            status=AgentLifecycle.ONLINE.value,
            version="1.0.0",
            last_seen_at=datetime.now(timezone.utc),
        )
        plan = MigrationPlan(
            id=uuid.uuid4(),
            user_id=user.id,
            agent_id=agent.id,
            status=MigrationPlanLifecycle.APPROVED.value,
            plan_data={"table_mappings": []},
            is_valid=True,
        )
        session.add_all([user, agent, plan])
        await session.commit()
        yield session, {"user": user, "agent": agent, "plan": plan}

    await engine.dispose()


# ---------------------------------------------------------------------------
# 1. State Machine Definitions and Case-Insensitive Normalization Tests
# ---------------------------------------------------------------------------

def test_central_state_enums_and_normalization():
    """Verify state enums, case-insensitivity, and string compatibility."""
    assert ExecutionLifecycle.QUEUED == "queued"
    assert ExecutionLifecycle.QUEUED == "QUEUED"
    assert ExecutionLifecycle.from_str("RUNNING") == ExecutionLifecycle.RUNNING
    assert ExecutionLifecycle.from_str("running") == ExecutionLifecycle.RUNNING

    assert AgentLifecycle.ONLINE == "online"
    assert AgentLifecycle.from_str("BUSY") == AgentLifecycle.BUSY

    assert MigrationPlanLifecycle.APPROVED == "approved"
    assert MigrationPlanLifecycle.from_str("DRAFT") == MigrationPlanLifecycle.DRAFT

    assert ExecutionStepLifecycle.RUNNING == "running"
    assert ExecutionStepLifecycle.from_str("COMPLETED") == ExecutionStepLifecycle.COMPLETED


# ---------------------------------------------------------------------------
# 2. Valid and Invalid State Transitions
# ---------------------------------------------------------------------------

def test_valid_state_transitions():
    """Verify all valid lifecycle transition edges."""
    assert ExecutionStateMachine.validate_transition(ExecutionLifecycle.QUEUED, ExecutionLifecycle.CLAIMED)
    assert ExecutionStateMachine.validate_transition(ExecutionLifecycle.CLAIMED, ExecutionLifecycle.PREPARING)
    assert ExecutionStateMachine.validate_transition(ExecutionLifecycle.PREPARING, ExecutionLifecycle.RUNNING)
    assert ExecutionStateMachine.validate_transition(ExecutionLifecycle.RUNNING, ExecutionLifecycle.PAUSED)
    assert ExecutionStateMachine.validate_transition(ExecutionLifecycle.PAUSED, ExecutionLifecycle.RUNNING)
    assert ExecutionStateMachine.validate_transition(ExecutionLifecycle.RUNNING, ExecutionLifecycle.RECOVERING)
    assert ExecutionStateMachine.validate_transition(ExecutionLifecycle.RECOVERING, ExecutionLifecycle.RUNNING)
    assert ExecutionStateMachine.validate_transition(ExecutionLifecycle.RUNNING, ExecutionLifecycle.VERIFYING)
    assert ExecutionStateMachine.validate_transition(ExecutionLifecycle.VERIFYING, ExecutionLifecycle.COMPLETED)
    assert ExecutionStateMachine.validate_transition(ExecutionLifecycle.RUNNING, ExecutionLifecycle.FAILED)
    assert ExecutionStateMachine.validate_transition(ExecutionLifecycle.RUNNING, ExecutionLifecycle.CANCELLED)
    assert ExecutionStateMachine.validate_transition(ExecutionLifecycle.QUEUED, ExecutionLifecycle.CANCELLED)
    # Self-transitions (idempotent updates)
    assert ExecutionStateMachine.validate_transition(ExecutionLifecycle.RUNNING, ExecutionLifecycle.RUNNING)


def test_invalid_state_transitions():
    """Verify invalid state transitions raise InvalidStateTransitionError."""
    # Terminal COMPLETED cannot transition to RUNNING or CANCELLED
    with pytest.raises(InvalidStateTransitionError) as exc_info:
        ExecutionStateMachine.validate_transition(ExecutionLifecycle.COMPLETED, ExecutionLifecycle.RUNNING)
    assert "cannot transition from 'completed' to 'running'" in str(exc_info.value)

    with pytest.raises(InvalidStateTransitionError):
        ExecutionStateMachine.validate_transition(ExecutionLifecycle.COMPLETED, ExecutionLifecycle.CANCELLED)

    # Terminal CANCELLED cannot transition back to RUNNING or COMPLETED
    with pytest.raises(InvalidStateTransitionError):
        ExecutionStateMachine.validate_transition(ExecutionLifecycle.CANCELLED, ExecutionLifecycle.RUNNING)

    # QUEUED cannot jump directly to COMPLETED
    with pytest.raises(InvalidStateTransitionError):
        ExecutionStateMachine.validate_transition(ExecutionLifecycle.QUEUED, ExecutionLifecycle.COMPLETED)

    # FAILED cannot transition directly to COMPLETED without RECOVERING
    with pytest.raises(InvalidStateTransitionError):
        ExecutionStateMachine.validate_transition(ExecutionLifecycle.FAILED, ExecutionLifecycle.COMPLETED)


# ---------------------------------------------------------------------------
# 3. Execution Events Creation, Attributes, and Append-Only History
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_event_creation_and_ordering():
    """
    Verify state transitions generate append-only ExecutionEvents with UUIDs and correct ordering.
    """
    async with setup_test_context() as (session, entities):
        plan = entities["plan"]
        user = entities["user"]
        agent = entities["agent"]

        # 1. Create job -> emits JOB_CREATED
        job = await ExecutionService.create_execution_job(
            session=session,
            user_id=user.id,
            plan_id=plan.id,
        )
        assert job.status == ExecutionLifecycle.QUEUED.value

        # 2. Agent claims job -> creates AgentRun, transitions to PREPARING
        tasks = await ExecutionService.get_pending_tasks_for_agent(
            session=session, agent_id=agent.id
        )
        assert len(tasks) == 1
        refreshed_job = tasks[0]
        assert refreshed_job.status == ExecutionLifecycle.PREPARING.value
        assert refreshed_job.current_run_id is not None

        # 3. Transition to RUNNING
        await ExecutionStateMachine.transition_job(
            session=session,
            job=refreshed_job,
            target_state=ExecutionLifecycle.RUNNING,
            actor_type="agent",
            actor_id=str(agent.id),
        )
        await session.commit()

        # 4. Transition to VERIFYING
        await ExecutionStateMachine.transition_job(
            session=session,
            job=refreshed_job,
            target_state=ExecutionLifecycle.VERIFYING,
            actor_type="agent",
            actor_id=str(agent.id),
        )
        await session.commit()

        # 5. Transition to COMPLETED
        await ExecutionStateMachine.transition_job(
            session=session,
            job=refreshed_job,
            target_state=ExecutionLifecycle.COMPLETED,
            actor_type="agent",
            actor_id=str(agent.id),
        )
        await session.commit()

        # Verify event trail
        events = await ExecutionService.list_events_for_job(
            session=session, job_id=job.id, user_id=user.id
        )
        event_types = [e.event_type for e in events]

        assert ExecutionEventType.JOB_CREATED.value in event_types
        assert ExecutionEventType.JOB_STARTED.value in event_types
        assert ExecutionEventType.VERIFICATION_STARTED.value in event_types
        assert ExecutionEventType.JOB_COMPLETED.value in event_types

        # Verify event attributes
        for e in events:
            assert isinstance(e.event_id, uuid.UUID)
            assert e.migration_job_id == job.id
            assert e.timestamp is not None
            assert e.schema_version == 1
            assert isinstance(e.payload, dict)

        # Verify chronological ordering
        timestamps = [e.timestamp for e in events]
        assert timestamps == sorted(timestamps)


# ---------------------------------------------------------------------------
# 4. Idempotency Support for Job Creation
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_idempotent_job_creation():
    """
    Verify repeated submissions with the same idempotency key return the existing job
    without creating duplicate executions.
    """
    async with setup_test_context() as (session, entities):
        plan = entities["plan"]
        user = entities["user"]
        idem_key = "idemp_test_key_12345"

        # First submission
        job1 = await ExecutionService.create_execution_job(
            session=session,
            user_id=user.id,
            plan_id=plan.id,
            idempotency_key=idem_key,
        )
        assert job1.idempotency_key == idem_key

        # Second submission with identical idempotency key
        job2 = await ExecutionService.create_execution_job(
            session=session,
            user_id=user.id,
            plan_id=plan.id,
            idempotency_key=idem_key,
        )
        assert job2.id == job1.id
        assert job2.idempotency_key == idem_key

        # Verify only 1 job exists in DB
        stmt = select(MigrationJob).where(MigrationJob.idempotency_key == idem_key)
        res = await session.execute(stmt)
        all_jobs = list(res.scalars().all())
        assert len(all_jobs) == 1


# ---------------------------------------------------------------------------
# 5. AgentRun Creation and Run Identity
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_agent_run_identity_and_fields():
    """
    Verify agent_run_id is a distinct first-class entity with required fields.
    """
    async with setup_test_context() as (session, entities):
        plan = entities["plan"]
        user = entities["user"]
        agent = entities["agent"]

        job = await ExecutionService.create_execution_job(
            session=session, user_id=user.id, plan_id=plan.id
        )

        run = await ExecutionStateMachine.create_agent_run(
            session=session,
            job=job,
            agent_id=agent.id,
            agent_version="1.2.0",
            execution_engine_version="polars-0.20",
            status=ExecutionLifecycle.PREPARING,
        )
        await session.commit()

        # Identity distinctness check
        assert run.id != job.id
        assert run.id != plan.id
        assert run.id != agent.id
        assert job.current_run_id == run.id

        # Attributes check
        assert run.migration_job_id == job.id
        assert run.agent_id == agent.id
        assert run.agent_version == "1.2.0"
        assert run.execution_engine_version == "polars-0.20"
        assert run.status == ExecutionLifecycle.PREPARING.value
        assert run.started_at is not None
        assert run.finished_at is None


# ---------------------------------------------------------------------------
# 6. Multiple Agent Runs for the Same Job (Crash / Reassignment / Recovery)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_multiple_agent_runs_for_same_job():
    """
    Verify a single migration job can have multiple AgentRuns across crash/restarts.
    """
    async with setup_test_context() as (session, entities):
        plan = entities["plan"]
        user = entities["user"]
        agent = entities["agent"]

        job = await ExecutionService.create_execution_job(
            session=session, user_id=user.id, plan_id=plan.id
        )

        # Run 1: Fails due to agent container crash
        run1 = await ExecutionStateMachine.create_agent_run(
            session=session, job=job, agent_id=agent.id, status=ExecutionLifecycle.RUNNING
        )
        await ExecutionStateMachine.transition_job(
            session=session,
            job=job,
            target_state=ExecutionLifecycle.FAILED,
            actor_type="watchdog",
            reason="Agent container disconnected unexpectedly",
        )
        await session.commit()

        assert job.status == ExecutionLifecycle.FAILED.value
        assert run1.status == ExecutionLifecycle.FAILED.value
        assert run1.finished_at is not None
        assert run1.failure_reason == "Agent container disconnected unexpectedly"

        # Job is recovered/restarted with Run 2
        await ExecutionStateMachine.transition_job(
            session=session,
            job=job,
            target_state=ExecutionLifecycle.RECOVERING,
            actor_type="system",
            reason="Restarting execution with fresh agent attempt",
        )
        run2 = await ExecutionStateMachine.create_agent_run(
            session=session, job=job, agent_id=agent.id, status=ExecutionLifecycle.RUNNING
        )
        await ExecutionStateMachine.transition_job(
            session=session,
            job=job,
            target_state=ExecutionLifecycle.RUNNING,
            actor_type="agent",
        )
        await ExecutionStateMachine.transition_job(
            session=session,
            job=job,
            target_state=ExecutionLifecycle.COMPLETED,
            actor_type="agent",
        )
        await session.commit()

        assert run2.id != run1.id
        assert job.current_run_id == run2.id
        assert run2.status == ExecutionLifecycle.COMPLETED.value
        assert run2.finished_at is not None

        # Verify both runs are preserved for this job
        runs = await ExecutionService.list_runs_for_job(
            session=session, job_id=job.id, user_id=user.id
        )
        assert len(runs) == 2
        assert runs[0].id == run1.id
        assert runs[1].id == run2.id


# ---------------------------------------------------------------------------
# 7. Cancellation State Transition
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_cancellation_state_transition():
    """
    Verify cancelling a running job transitions state, resets agent status, and records event.
    """
    async with setup_test_context() as (session, entities):
        plan = entities["plan"]
        user = entities["user"]
        agent = entities["agent"]

        job = await ExecutionService.create_execution_job(
            session=session, user_id=user.id, plan_id=plan.id
        )
        run = await ExecutionStateMachine.create_agent_run(
            session=session, job=job, agent_id=agent.id, status=ExecutionLifecycle.RUNNING
        )
        job.status = ExecutionLifecycle.RUNNING.value
        agent.status = AgentLifecycle.BUSY.value
        await session.commit()

        cancelled_job = await ExecutionService.cancel_execution_job(
            session=session,
            user_id=user.id,
            job_id=job.id,
            reason="Manual user termination",
        )
        assert cancelled_job.status == ExecutionLifecycle.CANCELLED.value
        assert cancelled_job.error_message == "Manual user termination"
        assert cancelled_job.completed_at is not None

        # Verify agent was reset to online
        refreshed_agent = await session.get(Agent, agent.id)
        assert refreshed_agent.status == AgentLifecycle.ONLINE.value

        # Verify run was marked cancelled
        refreshed_run = await session.get(AgentRun, run.id)
        assert refreshed_run.status == ExecutionLifecycle.CANCELLED.value
        assert refreshed_run.finished_at is not None

        # Verify JOB_CANCELLED event exists
        events = await ExecutionService.list_events_for_job(
            session=session, job_id=job.id, user_id=user.id
        )
        cancel_events = [e for e in events if e.event_type == ExecutionEventType.JOB_CANCELLED.value]
        assert len(cancel_events) >= 1


# ---------------------------------------------------------------------------
# 8. Recovery State Transition
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_recovery_state_transition():
    """
    Verify transition from RUNNING -> RECOVERING -> RUNNING and event emissions.
    """
    async with setup_test_context() as (session, entities):
        plan = entities["plan"]
        user = entities["user"]
        agent = entities["agent"]

        job = await ExecutionService.create_execution_job(
            session=session, user_id=user.id, plan_id=plan.id
        )
        await ExecutionStateMachine.create_agent_run(
            session=session, job=job, agent_id=agent.id, status=ExecutionLifecycle.RUNNING
        )
        job.status = ExecutionLifecycle.RUNNING.value
        await session.commit()

        # Enter RECOVERING
        ev_rec = await ExecutionStateMachine.transition_job(
            session=session,
            job=job,
            target_state=ExecutionLifecycle.RECOVERING,
            actor_type="system",
            reason="Transient DB lock timeout; attempting checkpoint replay",
        )
        assert job.status == ExecutionLifecycle.RECOVERING.value
        assert ev_rec.event_type == ExecutionEventType.RECOVERY_STARTED.value

        # Resume to RUNNING
        ev_res = await ExecutionStateMachine.transition_job(
            session=session,
            job=job,
            target_state=ExecutionLifecycle.RUNNING,
            actor_type="system",
            reason="Checkpoint verified; streaming resumed",
        )
        assert job.status == ExecutionLifecycle.RUNNING.value
        assert ev_res.event_type == ExecutionEventType.JOB_STARTED.value
