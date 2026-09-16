"""
Target database loader module for PostgreSQL, MySQL, SQLite, and MongoDB.
"""

import logging
from typing import Tuple
import polars as pl
from sqlalchemy import text
from ..db import _get_engine, _quote_identifier

logger = logging.getLogger("docker-agent-execution")


def _sanitize_rows_for_target(rows: list, engine_type: str) -> list:
    """
    Sanitizes Python object types in row dicts before DB binding.
    Handles every Python type that SQLAlchemy cannot natively adapt:
    - uuid.UUID           → str
    - datetime            → ISO-8601 string
    - Decimal             → str (preserves precision)
    - bytes               → hex string
    - set / frozenset     → sorted list, then JSON / pg-array
    - dict / list         → JSON string (MySQL/SQLite) or pg-array literal (PostgreSQL lists)
    - MongoDB: dicts/lists → native Python objects (pymongo handles them)
    """
    import json
    import re
    import uuid
    from datetime import datetime, timezone
    from decimal import Decimal

    if not rows:
        return rows

    SQL_NOW_LITERALS = frozenset({
        "CURRENT_TIMESTAMP",
        "CURRENT_TIMESTAMP()",
        "NOW()",
        "NOW",
        "CURRENT_DATE",
        "CURRENT_DATE()",
        "CURRENT_TIME",
        "CURRENT_TIME()",
        "LOCALTIMESTAMP",
        "LOCALTIME",
        "GETDATE()",
        "GETDATE",
        "SYSDATE",
        "SYSDATETIME()",
        "UTC_TIMESTAMP",
        "UTC_TIMESTAMP()",
    })

    engine_type_clean = (engine_type or "").lower().strip()
    is_mongo = engine_type_clean in ("mongodb", "mongo")
    is_postgres = "postgres" in engine_type_clean

    def _pg_array_literal(lst: list) -> str:
        """Format a Python list as a PostgreSQL array literal: {"a","b","c"}."""
        def _escape(elem):
            s = str(elem).replace("\\", "\\\\").replace('"', '\\"')
            return f'"{s}"'
        return "{" + ",".join(_escape(e) for e in lst) + "}"

    sanitized = []
    for r in rows:
        clean_row = {}
        for k, v in r.items():
            if v is None:
                clean_row[k] = None
            elif isinstance(v, str):
                v_str = v.strip()
                v_upper = v_str.upper()
                if v_upper in SQL_NOW_LITERALS:
                    clean_row[k] = datetime.now(timezone.utc) if is_mongo else datetime.now(timezone.utc).isoformat()
                elif v_str in ("0000-00-00 00:00:00", "0000-00-00"):
                    clean_row[k] = None
                elif is_mongo and re.match(r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}", v_str):
                    try:
                        clean_row[k] = datetime.fromisoformat(v_str.replace("Z", "+00:00"))
                    except Exception:
                        clean_row[k] = v
                elif is_mongo and re.match(r"^-?\d+\.\d+$", v_str):
                    try:
                        from bson import Decimal128
                        clean_row[k] = Decimal128(v_str)
                    except Exception:
                        clean_row[k] = float(v_str)
                else:
                    clean_row[k] = v
            elif isinstance(v, uuid.UUID):
                clean_row[k] = str(v)
            elif isinstance(v, datetime):
                if is_mongo:
                    clean_row[k] = v
                else:
                    clean_row[k] = v.isoformat()
            elif isinstance(v, Decimal):
                if is_mongo:
                    try:
                        from bson import Decimal128
                        clean_row[k] = Decimal128(str(v))
                    except Exception:
                        clean_row[k] = float(v)
                else:
                    clean_row[k] = str(v)
            elif isinstance(v, bytes):
                clean_row[k] = v.hex()
            elif isinstance(v, (set, frozenset)):
                lst = sorted(str(e) for e in v)
                if is_mongo:
                    clean_row[k] = lst
                elif is_postgres:
                    clean_row[k] = _pg_array_literal(lst)
                else:
                    clean_row[k] = json.dumps(lst, ensure_ascii=False, default=str)
            elif isinstance(v, list):
                if is_mongo:
                    clean_row[k] = v
                elif is_postgres:
                    clean_row[k] = _pg_array_literal(v)
                else:
                    clean_row[k] = json.dumps(v, ensure_ascii=False, default=str)
            elif isinstance(v, dict):
                if is_mongo:
                    clean_row[k] = v
                else:
                    clean_row[k] = json.dumps(v, ensure_ascii=False, default=str)
            else:
                clean_row[k] = v

        # For MongoDB targets: promote 'id' to '_id' so documents only have one _id primary key
        if is_mongo and "id" in clean_row and "_id" not in clean_row:
            clean_row["_id"] = clean_row.pop("id")

        sanitized.append(clean_row)
    return sanitized



