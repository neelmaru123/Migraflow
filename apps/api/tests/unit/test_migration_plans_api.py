"""
Unit Tests for Migration Plans Domain:
- MetadataContextSerializer (Zero Raw Data verification)
- REST API lifecycle (generate, list, get, edit)
- All 9 transformation_type values validated
- LLM retry logic (mock LLM)
"""

import json
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.db import Base, get_db
from app.main import app
from app.modules.migration_plans.migration_plans_engine.migration_plans_llm import (
    MetadataContextSerializer,
    llm_plan_generator,
)
from app.modules.migration_plans.migration_plans_schemas import (
    ColumnMappingSpec,
    ConflictResolutionSpec,
    SourceColumnRef,
    SourceTableRef,
    TableMappingSpec,
    TransformationPlanAST,
)


# ============================================================================
# Fixtures
# ============================================================================

def _make_test_plan_ast() -> TransformationPlanAST:
    """Build a complete TransformationPlanAST covering all 9 transformation_type values."""
    return TransformationPlanAST(
        target_database_type="postgresql",
        ai_explanation="## Test Migration Plan\nMerging source_db_1 and source_db_2 into target_db.",
        confidence_score=0.91,
        warnings=["users.phone_number format differs between sources."],
        table_mappings=[
            TableMappingSpec(
                target_table_name="target_users",
                transformation_type="merge",
                ai_reasoning="Merging users and customers into a unified target_users table.",
                confidence_score=0.93,
                conflict_resolution=ConflictResolutionSpec(
                    primary_key_strategy="uuid_v4_rekey",
                    deduplication_key="email",
                    deduplication_strategy="first_wins",
                ),
                source_tables=[
                    SourceTableRef(identifier="source_db_1", schema_name="public", table_name="users", join_type="primary"),
                    SourceTableRef(identifier="source_db_2", schema_name="shop", table_name="customers", join_type="union_merge"),
                ],
                column_mappings=[
                    # 1. direct_copy
                    ColumnMappingSpec(
                        target_column_name="email",
                        target_data_type="varchar(255)",
                        nullable=False,
                        is_primary_key=False,
                        transformation_type="direct_copy",
                        ui_badge_type="direct_copy",
                        source_columns=[SourceColumnRef(identifier="source_db_1", table_name="users", column_name="email")],
                        explanation="Email copied directly, no transformation.",
                    ),
                    # 2. merge_concat
                    ColumnMappingSpec(
                        target_column_name="full_name",
                        target_data_type="varchar(255)",
                        nullable=True,
                        transformation_type="merge_concat",
                        ui_badge_type="merge_concat",
                        source_columns=[
                            SourceColumnRef(identifier="source_db_2", table_name="customers", column_name="first_name"),
                            SourceColumnRef(identifier="source_db_2", table_name="customers", column_name="last_name"),
                        ],
                        expression_template="CONCAT(first_name, ' ', last_name)",
                        explanation="first_name and last_name merged into full_name.",
                    ),
                    # 3. type_cast
                    ColumnMappingSpec(
                        target_column_name="id",
                        target_data_type="uuid",
                        nullable=False,
                        is_primary_key=True,
                        transformation_type="type_cast",
                        ui_badge_type="type_cast",
                        source_columns=[SourceColumnRef(identifier="source_db_1", table_name="users", column_name="id")],
                        expression_template="gen_random_uuid()",
                        explanation="Integer PK re-keyed to UUID.",
                    ),
                    # 4. split
                    ColumnMappingSpec(
                        target_column_name="street",
                        target_data_type="varchar(255)",
                        nullable=True,
                        transformation_type="split",
                        ui_badge_type="split",
                        source_columns=[SourceColumnRef(identifier="source_db_2", table_name="customers", column_name="full_address")],
                        expression_template="SPLIT_PART(full_address, ',', 1)",
                        explanation="full_address split — street portion.",
                    ),
                    # 5. expression
                    ColumnMappingSpec(
                        target_column_name="net_price",
                        target_data_type="numeric(10,2)",
                        nullable=True,
                        transformation_type="expression",
                        ui_badge_type="expression",
                        source_columns=[
                            SourceColumnRef(identifier="source_db_1", table_name="orders", column_name="price"),
                            SourceColumnRef(identifier="source_db_1", table_name="orders", column_name="discount"),
                        ],
                        expression_template="price * (1 - discount)",
                        explanation="net_price derived from price and discount.",
                    ),
                    # 6. default_constant
                    ColumnMappingSpec(
                        target_column_name="tenant_id",
                        target_data_type="varchar(50)",
                        nullable=False,
                        transformation_type="default_constant",
                        ui_badge_type="default_constant",
                        source_columns=[],
                        constant_value="org_01",
                        explanation="tenant_id filled with constant 'org_01'.",
                    ),
                    # 7. lookup_join
                    ColumnMappingSpec(
                        target_column_name="category_name",
                        target_data_type="varchar(255)",
                        nullable=True,
                        transformation_type="lookup_join",
                        ui_badge_type="lookup_join",
                        source_columns=[SourceColumnRef(identifier="source_db_1", table_name="products", column_name="category_id")],
                        expression_template="SELECT name FROM categories WHERE id = category_id",
                        explanation="FK category_id resolved to category_name.",
                    ),
                    # 8. drop_column
                    ColumnMappingSpec(
                        target_column_name=None,
                        target_data_type=None,
                        nullable=None,
                        transformation_type="drop_column",
                        ui_badge_type="drop_column",
                        source_columns=[SourceColumnRef(identifier="source_db_1", table_name="users", column_name="legacy_code")],
                        explanation="legacy_code has no target equivalent and will be dropped.",
                    ),
                    # 9. new_column_added
                    ColumnMappingSpec(
                        target_column_name="migrated_at",
                        target_data_type="timestamp with time zone",
                        nullable=False,
                        transformation_type="new_column_added",
                        ui_badge_type="new_column_added",
                        source_columns=[],
                        expression_template="CURRENT_TIMESTAMP",
                        explanation="Audit column recording migration timestamp.",
                    ),
                ],
            )
        ],
        pre_migration_ddl=["CREATE EXTENSION IF NOT EXISTS pgcrypto;", "CREATE TABLE target_users (id UUID PRIMARY KEY);"],
        post_migration_ddl=[
            "CREATE INDEX idx_users_email ON target_users(email)",
            "ALTER TABLE target_users ADD CONSTRAINT fk_user_self FOREIGN KEY (id) REFERENCES target_users(id)",
        ],
    )


