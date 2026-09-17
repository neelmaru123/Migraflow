"""
Unit Tests for Migration Plan Versioning & History Restoration:
- Initial plan generation creates version 1 (initial_ai_generation)
- Plan refinement creates version 2 (llm_refinement)
- Manual AST edits create version 3 (manual_ast_edit)
- Version history listing (metadata only, no heavy plan_data payloads)
- Version detail inspection (full plan_data AST)
- Version restoration (resets plan_data to historical snapshot & records version_restored)
- Active execution lock protection (409 Conflict when job active)
"""

import uuid
from unittest.mock import MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.db import Base, get_db
from app.main import app
from app.modules.execution.execution_models import MigrationJob
from app.modules.migration_plans.migration_plans_engine.migration_plans_llm import llm_plan_generator
from app.modules.migration_plans.migration_plans_schemas import (
    ColumnMappingSpec,
    ConflictResolutionSpec,
    SourceColumnRef,
    SourceTableRef,
    TableMappingSpec,
    TransformationPlanAST,
)


def _make_test_ast() -> TransformationPlanAST:
    return TransformationPlanAST(
        target_database_type="postgresql",
        ai_explanation="Test plan versioning AST",
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
async def test_migration_plan_versioning_and_restoration_flow():
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

    mock_ast = _make_test_ast()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        # 1. Register User & Agent with Metadata
        res_u = await client.post("/api/v1/auth/register", json={
            "email": "version_test@example.com", "password": "Password123!", "name": "Version Tester"
        })
        assert res_u.status_code == 201

        res_agent = await client.post("/api/v1/agents", json={
            "name": "Version Test Agent",
            "agent_identifier": "ver_agent_01",
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

        # 2. Generate Plan (Version 1 Created)
        with patch.object(llm_plan_generator, "generate", return_value=mock_ast):
            res_gen = await client.post("/api/v1/plans/generate", json={
                "agent_id": agent_id,
                "target_config": {"database_type": "postgresql"},
            })
        assert res_gen.status_code == 201
        plan_id = res_gen.json()["id"]

        # 3. Refine Plan (Version 2 Created)
        refined_ast = _make_test_ast()
        refined_ast.ai_explanation = "Refined AST version 2"
        from app.modules.migration_plans.migration_plans_schemas import RefinementFeedback
        refined_ast.refinement_feedback = RefinementFeedback(
            applied=False,
            verdict="infeasible_rejected",
            user_prompt="Can we do that same conversion without data loss in 12 tables",
            explanation="Consolidating into 12 tables is not feasible without data loss. The 14 distinct source tables represent separate business domains.",
            table_count_before=14,
            table_count_after=14,
            changes_summary=["All 14 domain tables retained to preserve 100% data fidelity."]
        )
        with patch.object(llm_plan_generator, "refine", return_value=refined_ast):
            res_refine = await client.post(f"/api/v1/plans/{plan_id}/refine", json={
                "user_feedback": "Can we do that same conversion without data loss in 12 tables"
            })
        assert res_refine.status_code == 200
        ref_payload = res_refine.json()["plan_data"]["refinement_feedback"]
        assert ref_payload is not None
        assert ref_payload["applied"] is False
        assert ref_payload["verdict"] == "infeasible_rejected"
        assert "12 tables is not feasible" in ref_payload["explanation"]

        # 4. Update Plan Data (Version 3 Created)
        edited_ast = refined_ast.model_dump(mode="json")
        edited_ast["ai_explanation"] = "Manual Edit version 3"
        res_edit = await client.put(f"/api/v1/plans/{plan_id}", json=edited_ast)
        assert res_edit.status_code == 200

        # 5. List Versions (GET /api/v1/plans/{plan_id}/versions)
        res_vers = await client.get(f"/api/v1/plans/{plan_id}/versions")
        assert res_vers.status_code == 200
        vers_list = res_vers.json()
        assert len(vers_list) == 3
        # Ordered DESC
        assert vers_list[0]["version_number"] == 3
        assert vers_list[0]["edit_type"] == "manual_ast_edit"
        assert vers_list[1]["version_number"] == 2
        assert vers_list[1]["edit_type"] == "llm_refinement"
        assert vers_list[1]["user_feedback"] == "Can we do that same conversion without data loss in 12 tables"
        assert vers_list[2]["version_number"] == 1
        assert vers_list[2]["edit_type"] == "initial_ai_generation"

        # 6. Fetch Version 1 Detail
        res_v1 = await client.get(f"/api/v1/plans/{plan_id}/versions/1")
        assert res_v1.status_code == 200
        v1_detail = res_v1.json()
        assert v1_detail["version_number"] == 1
        assert v1_detail["plan_data"]["ai_explanation"] == "Test plan versioning AST"

        # 7. Restore Version 1 (Version 4 Created marking version_restored)
        res_restore = await client.post(f"/api/v1/plans/{plan_id}/versions/1/restore")
        assert res_restore.status_code == 200
        restored_plan = res_restore.json()
        assert restored_plan["plan_data"]["ai_explanation"] == "Test plan versioning AST"

        # Verify version list now has 4 versions
        res_vers_after = await client.get(f"/api/v1/plans/{plan_id}/versions")
        assert res_vers_after.status_code == 200
        vers_after = res_vers_after.json()
        assert len(vers_after) == 4
        assert vers_after[0]["version_number"] == 4
        assert vers_after[0]["edit_type"] == "version_restored"

        # 8. Verify Execution Lock Protection
        # Create active job linked to plan
        async with TestSession() as session:
            job = MigrationJob(
                migration_plan_id=uuid.UUID(plan_id),
                agent_id=uuid.UUID(agent_id),
                status="running",
            )
            session.add(job)
            await session.commit()

        # Attempt restore during running job → 409 Conflict
        res_lock_restore = await client.post(f"/api/v1/plans/{plan_id}/versions/1/restore")
        assert res_lock_restore.status_code == 409
        assert "Cannot edit migration plan while execution job" in res_lock_restore.json()["detail"]

    app.dependency_overrides.clear()