class TargetWriterFactory:
    """Bulk-inserts transformed Polars DataFrames into PostgreSQL, MySQL, or MongoDB target databases."""

    @staticmethod
    def bulk_load(db_url: str, engine_type: str, table_name: str, df: pl.DataFrame) -> Tuple[int, int, int]:
        """
        Bulk loads a DataFrame into target DB.
        Returns a tuple of (successful_rows, failed_rows, skipped_rows).
        """
        if hasattr(df, "is_empty"):
            if df.is_empty():
                return 0, 0, 0
            rows = df.to_dicts()
        elif isinstance(df, list):
            if not df:
                return 0, 0, 0
            rows = df
        else:
            rows = list(df)
        if not rows:
            return 0, 0, 0

        rows = _sanitize_rows_for_target(rows, engine_type)

        successful_rows = 0
        failed_rows = 0
        skipped_rows = 0

        if engine_type in ["mongodb", "mongo"]:
            try:
                import importlib
                pymongo = importlib.import_module("pymongo")
                try:
                    client = pymongo.MongoClient(db_url)
                    client.admin.command('ping')
                except pymongo.errors.OperationFailure:
                    # Fallback to unauthenticated MongoDB connection if host server has auth disabled
                    clean_url = db_url.split("@")[-1] if "@" in db_url else db_url
                    if not clean_url.startswith("mongodb://") and not clean_url.startswith("mongodb+srv://"):
                        clean_url = f"mongodb://{clean_url}"
                    clean_url = clean_url.split("?")[0]
                    client = pymongo.MongoClient(clean_url)

                db_name = db_url.rsplit("/", 1)[-1].split("?")[0] or "target_db"
                db = client[db_name]
                coll = db[table_name]

                res = coll.insert_many(rows, ordered=False)
                successful_rows = len(res.inserted_ids)
                logger.info(f"Bulk-inserted {successful_rows} documents into MongoDB collection '{table_name}'.")
                return successful_rows, 0, 0
            except Exception as exc:
                is_bulk_err = False
                try:
                    import importlib
                    pymongo = importlib.import_module("pymongo")
                    if isinstance(exc, pymongo.errors.BulkWriteError):
                        is_bulk_err = True
                except Exception:
                    pass

                if is_bulk_err or (hasattr(exc, "details") and isinstance(getattr(exc, "details"), dict)):
                    details = getattr(exc, "details", {})
                    write_errors = details.get("writeErrors", [])
                    duplicate_skips = sum(1 for err in write_errors if err.get("code") == 11000)
                    failed_rows = len(write_errors) - duplicate_skips
                    n_inserted = details.get("nInserted", 0)
                    successful_rows = max(0, n_inserted)
                    skipped_rows = duplicate_skips
                    logger.warning(
                        f"MongoDB partial bulk insert notice for collection '{table_name}': "
                        f"{successful_rows} successful, {failed_rows} failed, {skipped_rows} skipped (duplicate keys)."
                    )
                    return successful_rows, failed_rows, skipped_rows

                logger.error(f"MongoDB bulk insert error for collection '{table_name}': {exc}")
                return 0, len(rows), 0

        # 2. PostgreSQL, MySQL & SQLite Target Writer
        else:
            engine = _get_engine(db_url)

            # Fast connectivity check: distinguish "target DB is completely
            # unreachable" (auth failure, wrong host, network down) from a
            # per-row data/schema problem. Without this check, a dead target
            # falls into the slow per-row retry loop below and produces a
            # misleading "verify target schema" error instead of the real
            # connection failure.
            try:
                with engine.connect() as _test_conn:
                    pass
            except Exception as conn_exc:
                logger.error(f"Cannot connect to target database for table '{table_name}': {conn_exc}")
                raise RuntimeError(
                    f"Target database connection failed for table '{table_name}': {conn_exc}. "
                    f"Verify the target DB URL, credentials, and network reachability."
                ) from conn_exc

            columns = list(rows[0].keys())

            quoted_table = _quote_identifier(table_name, engine_type)

            # Auto-align missing target columns (e.g. _source_origin lineage column)
            try:
                with engine.begin() as col_conn:
                    for col in columns:
                        q_col = _quote_identifier(col, engine_type)
                        if "postgres" in engine_type:
                            col_conn.execute(text(f'ALTER TABLE {quoted_table} ADD COLUMN IF NOT EXISTS {q_col} TEXT;'))
                        elif "mysql" in engine_type:
                            try:
                                col_conn.execute(text(f'ALTER TABLE {quoted_table} ADD COLUMN {q_col} TEXT;'))
                            except Exception:
                                pass
            except Exception:
                pass

            quoted_cols = [_quote_identifier(c, engine_type) for c in columns]
            col_names = ", ".join(quoted_cols)
            placeholders = ", ".join([f":{c}" for c in columns])

            if "mysql" in engine_type:
                insert_sql = f'INSERT IGNORE INTO {quoted_table} ({col_names}) VALUES ({placeholders});'
            elif "sqlite" in engine_type:
                insert_sql = f'INSERT OR IGNORE INTO {quoted_table} ({col_names}) VALUES ({placeholders});'
            else:
                insert_sql = f'INSERT INTO {quoted_table} ({col_names}) VALUES ({placeholders}) ON CONFLICT DO NOTHING;'

            try:
                with engine.begin() as conn:
                    if "postgres" in engine_type:
                        try:
                            conn.execute(text("SET session_replication_role = 'replica';"))
                        except Exception:
                            pass
                    try:
                        result = conn.execute(text(insert_sql), rows)
                        raw_rowcount = getattr(result, "rowcount", -1)
                        if raw_rowcount >= 0:
                            successful_rows = raw_rowcount
                            skipped_rows = max(0, len(rows) - successful_rows)
                        else:
                            successful_rows, skipped_rows = len(rows), 0
                    finally:
                        if "postgres" in engine_type:
                            try:
                                conn.execute(text("SET session_replication_role = 'origin';"))
                            except Exception:
                                pass

                logger.info(
                    f"Bulk-inserted {successful_rows} rows into target table '{table_name}' "
                    f"(Skipped due to conflict: {skipped_rows})."
                )
                return successful_rows, 0, skipped_rows
            except Exception as exc:
                logger.warning(f"Batch bulk insert notice for table '{table_name}': {exc}. Retrying per-row insertion...")
                # Fallback to per-row insertion with sample error capping and 50% abort threshold
                MAX_SAMPLE_ERRORS = 100
                ABORT_THRESHOLD_PERCENT = 0.50
                threshold_sample_size = min(1000, max(5, len(rows) // 2))

                for idx, row in enumerate(rows):
                    try:
                        with engine.begin() as conn:
                            if "postgres" in engine_type:
                                try:
                                    conn.execute(text("SET session_replication_role = 'replica';"))
                                except Exception:
                                    pass
                            try:
                                res_row = conn.execute(text(insert_sql), [row])
                                r_cnt = getattr(res_row, "rowcount", -1)
                                if r_cnt == 0:
                                    skipped_rows += 1
                                else:
                                    successful_rows += 1
                            finally:
                                if "postgres" in engine_type:
                                    try:
                                        conn.execute(text("SET session_replication_role = 'origin';"))
                                    except Exception:
                                        pass
                    except Exception as row_exc:
                        failed_rows += 1
                        if failed_rows <= MAX_SAMPLE_ERRORS:
                            logger.warning(f"Row insertion notice in table '{table_name}' (Row #{idx}): {row_exc}")

                        if (idx + 1) >= threshold_sample_size and (failed_rows / (idx + 1)) > ABORT_THRESHOLD_PERCENT:
                            raise RuntimeError(
                                f"Migration aborted for table '{table_name}': Error rate exceeded {ABORT_THRESHOLD_PERCENT*100:.0f}% "
                                f"({failed_rows}/{idx+1} rows failed). Please verify target schema and column mapping specs."
                            )

                return successful_rows, failed_rows, skipped_rows
