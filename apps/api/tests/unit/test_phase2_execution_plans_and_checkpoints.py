"""
Unit Tests for Phase 2: Durable Execution Plan, Steps, Checkpointing, and Reassignment
"""

from contextlib import asynccontextmanager
import uuid
from datetime import datetime, timezone, timedelta
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.db import Base
from app.core.state import (
    ExecutionEventType,
    ExecutionLifecycle,
    ExecutionPlanLifecycle,
    ExecutionStepLifecycle,
    ExecutionStepType,
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
from app.modules.execution.execution_services import ExecutionService
from app.modules.execution.retry_policy import RetryPolicy
from app.modules.migration_plans.migration_plans_models import MigrationPlan
from app.modules.users.users_models import User


@asynccontextmanager
async def setup_test_context():
    """Provides an isolated SQLite in-memory AsyncSession and test seed data."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
    async with session_factory() as session:
        user_id = uuid.uuid4()
        agent_id = uuid.uuid4()
        plan_id = uuid.uuid4()

        user = User(
            id=user_id,
            email="test_user@example.com",
            password_hash="hash",
            name="Tester",
        )
        agent = Agent(
            id=agent_id,
            user_id=user_id,
            name="Docker Agent 1",
            agent_identifier="agent_1",
            api_token_hash="tok1",
            status="online",
            last_seen_at=datetime.now(timezone.utc),
        )
        plan_data = {
            "pre_migration_ddl": ["CREATE TABLE users (id INT);", "CREATE TABLE orders (id INT);"],
            "table_mappings": [
                {
                    "target_table_name": "users",
                    "source_tables": [{"identifier": "src_1", "table_name": "users_src"}],
                    "column_mappings": [{"target_column_name": "id"}],
                },
                {
                    "target_table_name": "orders",
                    "source_tables": [{"identifier": "src_1", "table_name": "orders_src"}],
                    "column_mappings": [{"target_column_name": "id"}],
                },
            ],
            "post_migration_ddl": ["CREATE INDEX idx_orders ON orders (id);"],
        }
        plan = MigrationPlan(
            id=plan_id,
            user_id=user_id,
            agent_id=agent_id,
            status="approved",
            is_valid=True,
            plan_data=plan_data,
            target_config={"database_type": "postgresql"},
        )
        session.add_all([user, agent, plan])
        await session.commit()

        yield {
            "session": session,
            "session_factory": session_factory,
            "user_id": user_id,
            "agent_id": agent_id,
            "plan_id": plan_id,
            "user": user,
            "agent": agent,
            "plan": plan,
        }

    await engine.dispose()


@pytest.mark.asyncio
async def test_execution_plan_and_dag_step_creation():
    """
    Verifies that creating a MigrationJob automatically derives a MigrationExecutionPlan
    and the ordered DAG of steps (preflight -> pre_ddl -> load:users, load:orders -> post_ddl -> verify).
    """
    async with setup_test_context() as ctx:
        session = ctx["session"]
        job = await ExecutionService.create_execution_job(
            session=session,
            user_id=ctx["user_id"],
            plan_id=ctx["plan_id"],
        )

        exec_plan = await ExecutionPlanService.get_execution_plan_by_job_id(session, job.id)
        assert exec_plan is not None
        assert exec_plan.migration_job_id == job.id
        assert exec_plan.status == ExecutionPlanLifecycle.PENDING.value
        assert exec_plan.concurrency_limit == 2

        # Verify DAG step ordering & dependencies
        steps = exec_plan.steps
        assert len(steps) == 6  # preflight, pre_ddl, load:users, load:orders, post_ddl, verify
        step_map = {s.step_key: s for s in steps}

        assert "preflight" in step_map
        assert step_map["preflight"].dependencies == []
        assert step_map["preflight"].sequence == 1

        assert "pre_ddl" in step_map
        assert step_map["pre_ddl"].dependencies == ["preflight"]
        assert step_map["pre_ddl"].sequence == 2

        assert "load:users" in step_map
        assert step_map["load:users"].dependencies == ["pre_ddl"]

        assert "load:orders" in step_map
        assert step_map["load:orders"].dependencies == ["pre_ddl"]

        assert "post_ddl" in step_map
        assert set(step_map["post_ddl"].dependencies) == {"load:users", "load:orders"}

        assert "verify" in step_map
        assert step_map["verify"].dependencies == ["post_ddl"]


@pytest.mark.asyncio
async def test_step_claiming_with_dependencies_and_concurrency_limit():
    """
    Verifies that step claiming respects DAG dependencies and enforces the plan's concurrency limit.
    """
    async with setup_test_context() as ctx:
        session = ctx["session"]
        job = await ExecutionService.create_execution_job(
            session=session,
            user_id=ctx["user_id"],
            plan_id=ctx["plan_id"],
        )
        exec_plan = await ExecutionPlanService.get_execution_plan_by_job_id(session, job.id)
        run_id = uuid.uuid4()

        # 1. First claimable step is preflight
        step1 = await ExecutionPlanService.claim_next_step(
            session=session,
            execution_plan_id=exec_plan.id,
            agent_id=ctx["agent_id"],
            agent_run_id=run_id,
        )
        assert step1 is not None
        assert step1.step_key == "preflight"
        assert step1.status == ExecutionStepLifecycle.RUNNING.value
        assert step1.attempt_count == 1

        # 2. Cannot claim pre_ddl while preflight is still RUNNING
        step2_attempt = await ExecutionPlanService.claim_next_step(
            session=session,
            execution_plan_id=exec_plan.id,
            agent_id=ctx["agent_id"],
            agent_run_id=run_id,
        )
        assert step2_attempt is None

        # 3. Complete preflight -> now pre_ddl is claimable
        await ExecutionPlanService.complete_step(session, step1.id)
        step_pre_ddl = await ExecutionPlanService.claim_next_step(
            session=session,
            execution_plan_id=exec_plan.id,
            agent_id=ctx["agent_id"],
            agent_run_id=run_id,
        )
        assert step_pre_ddl is not None
        assert step_pre_ddl.step_key == "pre_ddl"

        # 4. Complete pre_ddl -> both load:users and load:orders become eligible
        await ExecutionPlanService.complete_step(session, step_pre_ddl.id)

        # Claim table 1 (running count = 1 < 2)
        claim_tbl1 = await ExecutionPlanService.claim_next_step(
            session=session,
            execution_plan_id=exec_plan.id,
            agent_id=ctx["agent_id"],
            agent_run_id=run_id,
        )
        assert claim_tbl1 is not None
        assert claim_tbl1.step_key in ("load:users", "load:orders")

        # Claim table 2 (running count = 2 == concurrency limit)
        claim_tbl2 = await ExecutionPlanService.claim_next_step(
            session=session,
            execution_plan_id=exec_plan.id,
            agent_id=ctx["agent_id"],
            agent_run_id=run_id,
        )
        assert claim_tbl2 is not None

        # Attempt to claim another step when limit reached (2/2 running)
        claim_blocked = await ExecutionPlanService.claim_next_step(
            session=session,
            execution_plan_id=exec_plan.id,
            agent_id=ctx["agent_id"],
            agent_run_id=run_id,
        )
        assert claim_blocked is None


@pytest.mark.asyncio
async def test_authoritative_checkpoint_persistence_and_versioning():
    """
    Verifies that authoritative database checkpoints are correctly inserted, updated,
    version-incremented, and queryable.
    """
    async with setup_test_context() as ctx:
        session = ctx["session"]
        job = await ExecutionService.create_execution_job(
            session=session,
            user_id=ctx["user_id"],
            plan_id=ctx["plan_id"],
        )
        exec_plan = await ExecutionPlanService.get_execution_plan_by_job_id(session, job.id)
        step = exec_plan.steps[0]

        # 1. Initial Checkpoint save
        cp1 = await ExecutionPlanService.save_checkpoint(
            session=session,
            step_id=step.id,
            source_identifier="src_1",
            source_table="users_src",
            target_table="users",
            cursor_offset=50000,
            rows_processed=50000,
            source_position={"last_pk": 50000},
        )
        assert cp1.cursor_offset == 50000
        assert cp1.checkpoint_version == 1

        # 2. Update Checkpoint for next batch
        cp2 = await ExecutionPlanService.save_checkpoint(
            session=session,
            step_id=step.id,
            source_identifier="src_1",
            source_table="users_src",
            target_table="users",
            cursor_offset=100000,
            rows_processed=100000,
            source_position={"last_pk": 100000},
        )
        assert cp2.cursor_offset == 100000
        assert cp2.rows_processed == 100000
        assert cp2.checkpoint_version == 2
        assert cp2.id == cp1.id  # Same checkpoint entity updated

        # 3. Retrieve Checkpoints for Step
        checkpoints = await ExecutionPlanService.get_checkpoints_for_step(session, step.id)
        assert len(checkpoints) == 1
        assert checkpoints[0].cursor_offset == 100000


@pytest.mark.asyncio
async def test_agent_crash_simulation_and_step_reassignment_with_checkpoint_resume():
    """
    CRITICAL ARCHITECTURAL TEST:
    Agent A claims step -> saves checkpoint at row 50,000 -> Agent A crashes/disappears
    -> Watchdog recovers stale step -> Agent B claims step -> Agent B resumes from checkpoint at row 50,000.
    """
    async with setup_test_context() as ctx:
        session = ctx["session"]

        agent_b = Agent(
            id=uuid.uuid4(),
            user_id=ctx["user_id"],
            name="Docker Agent 2 (Failover)",
            agent_identifier="agent_2",
            api_token_hash="tok2",
            status="online",
            last_seen_at=datetime.now(timezone.utc),
        )
        session.add(agent_b)
        await session.commit()

        job = await ExecutionService.create_execution_job(
            session=session,
            user_id=ctx["user_id"],
            plan_id=ctx["plan_id"],
        )
        exec_plan = await ExecutionPlanService.get_execution_plan_by_job_id(session, job.id)

        run_a = uuid.uuid4()
        run_b = uuid.uuid4()

        # Step 1: Agent A claims step
        step = await ExecutionPlanService.claim_next_step(
            session=session,
            execution_plan_id=exec_plan.id,
            agent_id=ctx["agent_id"],
            agent_run_id=run_a,
        )
        assert step.agent_run_id == run_a
        assert step.attempt_count == 1

        # Step 2: Agent A saves checkpoint at offset 50,000
        await ExecutionPlanService.save_checkpoint(
            session=session,
            step_id=step.id,
            source_identifier="src_1",
            source_table="users_src",
            target_table="users",
            cursor_offset=50000,
            rows_processed=50000,
            source_position={"last_pk": 50000},
        )

        # Step 3: Simulate Agent A crashing & updated_at aging > 300s
        step.updated_at = datetime.now(timezone.utc) - timedelta(seconds=400)
        await session.commit()

        # Step 4: Watchdog runs stale recovery
        recovered = await ExecutionPlanService.recover_stale_steps(session, stale_threshold_seconds=300)
        assert recovered == 1
        assert step.status == ExecutionStepLifecycle.RETRYING.value
        assert step.error_type == "AGENT_TIMEOUT"

        # Step 5: Agent B claims the recovered step
        reassigned_step = await ExecutionPlanService.claim_next_step(
            session=session,
            execution_plan_id=exec_plan.id,
            agent_id=agent_b.id,
            agent_run_id=run_b,
        )
        assert reassigned_step is not None
        assert reassigned_step.id == step.id
        assert reassigned_step.agent_run_id == run_b
        assert reassigned_step.attempt_count == 2
        assert reassigned_step.status == ExecutionStepLifecycle.RUNNING.value

        # Step 6: Agent B queries checkpoints and resumes from row 50,000
        checkpoints = await ExecutionPlanService.get_checkpoints_for_step(session, reassigned_step.id)
        assert len(checkpoints) == 1
        assert checkpoints[0].cursor_offset == 50000
        assert checkpoints[0].rows_processed == 50000

        # Step 7: Agent B completes the remainder of the step
        completed_step = await ExecutionPlanService.complete_step(
            session=session,
            step_id=reassigned_step.id,
            output_summary={"total_rows": 100000, "resumed_from": 50000},
        )
        assert completed_step.status == ExecutionStepLifecycle.COMPLETED.value


def test_retry_policy_classification_and_backoff():
    """
    Verifies RetryPolicy error classification, attempt threshold enforcement, and backoff delay.
    """
    policy = RetryPolicy(max_attempts=3, initial_delay=1.0, backoff_factor=2.0, jitter=False)

    # 1. Retryable vs Non-retryable
    assert policy.is_retryable_error("NETWORK_TIMEOUT") is True
    assert policy.is_retryable_error("CONNECTION_RESET") is True
    assert policy.is_retryable_error("SYNTAX_ERROR") is False
    assert policy.is_retryable_error("SCHEMA_MISMATCH") is False

    # 2. Attempt threshold
    assert policy.should_retry(current_attempt=1, error_type="NETWORK_TIMEOUT") is True
    assert policy.should_retry(current_attempt=2, error_type="NETWORK_TIMEOUT") is True
    assert policy.should_retry(current_attempt=3, error_type="NETWORK_TIMEOUT") is False
    assert policy.should_retry(current_attempt=1, error_type="SYNTAX_ERROR") is False

    # 3. Exponential Backoff Calculation
    assert policy.compute_backoff_delay(attempt=1) == 1.0
    assert policy.compute_backoff_delay(attempt=2) == 2.0
    assert policy.compute_backoff_delay(attempt=3) == 4.0


@pytest.mark.asyncio
async def test_step_failure_and_plan_cascade():
    """
    Verifies that a permanent non-retryable step failure marks the step, execution plan,
    and parent MigrationJob as FAILED.
    """
    async with setup_test_context() as ctx:
        session = ctx["session"]
        job = await ExecutionService.create_execution_job(
            session=session,
            user_id=ctx["user_id"],
            plan_id=ctx["plan_id"],
        )
        exec_plan = await ExecutionPlanService.get_execution_plan_by_job_id(session, job.id)
        step = exec_plan.steps[0]

        # Fail step with non-retryable error
        failed_step = await ExecutionPlanService.fail_step(
            session=session,
            step_id=step.id,
            error_type="SYNTAX_ERROR",
            error_message="Invalid SQL dialect syntax.",
        )
        assert failed_step.status == ExecutionStepLifecycle.FAILED.value
        assert exec_plan.status == ExecutionPlanLifecycle.FAILED.value
        assert job.status == ExecutionLifecycle.FAILED.value


@pytest.mark.asyncio
async def test_step_completion_and_whole_plan_completion():
    """
    Verifies that completing all steps in an execution plan auto-completes the execution plan
    and parent MigrationJob.
    """
    async with setup_test_context() as ctx:
        session = ctx["session"]
        job = await ExecutionService.create_execution_job(
            session=session,
            user_id=ctx["user_id"],
            plan_id=ctx["plan_id"],
        )
        exec_plan = await ExecutionPlanService.get_execution_plan_by_job_id(session, job.id)

        for step in exec_plan.steps:
            await ExecutionPlanService.complete_step(session, step.id)

        assert exec_plan.status == ExecutionPlanLifecycle.COMPLETED.value
        assert job.status == ExecutionLifecycle.COMPLETED.value
        assert job.progress == 100.0


@pytest.mark.asyncio
async def test_phase2_rest_endpoints():
    """
    Verifies Phase 2 REST API endpoints:
    - GET /executions/{id}/plan
    - POST /executions/plans/{plan_id}/steps/claim
    - POST /executions/steps/{step_id}/checkpoint
    - GET /executions/steps/{step_id}/checkpoints
    - POST /executions/steps/{step_id}/complete
    - POST /executions/steps/{step_id}/fail
    """
    from httpx import ASGITransport, AsyncClient
    from app.main import app
    from app.core.db import get_db
    from app.modules.users.users_routes import get_current_active_user
    from app.modules.agents.agents_dependencies import get_current_agent

    async with setup_test_context() as ctx:
        session = ctx["session"]
        user = ctx["user"]
        agent = ctx["agent"]

        job = await ExecutionService.create_execution_job(
            session=session,
            user_id=ctx["user_id"],
            plan_id=ctx["plan_id"],
        )
        exec_plan = await ExecutionPlanService.get_execution_plan_by_job_id(session, job.id)

        app.dependency_overrides[get_db] = lambda: session
        app.dependency_overrides[get_current_active_user] = lambda: user
        app.dependency_overrides[get_current_agent] = lambda: agent

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://testserver"
        ) as client:
            # 1. GET /executions/{id}/plan
            resp_plan = await client.get(f"/api/v1/executions/{job.id}/plan")
            assert resp_plan.status_code == 200
            plan_json = resp_plan.json()
            assert plan_json["id"] == str(exec_plan.id)
            assert len(plan_json["steps"]) == 6

            step1_id = plan_json["steps"][0]["id"]
            run_id = uuid.uuid4()

            # 2. POST /executions/plans/{plan_id}/steps/claim
            resp_claim = await client.post(
                f"/api/v1/executions/plans/{exec_plan.id}/steps/claim",
                json={"agent_id": str(agent.id), "agent_run_id": str(run_id)},
            )
            assert resp_claim.status_code == 200
            claim_json = resp_claim.json()
            assert claim_json["step_key"] == "preflight"
            assert claim_json["status"] == "running"

            # 3. POST /executions/steps/{step_id}/checkpoint
            resp_cp = await client.post(
                f"/api/v1/executions/steps/{step1_id}/checkpoint",
                json={
                    "source_identifier": "src_1",
                    "source_table": "users_src",
                    "target_table": "users",
                    "cursor_offset": 25000,
                    "rows_processed": 25000,
                },
            )
            assert resp_cp.status_code == 200
            cp_json = resp_cp.json()
            assert cp_json["cursor_offset"] == 25000
            assert cp_json["checkpoint_version"] == 1

            # 4. GET /executions/steps/{step_id}/checkpoints
            resp_cps = await client.get(f"/api/v1/executions/steps/{step1_id}/checkpoints")
            assert resp_cps.status_code == 200
            cps_list = resp_cps.json()
            assert len(cps_list) == 1
            assert cps_list[0]["cursor_offset"] == 25000

            # 5. POST /executions/steps/{step_id}/complete
            resp_complete = await client.post(
                f"/api/v1/executions/steps/{step1_id}/complete",
                json={"output_summary": {"status": "ok"}},
            )
            assert resp_complete.status_code == 200
            assert resp_complete.json()["status"] == "completed"

            # 6. POST /executions/steps/{step_id}/fail
            step2_id = plan_json["steps"][1]["id"]
            resp_fail = await client.post(
                f"/api/v1/executions/steps/{step2_id}/fail",
                json={"error_type": "SYNTAX_ERROR", "error_message": "Invalid SQL"},
            )
            assert resp_fail.status_code == 200
            assert resp_fail.json()["status"] == "failed"

        app.dependency_overrides.clear()
