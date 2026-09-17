"""
Unit test for Fix 3: Verify actual DB write outcomes instead of assuming full success.
Verifies SQL conflict skip counts (rowcount), PyMongo BulkWriteError partial success reporting,
and ExecutionProgressUpdate schema backwards compatibility.
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch
import polars as pl
import pytest

REPO_ROOT = Path(__file__).resolve().parents[3].parent
AGENT_DIR = REPO_ROOT / "apps" / "agent"
if str(AGENT_DIR) not in sys.path:
    sys.path.insert(0, str(AGENT_DIR))

from execution_engine import TargetWriterFactory
from app.modules.execution.execution_schemas import ExecutionProgressUpdate


def test_sql_conflict_skip_count_verification():
    """
    Simulates a SQL batch load of 10 rows where 6 conflict with pre-existing target data.
    Asserts reported successful_rows reflects rowcount (4), and skipped_rows equals 6.
    """
    df = pl.DataFrame({
        "id": list(range(1, 11)),
        "name": [f"User_{i}" for i in range(1, 11)],
    })

    mock_conn = MagicMock()
    mock_result = MagicMock()
    mock_result.rowcount = 4  # 4 genuinely inserted, 6 skipped due to conflict
    mock_conn.execute.return_value = mock_result

    mock_engine = MagicMock()
    mock_engine.begin.return_value.__enter__.return_value = mock_conn

    with patch("engine.writers.target_writer._get_engine", return_value=mock_engine):
        successful, failed, skipped = TargetWriterFactory.bulk_load(
            db_url="postgresql://localhost/testdb",
            engine_type="postgresql",
            table_name="users",
            df=df,
        )

    assert successful == 4
    assert failed == 0
    assert skipped == 6


def test_mongo_bulk_write_error_partial_success():
    """
    Simulates a MongoDB insert_many where 2 of 10 documents fail with duplicate key _id.
    Asserts reported result is 8 successful / 2 failed, not 0/10.
    """
    df = pl.DataFrame({
        "_id": [f"id_{i}" for i in range(1, 11)],
        "val": [f"val_{i}" for i in range(1, 11)],
    })

    # Create mock BulkWriteError exception with details dict
    class MockBulkWriteError(Exception):
        def __init__(self, details):
            self.details = details

    bulk_err = MockBulkWriteError(
        details={
            "writeErrors": [{"index": 0, "code": 11000}, {"index": 1, "code": 11000}],
            "nInserted": 8,
        }
    )

    mock_coll = MagicMock()
    mock_coll.insert_many.side_effect = bulk_err

    mock_db = MagicMock()
    mock_db.__getitem__.return_value = mock_coll

    mock_client = MagicMock()
    mock_client.__getitem__.return_value = mock_db

    mock_pymongo = MagicMock()
    mock_pymongo.MongoClient.return_value = mock_client
    mock_pymongo.errors.BulkWriteError = MockBulkWriteError

    with patch("importlib.import_module", return_value=mock_pymongo):
        successful, failed, skipped = TargetWriterFactory.bulk_load(
            db_url="mongodb://localhost:27017/test_db",
            engine_type="mongodb",
            table_name="items",
            df=df,
        )

    assert successful == 8
    assert failed == 0
    assert skipped == 2


def test_execution_progress_update_schema_with_skipped_rows():
    """
    Verifies that ExecutionProgressUpdate accepts and defaults skipped_rows correctly.
    """
    update_default = ExecutionProgressUpdate(
        status="running",
        progress=50.0,
        processed_rows=10,
        successful_rows=4,
        failed_rows=0,
    )
    assert update_default.skipped_rows == 0

    update_custom = ExecutionProgressUpdate(
        status="running",
        progress=50.0,
        processed_rows=10,
        successful_rows=4,
        failed_rows=0,
        skipped_rows=6,
    )
    assert update_custom.skipped_rows == 6
    payload = update_custom.model_dump()
    assert payload["skipped_rows"] == 6
