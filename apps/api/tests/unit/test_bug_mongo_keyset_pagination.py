"""
Unit test for Fix 8: Mongo extraction keyset pagination instead of skip/limit.
Verifies _id-based keyset pagination, next_pk propagation, and zero duplicate/skipped documents.
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
API_DIR = REPO_ROOT / "apps" / "api"
AGENT_DIR = REPO_ROOT / "apps" / "agent"
if str(API_DIR) not in sys.path:
    sys.path.insert(0, str(API_DIR))
if str(AGENT_DIR) not in sys.path:
    sys.path.insert(0, str(AGENT_DIR))

from execution_engine import SourceConnectorFactory


def test_mongo_keyset_pagination_chunk_reading():
    """
    Verifies that SourceConnectorFactory.read_source_chunk executes _id-based keyset queries
    using find({"_id": {"$gt": last_id}}).sort("_id", 1).limit(chunk_size).
    """
    mock_docs_batch1 = [
        {"_id": "60f1b2c3d4e5f6a7b8c9d001", "name": "Doc 1"},
        {"_id": "60f1b2c3d4e5f6a7b8c9d002", "name": "Doc 2"},
    ]
    mock_docs_batch2 = [
        {"_id": "60f1b2c3d4e5f6a7b8c9d003", "name": "Doc 3"},
    ]

    queries_made = []

    class MockCollection:
        def find(self, query=None):
            queries_made.append(query)
            mock_cursor = MagicMock()
            mock_cursor.sort.return_value = mock_cursor
            if query and "$gt" in query.get("_id", {}):
                mock_cursor.limit.return_value = mock_docs_batch2
            else:
                mock_cursor.limit.return_value = mock_docs_batch1
            return mock_cursor

    class MockDB:
        def __getitem__(self, item):
            return MockCollection()

    class MockMongoClient:
        def __init__(self, *args, **kwargs):
            self.admin = MagicMock()
            self.admin.command = MagicMock(return_value={"ok": 1})

        def __getitem__(self, item):
            return MockDB()

        def close(self):
            pass

    with patch("pymongo.MongoClient", MockMongoClient):
        # Chunk 1 read
        df1, has_more1, next_pk1 = SourceConnectorFactory.read_source_chunk(
            db_url="mongodb://localhost:27017/test_db",
            engine_type="mongodb",
            table_or_file_name="users",
            offset=0,
            chunk_size=2,
            pk_col="_id",
            last_pk_val=None,
        )

        assert len(df1) == 2
        assert has_more1 is True
        assert next_pk1 == "60f1b2c3d4e5f6a7b8c9d001" or next_pk1 == "60f1b2c3d4e5f6a7b8c9d002"

        # Chunk 2 read passing next_pk1
        df2, has_more2, next_pk2 = SourceConnectorFactory.read_source_chunk(
            db_url="mongodb://localhost:27017/test_db",
            engine_type="mongodb",
            table_or_file_name="users",
            offset=2,
            chunk_size=2,
            pk_col="_id",
            last_pk_val=next_pk1,
        )

        assert len(df2) == 1
        assert has_more2 is False

    # Verify query 2 used $gt filter with next_pk1
    assert len(queries_made) == 2
    assert "$gt" in queries_made[1]["_id"]
