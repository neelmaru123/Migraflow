"""
Unit tests for Execution Domain REST API endpoints
"""

import pytest
from httpx import ASGITransport, AsyncClient
from app.main import app


@pytest.mark.asyncio
async def test_execution_api_lifecycle():
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
    from app.core.db import Base, get_db

    test_engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    TestSession = async_sessionmaker(bind=test_engine, class_=AsyncSession, expire_on_commit=False)

    async def override_get_db():
        async with TestSession() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            # 1. Test polling tasks endpoint with agent token header (unregistered token returns 401/404)
            agent_headers = {"X-Agent-Token": "test_agent_raw_token"}
            resp_tasks = await client.get("/api/v1/agents/tasks", headers=agent_headers)
            assert resp_tasks.status_code in [401, 404]

            # 2. Test execution listing endpoint without auth (returns 401)
            resp_list = await client.get("/api/v1/executions")
            assert resp_list.status_code == 401
    finally:
        app.dependency_overrides.pop(get_db, None)


def test_truncate_target_schema_and_task_serialization():
    from app.modules.execution.execution_schemas import (
        ExecutionStartRequest,
        AgentTaskItemResponse,
        ExecutionJobResponse,
    )
    import uuid
    from datetime import datetime, timezone

    # Test ExecutionStartRequest defaults to False, accepts True
    req_default = ExecutionStartRequest()
    assert req_default.truncate_target is False

    req_custom = ExecutionStartRequest(truncate_target=True)
    assert req_custom.truncate_target is True

    # Test AgentTaskItemResponse serializes truncate_target
    task_id = uuid.uuid4()
    plan_id = uuid.uuid4()
    task = AgentTaskItemResponse(
        job_id=task_id,
        migration_plan_id=plan_id,
        status="queued",
        is_dry_run=False,
        truncate_target=True,
        created_at=datetime.now(timezone.utc),
    )
    task_dict = task.model_dump(mode="json")
    assert task_dict["truncate_target"] is True

    # Test ExecutionJobResponse serializes truncate_target
    job_resp = ExecutionJobResponse(
        id=task_id,
        migration_plan_id=plan_id,
        status="queued",
        is_dry_run=False,
        truncate_target=True,
        progress=0.0,
        total_rows=0,
        processed_rows=0,
        successful_rows=0,
        failed_rows=0,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    job_dict = job_resp.model_dump(mode="json")
    assert job_dict["truncate_target"] is True


def test_cancel_execution_schema():
    from app.modules.execution.execution_schemas import (
        ExecutionCancelRequest,
        ExecutionJobResponse,
    )
    import uuid
    from datetime import datetime, timezone

    # Test ExecutionCancelRequest default reason None
    req_default = ExecutionCancelRequest()
    assert req_default.reason is None

    req_custom = ExecutionCancelRequest(reason="User stopped Docker container")
    assert req_custom.reason == "User stopped Docker container"

    # Test ExecutionJobResponse serializes cancelled status
    job_resp = ExecutionJobResponse(
        id=uuid.uuid4(),
        migration_plan_id=uuid.uuid4(),
        status="cancelled",
        is_dry_run=True,
        truncate_target=False,
        progress=25.0,
        total_rows=1000,
        processed_rows=250,
        successful_rows=250,
        failed_rows=0,
        current_stage="cancelled",
        error_message="Execution cancelled by user.",
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    job_dict = job_resp.model_dump(mode="json")
    assert job_dict["status"] == "cancelled"
    assert job_dict["current_stage"] == "cancelled"
    assert job_dict["error_message"] == "Execution cancelled by user."


@pytest.mark.asyncio
async def test_cancel_execution_service(tmp_path):
    """
    Verifies that ExecutionService.cancel_execution_job:
    1. Successfully cancels an active ('running' / 'queued') job.
    2. Resets assigned agent from 'busy' to 'online' and updates idle_since.
    3. Rejects cancelling already completed/failed/cancelled jobs with 400.
    """
    import uuid
    from datetime import datetime, timezone
    from fastapi import HTTPException
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
    from app.core.db import Base
    from app.modules.agents.agents_models import Agent
    from app.modules.execution.execution_models import MigrationJob
    from app.modules.execution.execution_services import ExecutionService
    from app.modules.migration_plans.migration_plans_models import MigrationPlan
    from app.modules.users.users_models import User

    db_file = tmp_path / "test_cancel.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_file}", echo=False)
    async_session = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    user_id = uuid.uuid4()
    agent_id = uuid.uuid4()
    plan_id = uuid.uuid4()
    running_job_id = uuid.uuid4()
    completed_job_id = uuid.uuid4()

    async with async_session() as session:
        user = User(id=user_id, email="cancel_user@example.com", password_hash="pw", name="Cancel User")
        agent = Agent(
            id=agent_id,
            user_id=user_id,
            name="Cancel Agent",
            agent_identifier="agent_cancel",
            api_token_hash="hash_cancel",
            status="busy",
        )
        plan = MigrationPlan(id=plan_id, user_id=user_id, agent_id=agent_id, plan_data={}, status="approved", is_valid=True)
        running_job = MigrationJob(
            id=running_job_id,
            migration_plan_id=plan_id,
            agent_id=agent_id,
            status="running",
            progress=30.0,
            current_stage="data_streaming",
        )
        completed_job = MigrationJob(
            id=completed_job_id,
            migration_plan_id=plan_id,
            agent_id=agent_id,
            status="completed",
            progress=100.0,
            current_stage="completed",
        )

        session.add_all([user, agent, plan, running_job, completed_job])
        await session.commit()

    # 1. Cancel the running job
    async with async_session() as session:
        cancelled_job = await ExecutionService.cancel_execution_job(
            session=session,
            user_id=user_id,
            job_id=running_job_id,
            reason="Container stopped manually",
        )
        assert cancelled_job.status == "cancelled"
        assert cancelled_job.current_stage == "cancelled"
        assert cancelled_job.error_message == "Container stopped manually"
        assert cancelled_job.completed_at is not None

    # Verify agent status was reset to 'online'
    async with async_session() as session:
        agent_refreshed = await session.get(Agent, agent_id)
        assert agent_refreshed.status == "online"
        assert agent_refreshed.idle_since is not None

    # 2. Attempt to cancel already completed job (should raise 400 Bad Request)
    async with async_session() as session:
        with pytest.raises(HTTPException) as exc_info:
            await ExecutionService.cancel_execution_job(
                session=session,
                user_id=user_id,
                job_id=completed_job_id,
            )
        assert exc_info.value.status_code == 400
        assert "Cannot cancel execution job" in exc_info.value.detail

    await engine.dispose()

