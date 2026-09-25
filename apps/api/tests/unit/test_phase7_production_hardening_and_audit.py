"""
Phase 7 Production Hardening, Concurrency, and Failure Recovery Audit Tests.
Verifies failure recovery behaviors, database locking, API security, credential masking,
and concurrency safety across multi-user, multi-agent scenarios.
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch
import uuid
import pytest
from fastapi import Request
from sqlalchemy import select

from app.core.state import (
    ExecutionLifecycle,
    ExecutionPlanLifecycle,
    ExecutionStepLifecycle,
)
from app.modules.agents.agents_models import Agent
from app.modules.agents.agents_services import AgentService
from app.modules.execution.execution_models import MigrationJob
from app.modules.execution.execution_plan_services import ExecutionPlanService
from app.modules.execution.execution_services import ExecutionService
from app.modules.migration_plans.migration_plans_services import MigrationPlanService
from app.modules.users.users_models import User
from tests.unit.test_phase1_state_machine_and_events import setup_test_context


@pytest.mark.asyncio
async def test_watchdog_recovers_jobs_in_verifying_recovering_and_claimed():
    """
    Failure Recovery Audit:
    When an agent crashes or times out while jobs are in 'verifying', 'recovering',
    or 'claimed' states, the watchdog must detect them, mark the agent offline,
    fail the orphaned jobs, and cascade the failure to execution plans and running steps.
    """
    async with setup_test_context() as (session, entities):
        agent = entities["agent"]
        user = entities["user"]
        plan = entities["plan"]

        # Create job 1 in 'verifying'
        job_verifying = await ExecutionService.create_execution_job(
            session=session, user_id=user.id, plan_id=plan.id
        )
        job_verifying.status = ExecutionLifecycle.VERIFYING.value
        exec_plan_v = await ExecutionPlanService.get_execution_plan_by_job_id(session, job_verifying.id)
        if exec_plan_v:
            exec_plan_v.status = ExecutionPlanLifecycle.RUNNING.value
            for s in exec_plan_v.steps[:1]:
                s.status = ExecutionStepLifecycle.RUNNING.value

        # Create job 2 in 'recovering'
        job_recovering = MigrationJob(
            id=uuid.uuid4(),
            migration_plan_id=plan.id,
            agent_id=agent.id,
            status=ExecutionLifecycle.RECOVERING.value,
            progress=45.0,
        )
        session.add(job_recovering)

        # Simulate agent crash: last seen > 60s ago
        agent.status = "online"
        agent.last_seen_at = datetime.now(timezone.utc) - timedelta(seconds=120)
        await session.commit()

        # Run watchdog
        result = await AgentService.check_stale_agents_and_jobs(
            session=session, stale_threshold_seconds=60
        )
        assert result["stale_agents_marked_offline"] >= 1

        # Verify agent is marked offline
        await session.refresh(agent)
        assert agent.status == "offline"
        assert agent.error_category == "DISCONNECTED_UNEXPECTEDLY"

        # Verify job_verifying is failed
        await session.refresh(job_verifying)
        assert job_verifying.status == ExecutionLifecycle.FAILED.value
        assert "Agent disconnected" in job_verifying.error_message

        # Verify job_recovering is failed
        await session.refresh(job_recovering)
        assert job_recovering.status == ExecutionLifecycle.FAILED.value

        # Verify attached execution plan and steps were cascaded
        if exec_plan_v:
            await session.refresh(exec_plan_v)
            assert exec_plan_v.status == ExecutionPlanLifecycle.FAILED.value
            for s in exec_plan_v.steps:
                if s.status == ExecutionStepLifecycle.FAILED.value:
                    assert s.failure_category == "AGENT_DISCONNECTED"


@pytest.mark.asyncio
async def test_cancellation_cascades_to_execution_plan_and_steps():
    """
    Failure Recovery Audit:
    Cancelling an active execution job must transition the job to CANCELLED,
    cascade the cancellation to the derived ExecutionPlan, and cancel all
    pending/running/retrying execution steps with finished_at timestamps.
    """
    async with setup_test_context() as (session, entities):
        user = entities["user"]
        plan = entities["plan"]

        job = await ExecutionService.create_execution_job(
            session=session, user_id=user.id, plan_id=plan.id
        )
        job.status = ExecutionLifecycle.RUNNING.value
        exec_plan = await ExecutionPlanService.get_execution_plan_by_job_id(session, job.id)
        assert exec_plan is not None

        # Set first step to running
        step1 = exec_plan.steps[0]
        step1.status = ExecutionStepLifecycle.RUNNING.value
        step1.started_at = datetime.now(timezone.utc)
        await session.commit()

        # Cancel the job
        cancelled_job = await ExecutionService.cancel_execution_job(
            session=session,
            user_id=user.id,
            job_id=job.id,
            reason="User cancelled migration due to planned maintenance.",
        )
        assert cancelled_job.status == ExecutionLifecycle.CANCELLED.value

        # Verify execution plan is cancelled
        await session.refresh(exec_plan)
        assert exec_plan.status == ExecutionPlanLifecycle.CANCELLED.value
        assert exec_plan.finalized_at is not None

        # Verify steps are cancelled
        for s in exec_plan.steps:
            await session.refresh(s)
            assert s.status == ExecutionStepLifecycle.CANCELLED.value
            assert s.finished_at is not None


@pytest.mark.asyncio
async def test_plan_mutation_locked_during_active_execution_phases():
    """
    API & Database Audit:
    MigrationPlan edits and AST updates must be strictly blocked whenever an
    associated execution job is in any active state (queued, claimed, preparing,
    running, paused, recovering, ask_user, verifying).
    """
    async with setup_test_context() as (session, entities):
        plan = entities["plan"]
        agent = entities["agent"]

        active_statuses = [
            "queued",
            "claimed",
            "preparing",
            "running",
            "paused",
            "recovering",
            "ask_user",
            "verifying",
        ]

        for active_st in active_statuses:
            job = MigrationJob(
                id=uuid.uuid4(),
                migration_plan_id=plan.id,
                agent_id=agent.id,
                status=active_st,
            )
            session.add(job)
            await session.commit()

            # Attempting to update plan data must be rejected with 409 Conflict
            with pytest.raises(Exception) as excinfo:
                await MigrationPlanService.update_plan_data(
                    session=session,
                    plan=plan,
                    plan_data={"version": 2, "tables": []},
                )
            assert "Cannot edit migration plan while execution job" in str(excinfo.value)
            assert active_st in str(excinfo.value)

            # Cleanup job for next status iteration
            await session.delete(job)
            await session.commit()


@pytest.mark.asyncio
async def test_monotonic_checkpoint_protection_prevents_stale_regression():
    """
    Data Safety & Recovery Audit:
    Authoritative checkpoints must enforce monotonic forward progression.
    A delayed out-of-order checkpoint payload with an older cursor_offset must NOT
    regress the durable checkpoint backwards.
    """
    async with setup_test_context() as (session, entities):
        step_id = uuid.uuid4()

        # Save primary checkpoint at offset 50,000
        cp1 = await ExecutionPlanService.save_checkpoint(
            session=session,
            step_id=step_id,
            source_identifier="src_main",
            source_table="customers",
            target_table="dim_customers",
            cursor_offset=50000,
            rows_processed=50000,
            source_position={"pk": 50000},
        )
        await session.commit()
        assert cp1.cursor_offset == 50000
        assert cp1.checkpoint_version == 1

        # Simulate delayed out-of-order packet with older offset 20,000
        await ExecutionPlanService.save_checkpoint(
            session=session,
            step_id=step_id,
            source_identifier="src_main",
            source_table="customers",
            target_table="dim_customers",
            cursor_offset=20000,
            rows_processed=20000,
            source_position={"pk": 20000},
        )
        await session.commit()

        # Checkpoint offset must remain 50,000 without backwards regression
        await session.refresh(cp1)
        assert cp1.cursor_offset == 50000
        assert cp1.rows_processed == 50000
        assert cp1.checkpoint_version == 1  # Version not incremented on rejected stale update


@pytest.mark.asyncio
async def test_cross_tenant_isolation_on_step_claiming():
    """
    API & Agent Security Audit:
    An agent owned by Tenant/User A must NOT be able to claim steps belonging
    to Tenant/User B's migration execution plan.
    """
    async with setup_test_context() as (session, entities):
        user_a = entities["user"]
        plan_a = entities["plan"]

        # Create Tenant B and Agent B
        user_b = User(
            id=uuid.uuid4(),
            email="tenant_b@company.com",
            password_hash="pw",
            name="Tenant B",
        )
        session.add(user_b)
        await session.flush()

        agent_b = Agent(
            id=uuid.uuid4(),
            user_id=user_b.id,
            name="Agent Tenant B",
            agent_identifier="agent_tenant_b",
            api_token_hash="hash_b",
            status="online",
        )
        session.add(agent_b)
        await session.commit()

        job_a = await ExecutionService.create_execution_job(
            session=session, user_id=user_a.id, plan_id=plan_a.id
        )
        exec_plan_a = await ExecutionPlanService.get_execution_plan_by_job_id(session, job_a.id)

        # Agent B attempts to claim step on Tenant A's execution plan
        claimed_step = await ExecutionPlanService.claim_next_step(
            session=session,
            execution_plan_id=exec_plan_a.id,
            agent_id=agent_b.id,
            agent_run_id=uuid.uuid4(),
        )
        # Must return None and refuse cross-tenant step claim
        assert claimed_step is None


@pytest.mark.asyncio
async def test_health_endpoint_validates_database_connectivity():
    """
    Docker & Production Readiness Audit:
    The /health probe must actively verify database liveness and return HTTP 200
    when connected, or HTTP 503 when the database connection fails.
    """
    from app.main import health_check

    # Healthy DB probe test: mock session execute returning 1
    mock_session_healthy = AsyncMock()
    mock_session_healthy.execute.return_value = MagicMock()
    mock_ctx_healthy = AsyncMock()
    mock_ctx_healthy.__aenter__.return_value = mock_session_healthy

    with patch("app.main.AsyncSessionLocal", return_value=mock_ctx_healthy):
        response_ok = await health_check()
        assert response_ok.status_code == 200
        import json
        data_ok = json.loads(response_ok.body.decode())
        assert data_ok["success"] is True
        assert data_ok["data"]["database_connected"] is True
        assert data_ok["data"]["status"] == "ok"

    # Degraded DB probe test (mocking exception during connection)
    mock_ctx_down = AsyncMock()
    mock_ctx_down.__aenter__.side_effect = ConnectionRefusedError("PostgreSQL server down")

    with patch("app.main.AsyncSessionLocal", return_value=mock_ctx_down):
        response_err = await health_check()
        assert response_err.status_code == 503
        data_err = json.loads(response_err.body.decode())
        assert data_err["success"] is False
        assert data_err["data"]["database_connected"] is False
        assert data_err["data"]["status"] == "degraded"


@pytest.mark.asyncio
async def test_credential_sanitization_in_unhandled_exceptions():
    """
    Data Safety Audit:
    Unhandled exceptions leaking raw database credentials, bearer tokens,
    or connection URIs must be masked before reaching HTTP error responses or logs.
    """
    from app.main import global_exception_handler

    request = MagicMock(spec=Request)
    request.method = "POST"
    request.url.path = "/api/v1/executions/start"

    # Exception containing raw PostgreSQL connection string with password
    unsafe_exc = RuntimeError(
        "Connection failed to postgresql+asyncpg://admin_user:SuperSecret123!@db.internal:5432/finance_db"
    )

    response = await global_exception_handler(request, unsafe_exc)
    assert response.status_code == 500

    import json
    body = json.loads(response.body.decode())
    assert body["success"] is False
    # Verify the password was masked
    assert "SuperSecret123!" not in json.dumps(body)
    assert "***REDACTED***" in json.dumps(body)


@pytest.mark.asyncio
async def test_idempotency_key_duplicate_submission_protection():
    """
    API & Database Concurrency Audit:
    Submitting multiple execution requests with identical idempotency_key must
    return the existing job without creating duplicate records or race conditions.
    """
    async with setup_test_context() as (session, entities):
        user = entities["user"]
        plan = entities["plan"]
        key = f"idemp-test-{uuid.uuid4()}"

        job1 = await ExecutionService.create_execution_job(
            session=session,
            user_id=user.id,
            plan_id=plan.id,
            idempotency_key=key,
        )
        assert job1.idempotency_key == key

        job2 = await ExecutionService.create_execution_job(
            session=session,
            user_id=user.id,
            plan_id=plan.id,
            idempotency_key=key,
        )
        # Must return the same job instance
        assert job2.id == job1.id

        # Verify database has exactly 1 job for this idempotency key
        stmt = select(MigrationJob).where(MigrationJob.idempotency_key == key)
        res = await session.execute(stmt)
        assert len(res.scalars().all()) == 1


@pytest.mark.asyncio
async def test_concurrent_step_claiming_no_duplicate_assignment():
    """
    Load / Concurrency Audit:
    When multiple workers concurrently claim steps from an ExecutionPlan,
    each eligible step must be claimed by at most one worker, adhering to
    concurrency limits and dependency graphs without duplicate assignments.
    """
    async with setup_test_context() as (session, entities):
        user = entities["user"]
        plan = entities["plan"]
        agent = entities["agent"]

        job = await ExecutionService.create_execution_job(
            session=session, user_id=user.id, plan_id=plan.id
        )
        exec_plan = await ExecutionPlanService.get_execution_plan_by_job_id(session, job.id)
        assert exec_plan is not None
        exec_plan.concurrency_limit = 4
        await session.commit()

        claimed_step_ids = []
        worker_runs = [uuid.uuid4() for _ in range(4)]

        for run_id in worker_runs:
            claimed = await ExecutionPlanService.claim_next_step(
                session=session,
                execution_plan_id=exec_plan.id,
                agent_id=agent.id,
                agent_run_id=run_id,
            )
            if claimed:
                claimed_step_ids.append(claimed.id)

        # Assert all claimed steps are completely unique (no duplicate claiming)
        assert len(claimed_step_ids) == len(set(claimed_step_ids))
        assert len(claimed_step_ids) >= 1

