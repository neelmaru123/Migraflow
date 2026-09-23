"""
Type Compatibility Tool for Google ADK Agents
Validates data type compatibility and recommends transformation types for cross-database migrations.
"""

from typing import Any, Dict, Optional

# Known type groups for cross-database mappings
NUMERIC_TYPES = {"int", "integer", "smallint", "bigint", "serial", "bigserial", "numeric", "decimal", "float", "double", "real"}
TEXT_TYPES = {"varchar", "char", "text", "string", "nvarchar", "tinytext", "mediumtext", "longtext"}
DATETIME_TYPES = {"timestamp", "timestamptz", "datetime", "date", "time", "timetz"}
BOOLEAN_TYPES = {"bool", "boolean", "tinyint(1)"}
JSON_TYPES = {"json", "jsonb", "document"}
BINARY_TYPES = {"bytea", "blob", "binary", "varbinary"}
UUID_TYPES = {"uuid", "varchar(36)", "char(36)", "string"}


def check_type_compatibility(
    source_type: str,
    target_type: str,
    source_dialect: str = "postgresql",
    target_dialect: str = "postgresql",
) -> Dict[str, Any]:
    """
    Checks compatibility between a source column data type and a proposed target data type.
    Returns recommendations, warnings, and the appropriate transformation_type.
    """
    s_clean = source_type.lower().strip()
    t_clean = target_type.lower().strip()

    # Extract base type name (strip parentheses and arguments)
    s_base = s_clean.split("(")[0].strip()
    t_base = t_clean.split("(")[0].strip()

    # Exact match
    if s_clean == t_clean or s_base == t_base:
        return {
            "compatible": True,
            "recommended_transformation": "direct_copy",
            "is_lossless": True,
            "notes": f"Direct compatible types: '{source_type}' -> '{target_type}'",
        }

    # UUID / String conversions
    if (s_base in UUID_TYPES or "uuid" in s_clean) and (t_base in UUID_TYPES or "uuid" in t_clean):
        return {
            "compatible": True,
            "recommended_transformation": "type_cast",
            "is_lossless": True,
            "notes": "UUID representation conversion",
        }

    # Numeric conversions (widening vs narrowing)
    if s_base in NUMERIC_TYPES and t_base in NUMERIC_TYPES:
        is_widening = (
            ("int" in s_base and "bigint" in t_base)
            or ("smallint" in s_base and "int" in t_base)
            or ("int" in s_base and "decimal" in t_base)
        )
        return {
            "compatible": True,
            "recommended_transformation": "type_cast",
            "is_lossless": is_widening,
            "notes": "Numeric cast: check precision/scale boundaries" if not is_widening else "Safe widening cast",
        }

    # DateTime conversions
    if s_base in DATETIME_TYPES and t_base in DATETIME_TYPES:
        return {
            "compatible": True,
            "recommended_transformation": "type_cast",
            "is_lossless": True,
            "notes": "Datetime/timestamp dialect cast",
        }

    # JSON to SQL / Flattening
    if s_base in JSON_TYPES or "json" in s_clean:
        if t_base in TEXT_TYPES:
            return {
                "compatible": True,
                "recommended_transformation": "json_stringify",
                "is_lossless": True,
                "notes": "Serialize JSON/JSONB object to text",
            }
        return {
            "compatible": True,
            "recommended_transformation": "json_flatten",
            "is_lossless": True,
            "notes": "Flatten JSON fields into structured columns",
        }

    # PostgreSQL Array to CSV/JSON
    if "[]" in s_clean or "array" in s_clean:
        if t_base in TEXT_TYPES:
            return {
                "compatible": True,
                "recommended_transformation": "array_to_csv",
                "is_lossless": True,
                "notes": "Decompose array to comma-separated string",
            }
        return {
            "compatible": True,
            "recommended_transformation": "array_to_json",
            "is_lossless": True,
            "notes": "Serialize array to JSON array string",
        }

    # General type cast
    return {
        "compatible": True,
        "recommended_transformation": "type_cast",
        "is_lossless": False,
        "notes": f"Potential lossy or custom cast between '{source_type}' and '{target_type}'",
    }
