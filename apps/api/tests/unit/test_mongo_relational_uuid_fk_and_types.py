"""
Unit tests for PostgreSQL to MongoDB migration fixes:
1. Deterministic UUIDv5 foreign key and primary key matching in ASTTransformer
2. Target writer BSON serialization: _id promotion, Decimal128, and datetime (ISODate)
3. MigrationPlanValidator automatic FK type synchronization when parent PK is UUID
"""

import sys
from pathlib import Path
import uuid
from datetime import datetime, timezone
from decimal import Decimal
import polars as pl
import pytest
from bson import Decimal128

REPO_ROOT = Path(__file__).resolve().parents[3]
AGENT_DIR = REPO_ROOT / "apps" / "agent"
if str(AGENT_DIR) not in sys.path:
    sys.path.insert(0, str(AGENT_DIR))

try:
    from engine.transformers.ast_transformer import ASTTransformer
    from engine.writers.target_writer import _sanitize_rows_for_target
except ImportError:
    from apps.agent.engine.transformers.ast_transformer import ASTTransformer
    from apps.agent.engine.writers.target_writer import _sanitize_rows_for_target

from app.modules.migration_plans.migration_plans_engine.migration_plans_validator import MigrationPlanValidator


class TestUUIDPrimaryKeyAndForeignKeyMatching:
    def test_pk_and_fk_produce_identical_uuids(self):
        """
        Verify that a parent PK and a child FK referring to the same integer ID
        (e.g., category_id: 100) yield the exact same deterministic UUIDv5.
        """
        src_ident = "src_db_1"
        parent_df = pl.DataFrame({"category_id": [100, 200, 300]})
        child_df = pl.DataFrame({"product_id": [1, 2], "category_id": [100, 200]})

        # Parent table transform: category_id -> id (UUID PK)
        parent_col_mapping = [
            {
                "target_column": "id",
                "transformation_type": "type_cast",
                "target_data_type": "uuid",
                "is_primary_key": True,
                "source_columns": [{"identifier": src_ident, "table_name": "categories", "column_name": "category_id"}],
            }
        ]
        parent_res, _ = ASTTransformer.transform_chunk(
            df=parent_df,
            column_mappings=parent_col_mapping,
        )
        parent_uuids = parent_res["id"].to_list()

        # Child table transform: category_id -> category_id (UUID FK)
        child_col_mapping = [
            {
                "target_column": "id",
                "transformation_type": "type_cast",
                "target_data_type": "uuid",
                "is_primary_key": True,
                "source_columns": [{"identifier": src_ident, "table_name": "products", "column_name": "product_id"}],
            },
            {
                "target_column": "category_id",
                "transformation_type": "type_cast",
                "target_data_type": "uuid",
                "source_columns": [{"identifier": src_ident, "table_name": "products", "column_name": "category_id"}],
            },
        ]
        child_res, _ = ASTTransformer.transform_chunk(
            df=child_df,
            column_mappings=child_col_mapping,
        )
        child_fk_uuids = child_res["category_id"].to_list()

        # Assert perfect equality between parent PK and child FK
        assert parent_uuids[0] == child_fk_uuids[0]
        assert parent_uuids[1] == child_fk_uuids[1]
        expected_uuid_0 = str(uuid.uuid5(uuid.NAMESPACE_DNS, f"{src_ident}_100"))
        assert parent_uuids[0] == expected_uuid_0
        assert child_fk_uuids[0] == expected_uuid_0

    def test_null_foreign_key_is_preserved_as_none(self):
        """Nullable foreign keys should produce None, not an empty string or dummy UUID."""
        src_ident = "src_db_1"
        df = pl.DataFrame({"customer_id": ["10", None, ""]})
        col_mapping = [
            {
                "target_column": "customer_id",
                "transformation_type": "type_cast",
                "target_data_type": "uuid",
                "source_columns": [{"identifier": src_ident, "table_name": "orders", "column_name": "customer_id"}],
            }
        ]
        res, _ = ASTTransformer.transform_chunk(df=df, column_mappings=col_mapping)
        vals = res["customer_id"].to_list()
        assert vals[0] == str(uuid.uuid5(uuid.NAMESPACE_DNS, f"{src_ident}_10"))
        assert vals[1] is None
        assert vals[2] is None


