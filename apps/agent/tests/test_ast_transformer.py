"""
Unit tests for ASTTransformer, specifically verifying lineage column (_source_origin) auto-population.
Can be executed directly via python or via pytest.
"""

import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import polars as pl
from engine.transformers.ast_transformer import ASTTransformer


def test_source_origin_auto_population():
    """Verify that _source_origin is populated with the source_origin argument and never None."""
    raw_df = pl.DataFrame({
        "customer_id": ["c1", "c2"],
        "email": ["alice@example.com", "bob@example.com"],
        "first_name": ["Alice", "Bob"],
    })

    column_mappings = [
        {
            "target_column_name": "id",
            "source_columns": [{"column_name": "customer_id"}],
            "transformation_type": "direct_copy",
            "is_primary_key": True,
        },
        {
            "target_column_name": "email",
            "source_columns": [{"column_name": "email"}],
            "transformation_type": "direct_copy",
        },
        {
            "target_column_name": "_source_origin",
            "source_columns": [],
            "transformation_type": "new_column_added",
            "nullable": False,
        },
    ]

    # Test with explicit source_origin
    df_trans, errors = ASTTransformer.transform_chunk(
        raw_df,
        column_mappings,
        source_origin="src_db_1.customers",
    )

    assert errors == 0, f"Expected 0 errors, got {errors}"
    assert "_source_origin" in df_trans.columns, "Missing _source_origin column"
    origins = df_trans["_source_origin"].to_list()
    assert origins == ["src_db_1.customers", "src_db_1.customers"], f"Unexpected origins: {origins}"
    print("[PASS] test_source_origin_auto_population passed successfully.")


def test_source_origin_fallback_when_none():
    """Verify fallback behavior when source_origin is not provided."""
    raw_df = pl.DataFrame({
        "customer_id": ["c1"],
        "email": ["alice@example.com"],
    })

    column_mappings = [
        {
            "target_column_name": "_source_origin",
            "source_columns": [],
            "transformation_type": "new_column_added",
            "constant_value": "default_src",
        },
    ]

    df_trans, errors = ASTTransformer.transform_chunk(
        raw_df,
        column_mappings,
        source_origin=None,
    )

    assert errors == 0, f"Expected 0 errors, got {errors}"
    assert df_trans["_source_origin"].to_list() == ["default_src"]
    print("[PASS] test_source_origin_fallback_when_none passed successfully.")


if __name__ == "__main__":
    test_source_origin_auto_population()
    test_source_origin_fallback_when_none()
    print("ALL TESTS PASSED!")
