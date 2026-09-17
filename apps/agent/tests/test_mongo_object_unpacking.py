import pytest
import json
import uuid
from datetime import datetime, timezone
from decimal import Decimal
try:
    from bson import Decimal128
except ImportError:
    Decimal128 = None

from engine.writers.target_writer import _sanitize_rows_for_target


def test_mongodb_target_generic_json_unpacking_and_bson_types():
    """
    Verifies that when target is MongoDB, any column containing serialized JSON
    is parsed to native Python dicts/lists and converted to BSON types.
    """
    rows = [
        {
            "id": "item-001",
            "name": "Widget A",
            "metadata_json": '{"color": "blue", "dimensions": {"length": 10.5, "width": 5.2}}',
            "tags_json": '["industrial", "sensor", "v2"]',
            "created_at": "2026-09-17T10:30:00Z",
            "price": "99.95",
            "raw_decimal": Decimal("45.50"),
        }
    ]

    sanitized = _sanitize_rows_for_target(rows, "mongodb")
    assert len(sanitized) == 1
    doc = sanitized[0]

    # 1. Primary key promoted
    assert "_id" in doc
    assert doc["_id"] == "item-001"
    assert "id" not in doc

    # 2. Generic JSON column parsed to dict
    assert isinstance(doc["metadata_json"], dict)
    assert doc["metadata_json"]["color"] == "blue"
    assert isinstance(doc["metadata_json"]["dimensions"], dict)
    assert doc["metadata_json"]["dimensions"]["length"] == 10.5

    # 3. Generic JSON array parsed to list
    assert isinstance(doc["tags_json"], list)
    assert doc["tags_json"] == ["industrial", "sensor", "v2"]

    # 4. Datetime converted
    assert isinstance(doc["created_at"], datetime)

    # 5. Decimal128 converted
    assert isinstance(doc["price"], Decimal128)
    assert isinstance(doc["raw_decimal"], Decimal128)


def test_mongodb_target_postgres_source_residual_promotion():
    """
    Simulates PostgreSQL source with JSONB 'extra_attributes' column.
    Verifies that residual attributes are promoted to the root document.
    """
    pg_row = {
        "id": "c7a6e768-3d84-4d87-9759-8692c3004351",
        "hardware_vin": "VIN-HEX-E54156C615BC",
        "model": "AeroDrone-X9",
        "operational_status": "ACTIVE",
        "manufacture_date": datetime(2026, 7, 20, 5, 54, 38, tzinfo=timezone.utc),
        "asset_cost_usd": Decimal("45325.50"),
        "extra_attributes": json.dumps({
            "current_telemetry_location": {"type": "Point", "coordinates": [-122.4194, 37.7749]},
            "architecture": {
                "firmware_version": "v3.2.1",
                "subsystems": {"powertrain": {"bus_standard": "CAN-FD-Bus-Extended"}}
            }
        }),
        "created_at": datetime.now(timezone.utc),
        "updated_at": datetime.now(timezone.utc),
    }

    sanitized = _sanitize_rows_for_target([pg_row], "mongodb")
    assert len(sanitized) == 1
    doc = sanitized[0]

    # extra_attributes wrapper removed
    assert "extra_attributes" not in doc

    # Sub-objects promoted to root as native dictionaries
    assert "current_telemetry_location" in doc
    assert isinstance(doc["current_telemetry_location"], dict)
    assert doc["current_telemetry_location"]["type"] == "Point"

    assert "architecture" in doc
    assert isinstance(doc["architecture"], dict)
    assert doc["architecture"]["firmware_version"] == "v3.2.1"
    assert doc["architecture"]["subsystems"]["powertrain"]["bus_standard"] == "CAN-FD-Bus-Extended"

    # Core columns intact
    assert doc["_id"] == "c7a6e768-3d84-4d87-9759-8692c3004351"
    assert doc["model"] == "AeroDrone-X9"
    assert isinstance(doc["asset_cost_usd"], Decimal128)


def test_mongodb_target_mysql_source_multiple_json_columns():
    """
    Simulates MySQL source with multiple JSON / TEXT columns (e.g. user_profile, event_payload).
    Verifies that multiple object columns are parsed without requiring 'extra_attributes'.
    """
    mysql_row = {
        "id": "usr_99812",
        "username": "johndoe",
        "user_profile": '{"preferences": {"theme": "dark", "notifications": true}, "bio": "Data Engineer"}',
        "device_telemetry": '{"os": "Linux", "screen": [1920, 1080], "client_version": "2.4.0"}',
        "login_count": 42,
    }

    sanitized = _sanitize_rows_for_target([mysql_row], "mongodb")
    assert len(sanitized) == 1
    doc = sanitized[0]

    assert doc["_id"] == "usr_99812"
    assert isinstance(doc["user_profile"], dict)
    assert doc["user_profile"]["preferences"]["theme"] == "dark"
    assert doc["user_profile"]["preferences"]["notifications"] is True

    assert isinstance(doc["device_telemetry"], dict)
    assert doc["device_telemetry"]["os"] == "Linux"
    assert doc["device_telemetry"]["screen"] == [1920, 1080]


def test_sql_target_safety_intact():
    """
    Ensures that when target is PostgreSQL or MySQL, dicts/lists are converted
    to valid SQL strings/arrays without modifying SQL target behaviour.
    """
    row = {
        "id": "123",
        "attributes": {"color": "red", "size": "large"},
        "tags": ["tag1", "tag2"],
    }

    # PostgreSQL target
    pg_sanitized = _sanitize_rows_for_target([row], "postgresql")
    assert pg_sanitized[0]["attributes"] == '{"color": "red", "size": "large"}'
    assert pg_sanitized[0]["tags"] == '{"tag1","tag2"}'
    assert "id" in pg_sanitized[0]

    # MySQL target
    mysql_sanitized = _sanitize_rows_for_target([row], "mysql")
    assert mysql_sanitized[0]["attributes"] == '{"color": "red", "size": "large"}'
    assert mysql_sanitized[0]["tags"] == '["tag1", "tag2"]'
    assert "id" in mysql_sanitized[0]