class TestTargetWriterMongoBSONSerialization:
    def test_single_id_promotion_for_mongo(self):
        """
        Verify that 'id' is promoted to '_id' for MongoDB targets so documents
        do not have both an auto ObjectId and a separate UUID id.
        """
        rows = [
            {"id": "5cc524b1-d434-5ce3-92d2-5ee6b343583b", "name": "Electronics"},
            {"id": "a931c890-0000-0000-0000-000000000000", "name": "Clothing"},
        ]
        sanitized = _sanitize_rows_for_target(rows, engine_type="mongodb")
        for doc in sanitized:
            assert "_id" in doc
            assert "id" not in doc
            assert isinstance(doc["_id"], str)

    def test_sql_targets_do_not_promote_id_to_underscore_id(self):
        """SQL targets (e.g. postgresql) must keep 'id' intact."""
        rows = [{"id": "5cc524b1-d434-5ce3-92d2-5ee6b343583b", "name": "Electronics"}]
        sanitized = _sanitize_rows_for_target(rows, engine_type="postgresql")
        assert "id" in sanitized[0]
        assert "_id" not in sanitized[0]

    def test_decimal_and_datetime_bson_types(self):
        """
        Verify that Decimal becomes Decimal128 and datetime / ISO strings become native datetimes
        for MongoDB targets.
        """
        rows = [
            {
                "id": "123",
                "price_dec": Decimal("99.99"),
                "price_str": "149.50",
                "created_dt": datetime(2026, 1, 15, 10, 30, tzinfo=timezone.utc),
                "created_iso": "2026-02-20T14:45:00Z",
            }
        ]
        sanitized = _sanitize_rows_for_target(rows, engine_type="mongodb")
        doc = sanitized[0]

        # Check Decimal128
        assert isinstance(doc["price_dec"], Decimal128)
        assert str(doc["price_dec"]) == "99.99"
        assert isinstance(doc["price_str"], Decimal128)
        assert str(doc["price_str"]) == "149.50"

        # Check datetime
        assert isinstance(doc["created_dt"], datetime)
        assert isinstance(doc["created_iso"], datetime)


class TestPlanValidatorFKSynchronization:
    def test_validator_auto_synchronizes_fk_to_uuid_when_parent_pk_is_uuid(self):
        """
        When a parent table's PK is configured as UUID, validator should ensure
        any foreign key referencing that parent table is also typed as UUID.
        """
        raw_ast = {
            "version": "1.0",
            "target_database_type": "mongodb",
            "confidence_score": 0.95,
            "ai_explanation": "Test migration summary",
            "table_mappings": [
                {
                    "target_table_name": "categories",
                    "transformation_type": "direct_copy",
                    "ai_reasoning": "Copy categories table",
                    "source_tables": [{"identifier": "src_db_1", "table_name": "categories", "schema_name": "public", "join_type": "primary"}],
                    "column_mappings": [
                        {
                            "target_column_name": "id",
                            "transformation_type": "type_cast",
                            "ui_badge_type": "type_cast",
                            "target_data_type": "uuid",
                            "is_primary_key": True,
                            "explanation": "Convert category_id to UUID PK",
                            "source_columns": [{"identifier": "src_db_1", "schema_name": "public", "table_name": "categories", "column_name": "category_id"}],
                        }
                    ],
                },
                {
                    "target_table_name": "products",
                    "transformation_type": "direct_copy",
                    "ai_reasoning": "Copy products table",
                    "source_tables": [{"identifier": "src_db_1", "table_name": "products", "schema_name": "public", "join_type": "primary"}],
                    "column_mappings": [
                        {
                            "target_column_name": "id",
                            "transformation_type": "type_cast",
                            "ui_badge_type": "type_cast",
                            "target_data_type": "uuid",
                            "is_primary_key": True,
                            "explanation": "Convert product_id to UUID PK",
                            "source_columns": [{"identifier": "src_db_1", "schema_name": "public", "table_name": "products", "column_name": "product_id"}],
                        },
                        {
                            "target_column_name": "category_id",
                            "transformation_type": "direct_copy",
                            "ui_badge_type": "direct_copy",
                            "target_data_type": "integer",  # Mismatched initial type from naive LLM
                            "explanation": "Direct copy category_id",
                            "source_columns": [{"identifier": "src_db_1", "schema_name": "public", "table_name": "products", "column_name": "category_id"}],
                        },
                    ],
                },
            ],
        }

        val_res = MigrationPlanValidator.validate(raw_ast, snapshots=[], alias_map={"src_db_1": "src_db_1"})

        # Category FK in products should be auto-synchronized to uuid
        products_map = next(tm for tm in raw_ast["table_mappings"] if tm["target_table_name"] == "products")
        cat_fk_col = next(cm for cm in products_map["column_mappings"] if cm["target_column_name"] == "category_id")

        assert cat_fk_col["target_data_type"] == "uuid"
        assert cat_fk_col["transformation_type"] == "type_cast"
