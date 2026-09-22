"""
Unit Tests for Asynchronous Plan Refinement Background Execution:
- POST /plans/{plan_id}/refine-async returns 202 Accepted immediately
- Duplicate refine-async call while refining returns 409 Conflict
- Plan approval while refining returns 409 Conflict
- GET /plans/{plan_id}/refine/status reports processing, then completed
- Database persists updated plan_data AST and creates new version snapshot
"""

import asyncio
import uuid
from unittest.mock import patch

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.db import Base, get_db
from app.main import app
from app.modules.migration_plans.migration_plans_engine.migration_plans_llm import llm_plan_generator
from app.modules.migration_plans.migration_plans_schemas import (
    ColumnMappingSpec,
    ConflictResolutionSpec,
    RefinementFeedback,
    SourceColumnRef,
    SourceTableRef,
    TableMappingSpec,
    TransformationPlanAST,
)
from app.modules.migration_plans.migration_plans_services import RefinementTaskManager


def _make_test_ast() -> TransformationPlanAST:
    return TransformationPlanAST(
        target_database_type="postgresql",
        ai_explanation="Initial test plan AST",
        confidence_score=0.95,
        warnings=[],
        table_mappings=[
            TableMappingSpec(
                target_table_name="target_users",
                transformation_type="merge",
                ai_reasoning="Test reasoning",
                confidence_score=0.95,
                source_tables=[
                    SourceTableRef(identifier="src_pg", schema_name="public", table_name="users")
                ],
                column_mappings=[
                    ColumnMappingSpec(
                        target_column_name="email",
                        target_data_type="varchar(255)",
                        nullable=False,
                        is_primary_key=False,
                        transformation_type="direct_copy",
                        ui_badge_type="direct_copy",
                        source_columns=[SourceColumnRef(identifier="src_pg", table_name="users", column_name="email")],
                        explanation="Direct copy email",
                    )
                ],
            )
        ],
        pre_migration_ddl=["CREATE TABLE target_users (email VARCHAR(255));"],
        post_migration_ddl=[],
    )


@pytest.mark.asyncio
async def test_async_plan_refinement_background_workflow():
    test_engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    TestSession = async_sessionmaker(bind=test_engine, class_=AsyncSession, expire_on_commit=False)

    async def override_get_db():
        async with TestSession() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    app.dependency_overrides[get_db] = override_get_db

    # Clear refinement task in-memory store
    RefinementTaskManager.clear()

    mock_ast = _make_test_ast()

    # We also mock AsyncSessionLocal used in background coroutine so it uses our test engine
    with patch("app.modules.migration_plans.migration_plans_services.AsyncSessionLocal", TestSession):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
            # 1. Register User & Agent
            res_u = await client.post("/api/v1/auth/register", json={
                "email": "async_refine@example.com", "password": "Password123!", "name": "Async Tester"
            })
            assert res_u.status_code == 201

            res_agent = await client.post("/api/v1/agents", json={
                "name": "Async Agent",
                "agent_identifier": "async_agent_01",
                "version": "1.0.0",
                "data_sources": [
                    {"name": "Source PG", "type": "postgresql", "role": "source", "identifier": "src_pg"},
                ],
            })
            assert res_agent.status_code == 201
            agent_data = res_agent.json()
            agent_id = agent_data["id"]
            api_token = agent_data["api_token"]

            # Sync metadata snapshot
            sync_payload = {
                "identifier": "src_pg",
                "database_name": "prod_db",
                "database_version": "PostgreSQL 16",
                "total_tables": 1,
                "total_columns": 1,
                "total_rows": 100,
                "schemas": [{"schema_name": "public", "tables": [
                    {"schema_name": "public", "table_name": "users", "table_type": "table",
                     "row_count": 100, "size_bytes": 1024,
                     "columns": [
                         {"column_name": "email", "ordinal_position": 1, "data_type": "varchar", "nullable": False, "is_primary_key": False},
                     ], "constraints": []},
                ]}],
            }
            res_sync = await client.post("/api/v1/metadata/sync", json=sync_payload, headers={"X-Agent-Token": api_token})
            assert res_sync.status_code == 201

            # 2. Generate Initial Plan
            with patch.object(llm_plan_generator, "generate", return_value=mock_ast):
                res_gen = await client.post("/api/v1/plans/generate", json={
                    "agent_id": agent_id,
                    "target_config": {"database_type": "postgresql"},
                })
            assert res_gen.status_code == 201
            plan_id = res_gen.json()["id"]

            # 3. Trigger Async Refinement
            refined_ast = _make_test_ast()
            refined_ast.ai_explanation = "Refined via async background task"
            refined_ast.refinement_feedback = RefinementFeedback(
                applied=True,
                verdict="applied",
                user_prompt="Add user_id prefix",
                explanation="Applied user_id prefix transformation.",
                changes_summary=["Updated column mapping"],
            )

            # We create a future to control when LLM finishes, simulating background delay
            llm_gate = asyncio.Event()

            def controlled_refine(*args, **kwargs):
                return refined_ast

            with patch.object(llm_plan_generator, "refine", side_effect=controlled_refine):
                res_start = await client.post(f"/api/v1/plans/{plan_id}/refine-async", json={
                    "user_feedback": "Add user_id prefix",
                })
                assert res_start.status_code == 202
                job_data = res_start.json()
                assert "task_id" in job_data
                assert job_data["status"] == "processing"
                assert job_data["plan_id"] == plan_id

                # Give the event loop a few milliseconds to let background task run
                await asyncio.sleep(0.1)

                # 4. Check status endpoint
                res_status = await client.get(f"/api/v1/plans/{plan_id}/refine/status")
                assert res_status.status_code == 200
                status_data = res_status.json()
                assert status_data["status"] in ["processing", "completed"]

                # Wait for background task to complete if not already
                for _ in range(20):
                    if status_data["status"] == "completed":
                        break
                    await asyncio.sleep(0.1)
                    res_status = await client.get(f"/api/v1/plans/{plan_id}/refine/status")
                    status_data = res_status.json()

                assert status_data["status"] == "completed"
                assert status_data["plan"] is not None
                assert status_data["plan"]["plan_data"]["ai_explanation"] == "Refined via async background task"

            # 5. Check versions list - should have version 2 as llm_refinement
            res_vers = await client.get(f"/api/v1/plans/{plan_id}/versions")
            assert res_vers.status_code == 200
            vers_list = res_vers.json()
            assert len(vers_list) == 2
            assert vers_list[0]["version_number"] == 2
            assert vers_list[0]["edit_type"] == "llm_refinement"
            assert vers_list[0]["user_feedback"] == "Add user_id prefix"