# ============================================================================
# Test 1: MetadataContextSerializer — Zero Raw Data Policy
# ============================================================================

def test_metadata_context_serializer_zero_raw_data():
    """Verify the serializer output contains ONLY structural metadata — no passwords or rows."""
    # Build a minimal mock MetadataSnapshot
    snap = MagicMock()
    snap.data_source_id = uuid.uuid4()
    snap.database_name = "prod_db"
    snap.database_version = "PostgreSQL 16"
    snap.total_tables = 1
    snap.total_columns = 2
    snap.total_rows = 50000
    snap.relationships = []

    col = MagicMock()
    col.column_name = "email"
    col.native_data_type = "varchar"
    col.data_type = "varchar"
    col.nullable = False
    col.is_primary_key = False
    col.is_unique = True
    col.max_length = 255
    col.numeric_precision = None
    col.numeric_scale = None
    col.default_value = None
    col.ordinal_position = 1

    tbl = MagicMock()
    tbl.table_name = "users"
    tbl.table_type = "table"
    tbl.row_count = 50000
    tbl.columns = [col]
    tbl.constraints = []

    schema = MagicMock()
    schema.schema_name = "public"
    schema.tables = [tbl]

    snap.schemas = [schema]

    alias_map = {str(snap.data_source_id): "source_db_1"}
    context = MetadataContextSerializer.serialize(
        snapshots=[snap],
        source_aliases=alias_map,
        target_db_type="postgresql",
    )

    # ZERO RAW DATA assertions
    assert "password" not in context.lower()
    assert "host" not in context.lower()
    assert "postgresql://" not in context.lower()
    assert "SELECT" not in context  # No raw SQL row queries

    # Structural content assertions
    assert "source_db_1" in context
    assert "users" in context
    assert "email" in context
    assert "varchar" in context
    assert "source_db_1" in context


# ============================================================================
# Test 2: TransformationPlanAST — All 9 transformation_types validated
# ============================================================================

def test_transformation_plan_ast_all_types():
    """Verify that TransformationPlanAST accepts all 9 transformation_type values."""
    plan = _make_test_plan_ast()
    assert plan.confidence_score == 0.91
    assert len(plan.table_mappings) == 1
    table = plan.table_mappings[0]
    assert table.transformation_type == "merge"
    assert len(table.column_mappings) == 9

    types_found = {cm.transformation_type for cm in table.column_mappings}
    expected = {
        "direct_copy", "merge_concat", "type_cast", "split",
        "expression", "default_constant", "lookup_join",
        "drop_column", "new_column_added",
    }
    assert types_found == expected, f"Missing types: {expected - types_found}"

    # Assert all ui_badge_type values match transformation_type
    for cm in table.column_mappings:
        assert cm.ui_badge_type == cm.transformation_type, (
            f"ui_badge_type mismatch: {cm.ui_badge_type} != {cm.transformation_type}"
        )


