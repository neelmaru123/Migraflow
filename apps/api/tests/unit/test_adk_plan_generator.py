"""
Unit Tests for Google ADK Multi-Agent Architecture Engine
Tests:
- DDL Linter tool (PostgreSQL, MySQL syntax and two-phase hygiene)
- Type Compatibility tool (cross-dialect type mapping and suggestions)
- Foreign Key Topology tool (cycle detection and topological order)
- Google ADK Engine generation and refinement flows with gemini-3.5-flash-lite
- Delegation routing from llm_plan_generator to adk_plan_generator
"""

from unittest.mock import MagicMock, patch
import pytest

from app.core.config import settings
from app.modules.migration_plans.migration_plans_engine.adk.agents import (
    GoogleADKMigrationEngine,
)
from app.modules.migration_plans.migration_plans_engine.adk.tools import (
    analyze_foreign_key_topology,
    check_type_compatibility,
    validate_ddl_batch,
    validate_ddl_statement,
)
from app.modules.migration_plans.migration_plans_engine.migration_plans_llm import (
    llm_plan_generator,
)
from app.modules.migration_plans.migration_plans_schemas import (
    ColumnMappingSpec,
    RefinementFeedback,
    SourceColumnRef,
    SourceTableRef,
    TableMappingSpec,
    TransformationPlanAST,
)


def _build_sample_ast() -> TransformationPlanAST:
    return TransformationPlanAST(
        target_database_type="postgresql",
        ai_explanation="ADK Engine test plan AST",
        confidence_score=0.98,
        warnings=[],
        table_mappings=[
            TableMappingSpec(
                target_table_name="users",
                transformation_type="direct_copy",
                ai_reasoning="Direct copy users table",
                confidence_score=0.99,
                source_tables=[
                    SourceTableRef(identifier="src_1", schema_name="public", table_name="users")
                ],
                column_mappings=[
                    ColumnMappingSpec(
                        target_column_name="id",
                        target_data_type="uuid",
                        nullable=False,
                        is_primary_key=True,
                        transformation_type="direct_copy",
                        ui_badge_type="direct_copy",
                        source_columns=[SourceColumnRef(identifier="src_1", table_name="users", column_name="id")],
                        explanation="Primary key",
                    ),
                    ColumnMappingSpec(
                        target_column_name="email",
                        target_data_type="varchar(255)",
                        nullable=False,
                        is_primary_key=False,
                        transformation_type="direct_copy",
                        ui_badge_type="direct_copy",
                        source_columns=[SourceColumnRef(identifier="src_1", table_name="users", column_name="email")],
                        explanation="User email",
                    ),
                ],
            )
        ],
        pre_migration_ddl=[
            'CREATE TABLE users (id UUID PRIMARY KEY DEFAULT gen_random_uuid(), email VARCHAR(255) NOT NULL);'
        ],
        post_migration_ddl=[
            'CREATE INDEX idx_users_email ON users (email);'
        ],
    )


# ============================================================================
# 1. Tool Tests
# ============================================================================

def test_ddl_linter_tool_postgres_valid():
    ddl = "CREATE TABLE customers (id UUID PRIMARY KEY DEFAULT gen_random_uuid(), name VARCHAR(100));"
    res = validate_ddl_statement(ddl, target_dialect="postgresql", is_pre_migration=True)
    assert res["is_valid"] is True
    assert len(res["errors"]) == 0


def test_ddl_linter_tool_detects_invalid_uuid_v4():
    ddl = "CREATE TABLE customers (id UUID PRIMARY KEY DEFAULT uuid_v4(), name VARCHAR(100));"
    res = validate_ddl_statement(ddl, target_dialect="postgresql", is_pre_migration=True)
    assert res["is_valid"] is False
    assert any("uuid_v4()" in e for e in res["errors"])


def test_ddl_linter_tool_detects_pre_migration_fk_warning():
    ddl = "CREATE TABLE orders (id INT PRIMARY KEY, customer_id INT REFERENCES customers(id));"
    res = validate_ddl_statement(ddl, target_dialect="postgresql", is_pre_migration=True)
    assert len(res["warnings"]) > 0
    assert any("FOREIGN KEY" in w for w in res["warnings"])


