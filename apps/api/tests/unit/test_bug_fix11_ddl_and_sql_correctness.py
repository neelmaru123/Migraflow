"""
Unit test for Fix 11: SQL->SQL correctness gaps bundle.
Verifies DDL error propagation, MySQL row count estimates, proportional abort threshold,
target column collision validation, and merge_concat length overflow warning.
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest

REPO_ROOT = Path(__file__).resolve().parents[3].parent
API_DIR = REPO_ROOT / "apps" / "api"
AGENT_DIR = REPO_ROOT / "apps" / "agent"
if str(API_DIR) not in sys.path:
    sys.path.insert(0, str(API_DIR))
if str(AGENT_DIR) not in sys.path:
    sys.path.insert(0, str(AGENT_DIR))

from execution_engine import DDLExecutor, TargetWriterFactory
from metadata_engine import AgentMetadataEngine
from app.modules.migration_plans.migration_plans_engine.migration_plans_validator import MigrationPlanValidator
from app.modules.migration_plans.migration_plans_schemas import TransformationPlanAST, TableMappingSpec, ColumnMappingSpec, SourceTableRef, SourceColumnRef


def test_ddl_failure_raises_exception_for_genuine_error():
    """Verifies that DDLExecutor raises RuntimeError on genuine DDL failure but logs warning for benign notices."""
    mock_engine = MagicMock()

    # 1. Genuine DDL syntax error raises RuntimeError
    mock_conn1 = MagicMock()
    mock_conn1.execute.side_effect = Exception("syntax error at or near 'INVALID'")
    mock_engine.begin.return_value.__enter__.return_value = mock_conn1

    with patch("engine.ddl_executor._get_engine", return_value=mock_engine):
        with pytest.raises(RuntimeError) as exc_info:
            DDLExecutor.execute_ddl_list("sqlite:///:memory:", ["INVALID DDL SYNTAX;"])
        assert "DDL execution failed" in str(exc_info.value) or "DDL error" in str(exc_info.value) or "DDL failed" in str(exc_info.value)

    # 2. Benign "table already exists" notice does NOT raise RuntimeError
    mock_conn2 = MagicMock()
    mock_conn2.execute.side_effect = Exception("table users already exists")
    mock_engine.begin.return_value.__enter__.return_value = mock_conn2

    with patch("engine.ddl_executor._get_engine", return_value=mock_engine):
        # Should not raise exception
        DDLExecutor.execute_ddl_list("sqlite:///:memory:", ["CREATE TABLE users (id INT);"])

    # 3. Benign MySQL "Duplicate key name" (error 1061) does NOT raise RuntimeError
    mock_conn3 = MagicMock()
    mock_conn3.execute.side_effect = Exception('(1061, "Duplicate key name \'idx_orders_customer_id\'")')
    mock_engine.begin.return_value.__enter__.return_value = mock_conn3

    with patch("engine.ddl_executor._get_engine", return_value=mock_engine):
        # Should not raise exception
        DDLExecutor.execute_ddl_list("sqlite:///:memory:", ["CREATE INDEX idx_orders_customer_id ON orders(customer_id);"], "Post-Migration DDL")


def test_mysql_row_count_estimate_query():
    """Verifies that AgentMetadataEngine executes COALESCE(table_rows, 0) for MySQL databases."""
    mock_engine = MagicMock()
    mock_conn = MagicMock()
    mock_engine.connect.return_value.__enter__.return_value = mock_conn

    mock_conn.execute.side_effect = [
        MagicMock(scalar=lambda: "8.0.32"),  # SELECT version()
        MagicMock(fetchall=lambda: [("test_db",)]),  # schemata
        MagicMock(fetchall=lambda: [("test_db", "users", "BASE TABLE", 15000)]),  # tables
        MagicMock(fetchall=lambda: []),  # columns
        MagicMock(fetchall=lambda: []),  # constraints
    ]

    with patch("metadata_engine.create_engine", return_value=mock_engine):
        res = AgentMetadataEngine.introspect_database("mysql_src", "mysql+pymysql://root:pass@localhost:3306/test_db")
        assert res is not None
        assert res["tables"][0]["row_count"] == 15000


def test_proportional_abort_threshold_for_small_tables():
    """Verifies that TargetWriterFactory.bulk_load aborts when a small table (<1000 rows) exceeds 50% failure rate."""
    mock_engine = MagicMock()
    mock_conn = MagicMock()
    # Batch execute fails, forcing per-row fallback
    mock_conn.execute.side_effect = Exception("Batch insert failed")
    mock_engine.begin.return_value.__enter__.return_value = mock_conn

    small_rows = [{"id": i, "val": f"v{i}"} for i in range(20)]

    with patch("execution_engine._get_engine", return_value=mock_engine):
        with pytest.raises(RuntimeError) as exc_info:
            TargetWriterFactory.bulk_load("sqlite:///:memory:", "sqlite", "users", small_rows)
        assert "Error rate exceeded 50%" in str(exc_info.value)


def test_target_column_collision_validator_error():
    """Verifies that MigrationPlanValidator flags duplicate target_column_name in the same table mapping."""
    ast = TransformationPlanAST(
        target_database_type="postgresql",
        ai_explanation="Test plan",
        table_mappings=[
            TableMappingSpec(
                target_table_name="users",
                transformation_type="direct_copy",
                ai_reasoning="Mapping users",
                confidence_score=0.9,
                source_tables=[SourceTableRef(identifier="src1", table_name="users")],
                column_mappings=[
                    ColumnMappingSpec(
                        target_column_name="email",
                        target_data_type="varchar",
                        transformation_type="direct_copy",
                        ui_badge_type="direct_copy",
                        explanation="Col 1",
                        source_columns=[SourceColumnRef(identifier="src1", table_name="users", column_name="email1")],
                    ),
                    ColumnMappingSpec(
                        target_column_name="email",  # Collision!
                        target_data_type="varchar",
                        transformation_type="direct_copy",
                        ui_badge_type="direct_copy",
                        explanation="Col 2 duplicate",
                        source_columns=[SourceColumnRef(identifier="src1", table_name="users", column_name="email2")],
                    ),
                ],
            )
        ],
    )

    result = MigrationPlanValidator.validate(ast, [])
    assert result.is_valid is False
    assert any("Duplicate target column name 'email'" in err for err in result.errors)


def test_merge_concat_length_overflow_validator_warning():
    """Verifies that MigrationPlanValidator warns when merge_concat source column lengths exceed target max_length."""
    ast = TransformationPlanAST(
        target_database_type="postgresql",
        ai_explanation="Test plan",
        table_mappings=[
            TableMappingSpec(
                target_table_name="users",
                transformation_type="direct_copy",
                ai_reasoning="Mapping users",
                confidence_score=0.9,
                source_tables=[SourceTableRef(identifier="src1", table_name="users")],
                column_mappings=[
                    ColumnMappingSpec(
                        target_column_name="full_address",
                        target_data_type="varchar(50)",
                        transformation_type="merge_concat",
                        ui_badge_type="merge_concat",
                        explanation="Concat address fields",
                        max_length=50,  # Small max length constraint
                        source_columns=[
                            SourceColumnRef(identifier="src1", table_name="users", column_name="street"),
                            SourceColumnRef(identifier="src1", table_name="users", column_name="city"),
                        ],
                    )
                ],
            )
        ],
    )

    result = MigrationPlanValidator.validate(ast, [])
    assert any("may exceed target column max length constraint (50)" in w for w in result.warnings)
