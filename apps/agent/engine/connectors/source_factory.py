"""
Source connector factory module supporting chunked streaming for SQL databases, MongoDB, CSV, and Excel with Keyset Pagination.
"""

import logging
import os
from typing import Any, Optional, Tuple
import polars as pl
from sqlalchemy import text
from ..db import _get_engine, _quote_identifier

logger = logging.getLogger("docker-agent-execution")


class SourceReadError(Exception):
    """Raised when a source chunk genuinely cannot be read."""


class SourceConnectorFactory:
    """Factory creating chunked stream readers for PostgreSQL, MySQL, MongoDB, CSV, and Excel with Keyset Pagination support."""

    @staticmethod
    def read_source_chunk(
        db_url: str,
        engine_type: str,
        table_or_file_name: str,
        offset: int = 0,
        chunk_size: int = 50000,
        pk_col: Optional[str] = None,
        last_pk_val: Optional[Any] = None,
    ) -> Tuple[pl.DataFrame, bool, Optional[Any]]:
        """
        Reads a chunk of rows from the source database using Polars or SQLAlchemy streaming.
        Supports Keyset Pagination (WHERE pk > last_pk ORDER BY pk) to eliminate offset drift on live DBs.
        Returns a tuple of (DataFrame, has_more_rows, next_last_pk_val).
        """
        engine_type = engine_type.lower()
        supported_dialects = ["postgresql", "postgres", "mysql", "mariadb", "sqlite", "mongodb", "mongo", "csv", "excel", "xlsx"]
        if engine_type not in supported_dialects:
            raise ValueError(
                f"Unsupported database engine dialect '{engine_type}'. "
                f"Supported dialects: postgresql, mysql, sqlite, mongodb, csv, excel."
            )

        quoted_table = _quote_identifier(table_or_file_name, engine_type)

        # 1. SQL Relational Databases (PostgreSQL, MySQL, SQLite)
        if engine_type in ["postgresql", "postgres", "mysql", "mariadb", "sqlite"]:
            try:
                import json
                import uuid

                engine = _get_engine(db_url)
                if not pk_col and offset == 0:
                    logger.warning(
                        f"Source table '{table_or_file_name}' does not specify a primary key column. "
                        f"Falling back to OFFSET pagination which may suffer from offset drift if source table undergoes concurrent writes."
                    )

                def _execute_sql_to_polars(query_str: str, query_params: dict) -> pl.DataFrame:
                    with engine.connect() as conn:
                        res = conn.execute(text(query_str), query_params)
                        cols = list(res.keys())
                        rows = res.fetchall()
                        if not rows:
                            return pl.DataFrame(schema={c: pl.Utf8 for c in cols})
                        cleaned_rows = []
                        for r in rows:
                            row_dict = {}
                            for col, val in zip(cols, r):
                                if isinstance(val, (dict, list)):
                                    row_dict[col] = json.dumps(val, default=str)
                                elif isinstance(val, uuid.UUID):
                                    row_dict[col] = str(val)
                                elif isinstance(val, (bytes, bytearray)):
                                    try:
                                        row_dict[col] = val.decode("utf-8")
                                    except Exception:
                                        row_dict[col] = val.hex()
                                else:
                                    row_dict[col] = val
                            cleaned_rows.append(row_dict)
                        try:
                            df_sql = pl.DataFrame(cleaned_rows, strict=False)
                        except TypeError:
                            df_sql = pl.DataFrame(cleaned_rows)
                        if not df_sql.is_empty() and any(dt == pl.Object for dt in df_sql.schema.values()):
                            for c, dt in df_sql.schema.items():
                                if dt == pl.Object:
                                    vals = [str(x) if x is not None else None for x in df_sql[c].to_list()]
                                    df_sql = df_sql.with_columns(pl.Series(c, vals, dtype=pl.Utf8))
                        return df_sql

                if pk_col and last_pk_val is not None:
                    quoted_pk = _quote_identifier(pk_col, engine_type)
                    query = f"SELECT * FROM {quoted_table} WHERE {quoted_pk} > :last_pk ORDER BY {quoted_pk} ASC LIMIT {chunk_size}"
                    try:
                        df = _execute_sql_to_polars(query, {"last_pk": last_pk_val})
                    except Exception as pk_err:
                        logger.warning(f"Keyset pagination error on '{quoted_table}': {pk_err}. Falling back to OFFSET.")
                        fallback_query = f"SELECT * FROM {quoted_table} LIMIT {chunk_size} OFFSET {offset}"
                        df = _execute_sql_to_polars(fallback_query, {})
                else:
                    quoted_pk = _quote_identifier(pk_col, engine_type) if pk_col else None
                    order_by_clause = f" ORDER BY {quoted_pk} ASC" if quoted_pk else ""
                    query = f"SELECT * FROM {quoted_table}{order_by_clause} LIMIT {chunk_size} OFFSET {offset}"
                    df = _execute_sql_to_polars(query, {})

                has_more = len(df) == chunk_size
                next_pk = None
                if not df.is_empty() and pk_col and pk_col in df.columns:
                    next_pk = df[pk_col][-1]

                return df, has_more, next_pk
            except Exception as exc:
                logger.error(f"Error reading SQL source chunk for table '{table_or_file_name}' (Offset: {offset}): {exc}")
                raise SourceReadError(f"Failed reading SQL source table '{table_or_file_name}' at offset {offset}: {exc}") from exc

        # 2. MongoDB Collection
        elif engine_type == "mongodb":
            try:
                from pymongo import MongoClient
                from pymongo.errors import OperationFailure
                from bson import ObjectId

                def _mongo_client(url: str) -> MongoClient:
                    """Connect to MongoDB, falling back to unauthenticated if auth fails."""
                    try:
                        c = MongoClient(url, serverSelectionTimeoutMS=5000)
                        c.admin.command("ping")
                        return c
                    except OperationFailure:
                        # Strip credentials and authSource for no-auth deployments
                        clean = url.split("@")[-1] if "@" in url else url
                        if not clean.startswith("mongodb://") and not clean.startswith("mongodb+srv://"):
                            clean = f"mongodb://{clean}"
                        clean = clean.split("?")[0]
                        logger.info(
                            f"[SourceFactory] MongoDB auth failed for '{table_or_file_name}', "
                            f"retrying without credentials."
                        )
                        return MongoClient(clean, serverSelectionTimeoutMS=5000)

                client = _mongo_client(db_url)
                db_name = db_url.rsplit("/", 1)[-1].split("?")[0] or "test"
                db = client[db_name]

                query = {}
                if last_pk_val is not None and str(last_pk_val).strip() != "":
                    try:
                        query_id = ObjectId(str(last_pk_val))
                    except Exception:
                        query_id = str(last_pk_val)
                    query = {"_id": {"$gt": query_id}}

                cursor = db[table_or_file_name].find(query).sort("_id", 1).limit(chunk_size)
                docs = list(cursor)
                client.close()

                if not docs:
                    return pl.DataFrame(), False, last_pk_val

                from decimal import Decimal
                from bson import ObjectId, Decimal128
                import json

                def _sanitize_doc(d: dict) -> dict:
                    cleaned = {}
                    for k, v in d.items():
                        if k == "_id":
                            cleaned["_id"] = str(v)
                        elif isinstance(v, (Decimal128, Decimal)):
                            try:
                                cleaned[k] = float(v.to_decimal()) if hasattr(v, "to_decimal") else float(v)
                            except Exception:
                                cleaned[k] = str(v)
                        elif isinstance(v, ObjectId):
                            cleaned[k] = str(v)
                        elif isinstance(v, (bytes, bytearray)):
                            try:
                                cleaned[k] = v.decode("utf-8")
                            except Exception:
                                cleaned[k] = str(v)
                        elif isinstance(v, (dict, list)):
                            cleaned[k] = json.dumps(v, default=str)
                        else:
                            cleaned[k] = v
                    return cleaned

                next_pk = str(docs[-1]["_id"]) if (docs and "_id" in docs[-1]) else last_pk_val
                cleaned_docs = [_sanitize_doc(d) for d in docs]
                try:
                    df = pl.DataFrame(cleaned_docs, strict=False)
                except TypeError:
                    df = pl.DataFrame(cleaned_docs)
                has_more = len(df) == chunk_size
                return df, has_more, next_pk
            except Exception as exc:
                logger.error(f"Error reading MongoDB collection '{table_or_file_name}': {exc}")
                raise SourceReadError(f"Failed reading MongoDB collection '{table_or_file_name}': {exc}") from exc

        # 3. CSV File
        elif engine_type == "csv" or table_or_file_name.endswith(".csv"):
            try:
                if os.path.exists(table_or_file_name):
                    df = pl.read_csv(table_or_file_name, n_rows=chunk_size, skip_rows=offset)
                    has_more = len(df) == chunk_size
                    return df, has_more, None
                return pl.DataFrame(), False, None
            except Exception as exc:
                logger.error(f"Error reading CSV file '{table_or_file_name}': {exc}")
                raise SourceReadError(f"Failed reading CSV file '{table_or_file_name}': {exc}") from exc

        # 4. Excel File
        elif engine_type in ["excel", "xlsx"] or table_or_file_name.endswith(".xlsx"):
            try:
                if os.path.exists(table_or_file_name):
                    df = pl.read_excel(table_or_file_name)
                    df_chunk = df.slice(offset, chunk_size)
                    has_more = (offset + chunk_size) < len(df)
                    return df_chunk, has_more, None
                return pl.DataFrame(), False, None
            except Exception as exc:
                logger.error(f"Error reading Excel file '{table_or_file_name}': {exc}")
                raise SourceReadError(f"Failed reading Excel file '{table_or_file_name}': {exc}") from exc

        else:
            logger.warning(f"Unsupported engine type '{engine_type}'. Returning empty DataFrame.")
            return pl.DataFrame(), False, None