def test_type_compatibility_tool():
    # Direct copy
    res = check_type_compatibility("VARCHAR(100)", "VARCHAR(100)")
    assert res["compatible"] is True
    assert res["recommended_transformation"] == "direct_copy"

    # Numeric widening
    res = check_type_compatibility("INT", "BIGINT")
    assert res["compatible"] is True
    assert res["recommended_transformation"] == "type_cast"
    assert res["is_lossless"] is True

    # JSON to text
    res = check_type_compatibility("JSONB", "TEXT")
    assert res["compatible"] is True
    assert res["recommended_transformation"] == "json_stringify"

    # Array to CSV
    res = check_type_compatibility("TEXT[]", "VARCHAR(255)")
    assert res["compatible"] is True
    assert res["recommended_transformation"] == "array_to_csv"


def test_fk_topology_tool_no_cycles():
    tables = ["customers", "orders", "order_items"]
    relationships = [
        {"from_table": "orders", "to_table": "customers"},
        {"from_table": "order_items", "to_table": "orders"},
    ]
    res = analyze_foreign_key_topology(tables, relationships)
    assert res["has_cycles"] is False
    assert res["is_valid"] is True
    # Customers must come before orders, and orders before order_items
    order = res["recommended_load_order"]
    assert order.index("customers") < order.index("orders")
    assert order.index("orders") < order.index("order_items")


def test_fk_topology_tool_detects_cycles():
    tables = ["table_a", "table_b"]
    relationships = [
        {"from_table": "table_a", "to_table": "table_b"},
        {"from_table": "table_b", "to_table": "table_a"},
    ]
    res = analyze_foreign_key_topology(tables, relationships)
    assert res["has_cycles"] is True
    assert res["is_valid"] is False
    assert len(res["cycle_tables"]) > 0


# ============================================================================
# 2. ADK Engine Execution Tests
# ============================================================================

def test_adk_engine_generate_plan_success():
    engine = GoogleADKMigrationEngine(api_key="mock-key", model="gemini-3.5-flash-lite")
    mock_ast = _build_sample_ast()

    mock_response = MagicMock()
    mock_response.parsed = mock_ast
    mock_response.text = None

    mock_client = MagicMock()
    mock_client.models.generate_content.return_value = mock_response

    with patch.object(engine, "_client", mock_client):
        stages = []
        result = engine.generate_plan(
            context_str="schema: public\n  table: users",
            target_db_type="postgresql",
            progress_callback=lambda stage, msg: stages.append(stage),
        )

        assert result.target_database_type == "postgresql"
        assert len(result.table_mappings) == 1
        assert "inspect_schema" in stages
        assert "completed" in stages
        assert mock_client.models.generate_content.called


def test_adk_engine_refine_plan_success():
    engine = GoogleADKMigrationEngine(api_key="mock-key", model="gemini-3.5-flash-lite")
    mock_ast = _build_sample_ast()
    mock_ast.refinement_feedback = RefinementFeedback(
        applied=True,
        verdict="applied",
        user_prompt="rename to app_users",
        explanation="Renamed target table users to app_users",
        table_count_before=1,
        table_count_after=1,
        changes_summary=["Renamed users -> app_users"],
    )

    mock_response = MagicMock()
    mock_response.parsed = mock_ast
    mock_response.text = None

    mock_client = MagicMock()
    mock_client.models.generate_content.return_value = mock_response

    with patch.object(engine, "_client", mock_client):
        stages = []
        result = engine.refine_plan(
            context_str="schema: public\n  table: users",
            current_ast_dict=_build_sample_ast().model_dump(mode="json"),
            user_feedback="rename users to app_users",
            progress_callback=lambda stage, msg: stages.append(stage),
        )

        assert result.refinement_feedback is not None
        assert result.refinement_feedback.applied is True
        assert result.refinement_feedback.verdict == "applied"
        assert "evaluating_refinement" in stages


def test_llm_plan_generator_routes_to_adk_when_configured():
    from app.modules.migration_plans.migration_plans_engine.adk import adk_plan_generator as adk_instance
    mock_ast = _build_sample_ast()

    with patch.object(adk_instance, "generate", return_value=mock_ast) as mock_adk_gen:
        with patch.object(settings, "LLM_ENGINE_TYPE", "google_adk"):
            with patch.object(settings, "LLM_PROVIDER", "gemini"):
                result = llm_plan_generator.generate("context", "postgresql")
                assert result == mock_ast
                assert mock_adk_gen.called