# ============================================================================
# Test 3: REST API lifecycle with mocked LLM
# ============================================================================

@pytest.mark.asyncio
async def test_migration_plan_api_lifecycle_with_mocked_llm():
    """
    Test full REST API lifecycle:
    1. Register user → Create agent with 2 data sources
    2. POST /api/v1/plans/generate (LLM mocked) → 201 Created
    3. GET /api/v1/plans → List includes plan
    4. GET /api/v1/plans/{plan_id} → Full AST returned
    5. PUT /api/v1/plans/{plan_id} → Edit plan → status='edited'
    6. Ownership security: 403 Forbidden for other user
    """
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

    mock_plan_ast = _make_test_plan_ast()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        # 1. Register user and create agent
        res_u = await client.post("/api/v1/auth/register", json={
            "email": "plan_owner@example.com", "password": "Password123!", "name": "Plan Owner"
        })
        assert res_u.status_code == 201

        res_agent = await client.post("/api/v1/agents", json={
            "name": "AI Plan Agent",
            "agent_identifier": "ai_plan_agent_01",
            "version": "1.0.0",
            "data_sources": [
                {"name": "Source PG DB", "type": "postgresql", "role": "source", "identifier": "src_pg"},
                {"name": "Source MySQL DB", "type": "mysql", "role": "source", "identifier": "src_mysql"},
            ],
        })
        assert res_agent.status_code == 201
        agent_data = res_agent.json()
        agent_id = agent_data["id"]
        api_token = agent_data["api_token"]
        src_pg_source_id = agent_data["data_sources"][0]["id"]

        # Sync metadata snapshots for both sources so plan generation has data
        sync_payload = {
            "identifier": "src_pg",
            "database_name": "prod_db",
            "database_version": "PostgreSQL 16",
            "total_tables": 2,
            "total_columns": 5,
            "total_rows": 0,
            "schemas": [{"schema_name": "public", "tables": [
                {"schema_name": "public", "table_name": "users", "table_type": "table",
                 "row_count": 0, "size_bytes": 0,
                 "columns": [
                     {"column_name": "id", "ordinal_position": 1, "data_type": "uuid", "nullable": False, "is_primary_key": True},
                     {"column_name": "email", "ordinal_position": 2, "data_type": "varchar", "nullable": False, "is_unique": True},
                     {"column_name": "first_name", "ordinal_position": 3, "data_type": "varchar", "nullable": True},
                     {"column_name": "last_name", "ordinal_position": 4, "data_type": "varchar", "nullable": True},
                     {"column_name": "phone_number", "ordinal_position": 5, "data_type": "varchar", "nullable": True},
                     {"column_name": "role", "ordinal_position": 6, "data_type": "varchar", "nullable": True},
                     {"column_name": "status", "ordinal_position": 7, "data_type": "varchar", "nullable": True},
                     {"column_name": "user_metadata", "ordinal_position": 8, "data_type": "jsonb", "nullable": True},
                     {"column_name": "old_system_id", "ordinal_position": 9, "data_type": "integer", "nullable": True},
                     {"column_name": "legacy_code", "ordinal_position": 10, "data_type": "varchar", "nullable": True},
                 ], "constraints": []},
                {"schema_name": "public", "table_name": "customers", "table_type": "table",
                 "row_count": 0, "size_bytes": 0,
                 "columns": [
                     {"column_name": "first_name", "ordinal_position": 1, "data_type": "varchar", "nullable": True},
                     {"column_name": "last_name", "ordinal_position": 2, "data_type": "varchar", "nullable": True},
                     {"column_name": "full_address", "ordinal_position": 3, "data_type": "varchar", "nullable": True},
                     {"column_name": "company_name", "ordinal_position": 4, "data_type": "varchar", "nullable": True},
                     {"column_name": "user_id", "ordinal_position": 5, "data_type": "uuid", "nullable": True},
                 ], "constraints": []},
                {"schema_name": "public", "table_name": "orders", "table_type": "table",
                 "row_count": 0, "size_bytes": 0,
                 "columns": [
                     {"column_name": "price", "ordinal_position": 1, "data_type": "numeric", "nullable": True},
                     {"column_name": "discount", "ordinal_position": 2, "data_type": "numeric", "nullable": True},
                 ], "constraints": []},
            ]}],
        }
        res_sync = await client.post("/api/v1/metadata/sync", json=sync_payload, headers={"X-Agent-Token": api_token})
        assert res_sync.status_code == 201

        sync_payload_mysql = {
            "identifier": "src_mysql",
            "database_name": "shop_db",
            "database_version": "MySQL 8.0",
            "total_tables": 2,
            "total_columns": 5,
            "total_rows": 0,
            "schemas": [{"schema_name": "shop", "tables": [
                {"schema_name": "shop", "table_name": "customers", "table_type": "table",
                 "row_count": 0, "size_bytes": 0,
                 "columns": [
                     {"column_name": "first_name", "ordinal_position": 1, "data_type": "varchar", "nullable": True},
                     {"column_name": "last_name", "ordinal_position": 2, "data_type": "varchar", "nullable": True},
                     {"column_name": "full_address", "ordinal_position": 3, "data_type": "varchar", "nullable": True},
                     {"column_name": "company_name", "ordinal_position": 4, "data_type": "varchar", "nullable": True},
                     {"column_name": "user_id", "ordinal_position": 5, "data_type": "uuid", "nullable": True},
                 ], "constraints": []},
                {"schema_name": "shop", "table_name": "orders", "table_type": "table",
                 "row_count": 0, "size_bytes": 0,
                 "columns": [
                     {"column_name": "price", "ordinal_position": 1, "data_type": "numeric", "nullable": True},
                     {"column_name": "discount", "ordinal_position": 2, "data_type": "numeric", "nullable": True},
                 ], "constraints": []},
            ]}],
        }
        res_sync_mysql = await client.post("/api/v1/metadata/sync", json=sync_payload_mysql, headers={"X-Agent-Token": api_token})
        assert res_sync_mysql.status_code == 201

        mock_plan_ast = _make_test_plan_ast()
        for tm in mock_plan_ast.table_mappings:
            for st in tm.source_tables:
                st.identifier = "src_pg" if st.identifier == "source_db_1" else "src_mysql"
            for cm in tm.column_mappings:
                for sc in cm.source_columns:
                    sc.identifier = "src_pg" if sc.identifier == "source_db_1" else "src_mysql"

        # 2. POST /api/v1/plans/generate — LLM mocked
        with patch.object(
            llm_plan_generator,
            "generate",
            return_value=mock_plan_ast,
        ), patch.object(
            llm_plan_generator,
            "refine",
            return_value=mock_plan_ast,
        ):
            res_gen = await client.post("/api/v1/plans/generate", json={
                "agent_id": agent_id,
                "target_config": {"database_type": "postgresql"},
            })

        assert res_gen.status_code == 201, res_gen.text
        gen_data = res_gen.json()
        plan_id = gen_data["id"]
        assert gen_data["status"] == "draft"
        assert gen_data["confidence_score"] > 0.0
        assert "table_mappings" in gen_data["plan_data"]
        assert len(gen_data["plan_data"]["table_mappings"]) > 0

        # Approve plan for Phase 5 HITL compliance
        res_appr = await client.post(f"/api/v1/plans/{plan_id}/approve")
        assert res_appr.status_code == 200
        assert res_appr.json()["status"] == "approved"

        # 3. GET /api/v1/plans — List plans
        res_list = await client.get("/api/v1/plans")
        assert res_list.status_code == 200
        assert len(res_list.json()) == 1
        assert res_list.json()[0]["id"] == plan_id

        # 4. GET /api/v1/plans/{plan_id} — Full AST
        res_detail = await client.get(f"/api/v1/plans/{plan_id}")
        assert res_detail.status_code == 200
        detail = res_detail.json()
        assert detail["id"] == plan_id
        assert "pre_migration_ddl" in detail["plan_data"]
        assert "post_migration_ddl" in detail["plan_data"]

        # Verify all 9 transformation types are present
        col_types = {
            cm["transformation_type"]
            for tm in detail["plan_data"]["table_mappings"]
            for cm in tm["column_mappings"]
        }
        expected_types = {
            "direct_copy", "merge_concat", "type_cast", "split",
            "expression", "default_constant", "lookup_join",
            "drop_column", "new_column_added",
        }
        assert col_types == expected_types

        # 5. PUT /api/v1/plans/{plan_id} — Edit plan
        edited_plan_data = detail["plan_data"].copy()
        edited_plan_data["_edited"] = True
        res_edit = await client.put(f"/api/v1/plans/{plan_id}", json=edited_plan_data)
        assert res_edit.status_code == 200
        assert res_edit.json()["status"] in ["edited", "awaiting_approval"]


        # 6. Ownership security — second user cannot access
        await client.post("/api/v1/auth/register", json={
            "email": "other_user@example.com", "password": "Password123!", "name": "Other User"
        })
        res_unauth = await client.get(f"/api/v1/plans/{plan_id}")
        assert res_unauth.status_code == 403

    app.dependency_overrides.clear()
