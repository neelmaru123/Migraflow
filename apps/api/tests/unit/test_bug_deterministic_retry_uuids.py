"""
Unit tests verifying deterministic UUID generation for safe migration retries.

Covers:
1. Happy path regression: non-null source values under default uuid_v5 (and prefix_id)
   generate identical IDs to previous behavior without alteration.
2. Actual bug scenario (retry / partial failure idempotency):
   Unresolved PK columns and uuid_v4_rekey generate deterministic UUIDs across retries
   with the same job_id and offset, ensuring target INSERT OR IGNORE / ON CONFLICT DO NOTHING
   prevents duplicate rows upon re-execution.
3. Multi-source merge collision prevention:
   Multiple sources merging into the same target table at the exact same row offset
   generate distinct UUIDs due to source_identifier and source_table in seed_prefix.
"""

import os
import sys
from pathlib import Path
import sqlite3
import uuid
import polars as pl
import pytest
from sqlalchemy import create_engine, text

REPO_ROOT = Path(__file__).resolve().parents[3]
AGENT_DIR = REPO_ROOT / "apps" / "agent"
if str(AGENT_DIR) not in sys.path:
    sys.path.insert(0, str(AGENT_DIR))

try:
    from engine.transformers.ast_transformer import (
        ASTTransformer,
        _deterministic_fallback_uuid,
    )
    from engine.writers.target_writer import TargetWriterFactory
except ImportError:
    from apps.agent.engine.transformers.ast_transformer import (
        ASTTransformer,
        _deterministic_fallback_uuid,
    )
    from apps.agent.engine.writers.target_writer import TargetWriterFactory


def test_happy_path_regression_non_null_values():
    """
    Happy path regression:
    Verify that for non-null source values, default UUID v5 generates the exact same
    hash as before (uuid5(NAMESPACE_DNS, f"{src_ident}_{v}")), completely untouched
    by the retry fallback changes.
    """
    df = pl.DataFrame({"user_id": ["101", "102", "103"]})
    col_mappings = [
        {
            "target_column_name": "id",
            "transformation_type": "type_cast",
            "is_primary_key": True,
            "target_data_type": "uuid",
            "source_columns": [{"identifier": "src_db_1", "column_name": "user_id"}],
        }
    ]

    res, errors = ASTTransformer.transform_chunk(
        df,
        col_mappings,
        retry_seed_prefix="job123:users:src_db_1:users",
        row_offset=0,
    )
    assert errors == 0

    expected_uuids = [
        str(uuid.uuid5(uuid.NAMESPACE_DNS, f"src_db_1_{v}"))
        for v in ["101", "102", "103"]
    ]
    assert res["id"].to_list() == expected_uuids

    # Backward compatibility: verify calling without retry_seed_prefix produces the same
    res_compat, _ = ASTTransformer.transform_chunk(df, col_mappings)
    assert res_compat["id"].to_list() == expected_uuids


def test_actual_bug_scenario_retry_no_duplicates_unresolved_and_rekey(tmp_path):
    """
    The actual bug scenario:
    Simulates a partial migration failure and retry.
    1. A chunk of rows is transformed using fallback/rekey and bulk loaded into target SQLite table.
    2. Migration is 'killed' before completion.
    3. Re-running the exact same job_id and offset produces identical UUIDs.
    4. Target table with PRIMARY KEY / ON CONFLICT DO NOTHING (INSERT OR IGNORE)
       safely ignores re-submitted rows, preventing duplicate row count growth.
    """
    db_file = str(tmp_path / "target_test.db")
    db_url = f"sqlite:///{db_file}"

    # Create target table with PRIMARY KEY (id)
    with sqlite3.connect(db_file) as conn:
        conn.execute("CREATE TABLE users (id TEXT PRIMARY KEY, email TEXT);")

    job_id = "job_retry_test_999"
    target_table = "users"
    src_ident = "source_crm"
    src_table = "legacy_users"
    seed_prefix = f"{job_id}:{target_table}:{src_ident}:{src_table}"

    # Case A: uuid_v4_rekey strategy
    df_chunk1 = pl.DataFrame({"raw_id": [1, 2, 3, 4, 5], "email": [f"user{i}@example.com" for i in range(1, 6)]})
    df_chunk2 = pl.DataFrame({"raw_id": [6, 7, 8, 9, 10], "email": [f"user{i}@example.com" for i in range(6, 11)]})

    cm_rekey = [
        {
            "target_column_name": "id",
            "transformation_type": "type_cast",
            "is_primary_key": True,
            "primary_key_strategy": "uuid_v4_rekey",
            "target_data_type": "uuid",
            "source_columns": [{"identifier": src_ident, "column_name": "raw_id"}],
        },
        {
            "target_column_name": "email",
            "transformation_type": "direct_copy",
            "source_columns": [{"identifier": src_ident, "column_name": "email"}],
        },
    ]

    # Run Chunk 1 (offset=0)
    res_c1, _ = ASTTransformer.transform_chunk(
        df_chunk1, cm_rekey, primary_key_strategy="uuid_v4_rekey",
        retry_seed_prefix=seed_prefix, row_offset=0
    )
    chunk1_uuids_first_run = res_c1["id"].to_list()
    assert len(chunk1_uuids_first_run) == 5

    # Bulk load chunk 1 into target DB
    succ, fail, skip = TargetWriterFactory.bulk_load(db_url, "sqlite", target_table, res_c1)
    assert succ == 5
    assert skip == 0

    with sqlite3.connect(db_file) as conn:
        count = conn.execute("SELECT COUNT(*) FROM users;").fetchone()[0]
        assert count == 5

    # --- SIMULATE CRASH & RETRY OF SAME JOB ---
    # Re-running the same migration job from the beginning (or re-processing chunk 1)
    res_c1_retry, _ = ASTTransformer.transform_chunk(
        df_chunk1, cm_rekey, primary_key_strategy="uuid_v4_rekey",
        retry_seed_prefix=seed_prefix, row_offset=0
    )
    chunk1_uuids_retry = res_c1_retry["id"].to_list()

    # The UUIDs MUST be 100% identical on retry
    assert chunk1_uuids_retry == chunk1_uuids_first_run

    # Bulk load chunk 1 again into target DB
    succ_retry, fail_retry, skip_retry = TargetWriterFactory.bulk_load(db_url, "sqlite", target_table, res_c1_retry)
    assert succ_retry == 0  # All 5 rows should be ignored due to identical PK!
    assert skip_retry == 5

    # Target table row count must NOT grow
    with sqlite3.connect(db_file) as conn:
        count_after_retry = conn.execute("SELECT COUNT(*) FROM users;").fetchone()[0]
        assert count_after_retry == 5

    # Now process Chunk 2 (offset=5)
    res_c2, _ = ASTTransformer.transform_chunk(
        df_chunk2, cm_rekey, primary_key_strategy="uuid_v4_rekey",
        retry_seed_prefix=seed_prefix, row_offset=5
    )
    succ_c2, _, _ = TargetWriterFactory.bulk_load(db_url, "sqlite", target_table, res_c2)
    assert succ_c2 == 5

    with sqlite3.connect(db_file) as conn:
        final_count = conn.execute("SELECT COUNT(*) FROM users;").fetchone()[0]
        assert final_count == 10  # Exactly 10 rows, zero duplicates


