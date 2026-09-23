"""
DDL Linter Tool for Google ADK Agents
Uses sqlglot to parse and validate DDL statements against target SQL dialects.
"""

import logging
from typing import Any, Dict, List, Optional
import sqlglot
from sqlglot import errors as sqlglot_errors

logger = logging.getLogger(__name__)

DIALECT_MAPPING = {
    "postgresql": "postgres",
    "postgres": "postgres",
    "mysql": "mysql",
    "sqlite": "sqlite",
    "duckdb": "duckdb",
    "mongodb": None,  # MongoDB is NoSQL; DDL validation is skipped or checked for JSON schema
}


def validate_ddl_statement(
    ddl: str,
    target_dialect: str,
    is_pre_migration: bool = True,
) -> Dict[str, Any]:
    """
    Parses and lints a single DDL statement for the given dialect.
    Enforces Phase 1 (pre_migration_ddl) and Phase 2 (post_migration_ddl) hygiene rules.
    """
    clean_ddl = ddl.strip().rstrip(";")
    if not clean_ddl:
        return {"is_valid": True, "statement": ddl, "errors": [], "warnings": []}

    dialect = DIALECT_MAPPING.get(target_dialect.lower(), "postgres")
    if dialect is None:
        # MongoDB does not use SQL DDL
        return {"is_valid": True, "statement": ddl, "errors": [], "warnings": []}

    errors: List[str] = []
    warnings: List[str] = []

    # 1. Syntax parse test
    try:
        parsed = sqlglot.parse(clean_ddl, read=dialect)
        if not parsed:
            errors.append(f"SQL statement could not be parsed: '{ddl}'")
    except sqlglot_errors.ParseError as pe:
        errors.append(f"Syntax error in '{target_dialect}' DDL: {pe}")
    except Exception as exc:
        errors.append(f"Unexpected parsing error: {exc}")

    # 2. Rule Check: gen_random_uuid vs uuid_v4
    upper_ddl = clean_ddl.upper()
    if dialect == "postgres":
        if "UUID_V4()" in upper_ddl or "UUIDV4()" in upper_ddl:
            errors.append("Invalid PostgreSQL default: 'uuid_v4()' does not exist. Use 'gen_random_uuid()' instead.")
    elif dialect == "mysql":
        if "GEN_RANDOM_UUID()" in upper_ddl:
            errors.append("Invalid MySQL default: 'gen_random_uuid()' is not supported in MySQL. Use VARCHAR(36) or BIGINT AUTO_INCREMENT.")

    # 3. Rule Check: Pre-migration DDL must NOT contain inline FOREIGN KEY definitions
    if is_pre_migration:
        if "FOREIGN KEY" in upper_ddl or " REFERENCES " in upper_ddl:
            warnings.append(
                "Pre-migration DDL contains FOREIGN KEY constraint. "
                "Per two-phase DDL hygiene, foreign keys should be deferred to post_migration_ddl."
            )

    return {
        "is_valid": len(errors) == 0,
        "statement": ddl,
        "errors": errors,
        "warnings": warnings,
    }


def validate_ddl_batch(
    statements: List[str],
    target_dialect: str,
    is_pre_migration: bool = True,
) -> Dict[str, Any]:
    """Validates an entire array of DDL statements."""
    all_errors: List[str] = []
    all_warnings: List[str] = []
    results = []

    for stmt in statements:
        res = validate_ddl_statement(stmt, target_dialect, is_pre_migration=is_pre_migration)
        results.append(res)
        if not res["is_valid"]:
            all_errors.extend(res["errors"])
        all_warnings.extend(res["warnings"])

    return {
        "is_valid": len(all_errors) == 0,
        "total_statements": len(statements),
        "errors": all_errors,
        "warnings": all_warnings,
        "details": results,
    }