def test_actual_bug_scenario_unresolved_column_fallback():
    """
    Verify that when target column 'id' cannot be resolved from source data,
    it falls back to _deterministic_fallback_uuid, producing identical UUIDs across retries.
    """
    df = pl.DataFrame({"name": ["Alice", "Bob"]})
    cm = [
        {
            "target_column_name": "id",
            "transformation_type": "direct_copy",
            "is_primary_key": True,
            "source_columns": [],  # Unresolvable
        },
        {
            "target_column_name": "name",
            "transformation_type": "direct_copy",
            "source_columns": [{"column_name": "name"}],
        },
    ]

    seed_prefix = "job_abc:users:src1:raw_users"
    res1, _ = ASTTransformer.transform_chunk(df, cm, retry_seed_prefix=seed_prefix, row_offset=100)
    res2, _ = ASTTransformer.transform_chunk(df, cm, retry_seed_prefix=seed_prefix, row_offset=100)

    # Identical across runs with same seed prefix & offset
    assert res1["id"].to_list() == res2["id"].to_list()
    assert res1["id"].to_list() == [
        _deterministic_fallback_uuid(seed_prefix, 100),
        _deterministic_fallback_uuid(seed_prefix, 101),
    ]


def test_collision_scenario_multisource_merge_at_same_offset(tmp_path):
    """
    Multi-source merge collision scenario:
    2 source tables merge into 1 target table. Both sources hit the fallback UUID
    path at the EXACT SAME row offset (e.g. row_offset=0).
    Verify that both rows produce DIFFERENT UUIDs and successfully insert into
    the target table without colliding or deduplicating each other out.
    """
    db_file = str(tmp_path / "target_collision_test.db")
    db_url = f"sqlite:///{db_file}"

    with sqlite3.connect(db_file) as conn:
        conn.execute("CREATE TABLE accounts (id TEXT PRIMARY KEY, username TEXT);")

    job_id = "job_multi_source_merge_42"
    target_table = "accounts"

    # Source 1: PostgreSQL accounts
    src1_ident = "pg_src"
    src1_table = "users"
    seed1 = f"{job_id}:{target_table}:{src1_ident}:{src1_table}"
    df1 = pl.DataFrame({"username": ["alice_pg"]})

    # Source 2: MySQL accounts
    src2_ident = "mysql_src"
    src2_table = "members"
    seed2 = f"{job_id}:{target_table}:{src2_ident}:{src2_table}"
    df2 = pl.DataFrame({"username": ["bob_mysql"]})

    cm_unresolved_pk = [
        {
            "target_column_name": "id",
            "transformation_type": "direct_copy",
            "is_primary_key": True,
            "source_columns": [],  # Both have no PK in source, forcing fallback
        },
        {
            "target_column_name": "username",
            "transformation_type": "direct_copy",
            "source_columns": [{"column_name": "username"}],
        },
    ]

    # Both are at row_offset=0!
    res1, _ = ASTTransformer.transform_chunk(df1, cm_unresolved_pk, retry_seed_prefix=seed1, row_offset=0)
    res2, _ = ASTTransformer.transform_chunk(df2, cm_unresolved_pk, retry_seed_prefix=seed2, row_offset=0)

    id1 = res1["id"][0]
    id2 = res2["id"][0]

    # CRITICAL: Even though row_offset is 0 for both, seeds differ because of source identifier and table
    assert id1 != id2, f"Collision detected! Both sources generated {id1}"

    # Bulk load both into target DB
    succ1, _, _ = TargetWriterFactory.bulk_load(db_url, "sqlite", target_table, res1)
    succ2, _, _ = TargetWriterFactory.bulk_load(db_url, "sqlite", target_table, res2)

    assert succ1 == 1
    assert succ2 == 1

    # Both rows must exist in target database
    with sqlite3.connect(db_file) as conn:
        rows = conn.execute("SELECT id, username FROM accounts ORDER BY username;").fetchall()
        assert len(rows) == 2
        assert rows[0][1] == "alice_pg"
        assert rows[1][1] == "bob_mysql"
        assert rows[0][0] == id1
        assert rows[1][0] == id2
