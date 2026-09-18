"""
AST column transformation engine applying Polars in-memory batch transformations,
PK resolution strategies, and residual field capture.

Robustness principles:
- _resolve_src_col is the SINGLE source-column resolver used by ALL transformation types.
  It checks, in priority order:
    1. Exact match from AST source_columns list (for the current DF columns)
    2. Exact match using target column name itself
    3. Dynamic fuzzy similarity matching against all actual DF column names
  Any fuzzy resolution is logged so it is fully traceable.
- Primary key / 'id' columns that are still unresolved generate fresh UUIDs instead of NULL.
- Datetime parsing is timezone-aware using explicit format with Utf8 fallback.
"""

import json
import logging
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import duckdb
import polars as pl

logger = logging.getLogger("docker-agent-execution")

_SQL_DATETIME_LITERALS = frozenset({
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


def _is_sql_datetime_expr(val: Any) -> bool:
    if val is None:
        return False
    return str(val).strip().upper() in _SQL_DATETIME_LITERALS


def _deterministic_fallback_uuid(seed_prefix: str, row_index: int) -> str:
    """
    Generates a stable UUID for rows that have no usable natural key to
    hash on. seed_prefix must already uniquely identify
    (job_id, target_table, source_identifier, source_table) so that two
    DIFFERENT source rows merging into the SAME target table never
    collide, even if they share the same row_index. This makes retries
    of a failed/partial migration safe against the target's
    ON CONFLICT DO NOTHING / INSERT IGNORE dedup logic, since the same
    source row always produces the same UUID across retries.
    """
    seed = f"{seed_prefix}:{row_index}"
    return str(uuid.uuid5(uuid.NAMESPACE_DNS, seed))


# ---------------------------------------------------------------------------
# Semantic synonym groups — each group is a set of column-name tokens that
# represent the same business concept. Used by the dynamic fuzzy resolver.
# ---------------------------------------------------------------------------
_SEMANTIC_SYNONYM_GROUPS: List[set] = [
    {"email", "email_address", "contact_email", "mail", "user_email", "reviewer_email", "emailaddress"},
    {"first_name", "fname", "given_name", "firstname", "givenname"},
    {"last_name", "lname", "family_name", "surname", "lastname", "familyname"},
    {"full_name", "fullname", "display_name", "name", "displayname"},
    {"phone", "mobile", "telephone", "phone_number", "contact_number", "phonenumber", "tel"},
    {"created_at", "created_time", "creation_date", "registration_date", "created_date", "createdat", "createdtime"},
    {"updated_at", "updated_time", "last_modified", "modified_at", "updatedat"},
    {"customer_id", "account_id", "user_id", "client_id", "member_id", "customerid", "accountid", "userid"},
    {"product_id", "item_id", "sku_id", "productid", "itemid"},
    {"order_id", "transaction_id", "orderid", "transactionid"},
    {"price", "unit_price", "cost", "amount", "rate", "fee"},
    {"quantity", "qty", "count", "stock", "inventory_count"},
    {"address", "street_address", "billing_address", "shipping_address", "location"},
    {"city", "town", "locality"},
    {"country", "country_code", "nation"},
    {"status", "state", "account_status", "order_status", "is_active"},
    {"description", "desc", "details", "notes", "comment", "remarks"},
    {"category", "category_name", "type", "group", "classification"},
    {"sku", "item_code", "product_code", "part_number"},
    {"tags", "labels", "keywords", "categories"},
]


def _normalize_col(name: str) -> str:
    """Normalize a column name: lowercase, strip underscores and common suffixes."""
    return re.sub(r"[_\-\s]+", "", name.lower())


def _synonym_group_for(name: str) -> Optional[set]:
    """Return the synonym group containing `name`, or None."""
    norm = _normalize_col(name)
    for group in _SEMANTIC_SYNONYM_GROUPS:
        if any(_normalize_col(g) == norm for g in group):
            return group
    return None


class ASTTransformer:
    """Applies all AST column transformation types to a Polars DataFrame in-memory."""

    @staticmethod
    def transform_chunk(
        df: pl.DataFrame,
        column_mappings: List[Dict[str, Any]],
        primary_key_strategy: Optional[str] = None,
        retry_seed_prefix: str = "default_seed",
        row_offset: int = 0,
        source_origin: Optional[str] = None,
    ) -> Tuple[pl.DataFrame, int]:
        if df.is_empty():
            return df, 0

        # Sanitize any Polars Object dtypes (e.g. UUID objects from psycopg2, custom Python objects)
        # to Utf8 strings so that .cast(pl.Utf8), expressions, and downstream mappings operate seamlessly.
        if any(dt == pl.Object for dt in df.schema.values()):
            for c, dt in df.schema.items():
                if dt == pl.Object:
                    vals = [str(x) if x is not None else None for x in df[c].to_list()]
                    df = df.with_columns(pl.Series(c, vals, dtype=pl.Utf8))

        exprs = []
        keep_columns = []
        row_errors = 0

        # ---------------------------------------------------------------
        # Core column resolver — used by EVERY transformation type.
        # Priority:
        #   1. First AST source_columns entry whose column_name exists in df
        #   2. Target column name itself (direct name match)
        #   3. Any AST source_columns entry after stripping table prefix
        #   4. Synonym-group fuzzy match against all actual df columns
        # Returns the resolved column name string, or None if unresolvable.
        # ---------------------------------------------------------------
        def _resolve_src_col(
            source_cols_list: List[Dict],
            target_name: str,
        ) -> Optional[str]:
            # Priority 1: First configured source column actually present in df
            for sc in source_cols_list:
                cname = sc.get("column_name")
                if cname and cname in df.columns:
                    return cname

            # Priority 2: Direct target column name match in df
            if target_name in df.columns:
                return target_name

            # Priority 3: Strip table prefix from configured column names
            for sc in source_cols_list:
                cname = sc.get("column_name", "")
                stripped = cname.split(".")[-1]
                if stripped in df.columns:
                    return stripped

            # Priority 4: Synonym fuzzy match
            synonyms = _synonym_group_for(target_name)
            if synonyms:
                for candidate in df.columns:
                    if _normalize_col(candidate) in synonyms:
                        logger.info(
                            f"[ASTTransformer] Fuzzy resolved target='{target_name}' "
                            f"to df column '{candidate}' via semantic synonym group."
                        )
                        return candidate

            # Priority 5: Normalized target match
            norm_target = _normalize_col(target_name)
            for candidate in df.columns:
                if _normalize_col(candidate) == norm_target:
                    logger.info(
                        f"[ASTTransformer] Normalized match target='{target_name}' → source='{candidate}'."
                    )
                    return candidate

            return None

        # ---------------------------------------------------------------
        # Helper: safe fallback expression when column cannot be resolved
        # ---------------------------------------------------------------
        def _unresolved_expr(target_col: str, col_spec: Dict) -> pl.Expr:
            """Return lineage for _source_origin, UUID series for PK/id columns, NULL literal for everything else."""
            if target_col == "_source_origin":
                val = source_origin or col_spec.get("constant_value") or "unknown"
                return pl.lit(str(val)).alias(target_col)
            if col_spec.get("is_primary_key") or target_col in ("id",):
                uuid_list = [_deterministic_fallback_uuid(retry_seed_prefix, row_offset + i) for i in range(len(df))]
                return pl.Series(target_col, uuid_list)
            return pl.lit(None).cast(pl.Utf8).alias(target_col)

        # ---------------------------------------------------------------
        # Process each column mapping
        # ---------------------------------------------------------------
        for col_spec in column_mappings:
            target_col = col_spec.get("target_column_name") or col_spec.get("target_column")
            trans_type = col_spec.get("transformation_type", "direct_copy")
            source_cols = col_spec.get("source_columns", [])
            expr_tmpl = col_spec.get("expression_template")
            const_val = col_spec.get("constant_value")

            if not target_col or trans_type == "drop_column":
                continue

            keep_columns.append(target_col)

            # Special handling for platform data lineage column _source_origin
            if target_col == "_source_origin":
                val = source_origin or const_val or "unknown"
                exprs.append(pl.lit(str(val)).alias(target_col))
                continue

            src_name = _resolve_src_col(source_cols, target_col)

            # ----------------------------------------------------------
            # 1. direct_copy
            # ----------------------------------------------------------
            if trans_type == "direct_copy":
                target_dtype = str(col_spec.get("target_data_type", "")).lower()
                if "uuid" in target_dtype and src_name:
                    src_ident = source_cols[0].get("identifier", "source") if source_cols else "source"
                    _sid = src_ident
                    uuid_expr = pl.col(src_name).cast(pl.Utf8).map_elements(
                        lambda v, s=_sid: (
                            str(uuid.uuid5(uuid.NAMESPACE_DNS, f"{s}_{v}"))
                            if v not in (None, "") and not (len(str(v)) == 36 and "-" in str(v))
                            else (str(v) if (v not in (None, "") and len(str(v)) == 36 and "-" in str(v)) else None)
                        ),
                        return_dtype=pl.Utf8,
                    )
                    exprs.append(uuid_expr.alias(target_col))
                elif any(kw in target_dtype for kw in ("bool", "boolean")) and src_name:
                    is_nullable = bool(col_spec.get("nullable", True))
                    def _parse_bool(v, nullable=is_nullable):
                        if v is None:
                            return None if nullable else False
                        if isinstance(v, bool):
                            return v
                        if isinstance(v, (int, float)):
                            return bool(v)
                        s = str(v).strip().lower()
                        if s in ("true", "t", "1", "yes", "y", "enabled", "clear", "cleared", "approved", "pass"):
                            return True
                        if s in ("false", "f", "0", "no", "n", "disabled", "rejected", "failed"):
                            return False
                        return None if nullable else False

                    # NOTE: Polars map_elements skip_nulls=True by default, so _parse_bool is
                    # never invoked for None values — they always return None from Polars.
                    # For non-nullable columns, chain .fill_null(False) to coerce SQL/MongoDB NULLs.
                    bool_expr = pl.col(src_name).map_elements(_parse_bool, return_dtype=pl.Boolean)
                    if not is_nullable:
                        bool_expr = bool_expr.fill_null(False)
                    exprs.append(bool_expr.alias(target_col))
                elif src_name:
                    exprs.append(pl.col(src_name).alias(target_col))
                else:
                    exprs.append(_unresolved_expr(target_col, col_spec))

            # ----------------------------------------------------------
            # 2. type_cast
            # ----------------------------------------------------------
            elif trans_type == "type_cast":
                src_ident = source_cols[0].get("identifier", "source") if source_cols else "source"
                target_dtype = col_spec.get("target_data_type", "varchar").lower()
                pk_strategy = primary_key_strategy or col_spec.get("primary_key_strategy", "uuid_v5")

                if src_name:
                    if col_spec.get("is_primary_key"):
                        if pk_strategy == "keep_original":
                            pk_expr = (
                                pl.col(src_name).cast(pl.Int64, strict=False)
                                if "int" in target_dtype
                                else pl.col(src_name).cast(pl.Utf8)
                            )
                        elif pk_strategy == "autoincrement_offset":
                            src_idx = col_spec.get("source_index", 0)
                            if not src_idx and src_ident:
                                nums = re.findall(r'\d+', str(src_ident))
                                if nums:
                                    src_idx = max(0, int(nums[-1]) - 1)
                            offset_val = 1_000_000_000 * int(src_idx)
                            pk_expr = (
                                pl.col(src_name).cast(pl.Int64, strict=False) + offset_val
                            ).cast(pl.Int64)
                        elif pk_strategy == "uuid_v4_rekey":
                            pk_list = [_deterministic_fallback_uuid(retry_seed_prefix, row_offset + i) for i in range(df.height)]
                            pk_expr = pl.Series(pk_list)
                        elif pk_strategy == "prefix_id":
                            _sid = src_ident  # capture for closure
                            _col_vals = [str(v) if v is not None else None for v in df[src_name].to_list()]
                            pk_list = [
                                f"{_sid}_{v}" if v not in (None, "") else _deterministic_fallback_uuid(retry_seed_prefix, row_offset + i)
                                for i, v in enumerate(_col_vals)
                            ]
                            pk_expr = pl.Series(pk_list)
                        else:
                            # Default: deterministic UUID v5
                            _sid = src_ident
                            _col_vals = [str(v) if v is not None else None for v in df[src_name].to_list()]
                            pk_list = [
                                str(uuid.uuid5(uuid.NAMESPACE_DNS, f"{_sid}_{v}")) if v not in (None, "")
                                else _deterministic_fallback_uuid(retry_seed_prefix, row_offset + i)
                                for i, v in enumerate(_col_vals)
                            ]
                            pk_expr = pl.Series(pk_list)
                        exprs.append(pk_expr.alias(target_col))

                    elif "uuid" in target_dtype or col_spec.get("is_foreign_key_to_uuid"):
                        _sid = src_ident
                        uuid_expr = pl.col(src_name).cast(pl.Utf8).map_elements(
                            lambda v, s=_sid: (
                                str(uuid.uuid5(uuid.NAMESPACE_DNS, f"{s}_{v}"))
                                if v not in (None, "") and not (len(str(v)) == 36 and "-" in str(v))
                                else (str(v) if (v not in (None, "") and len(str(v)) == 36 and "-" in str(v)) else None)
                            ),
                            return_dtype=pl.Utf8,
                        )
                        exprs.append(uuid_expr.alias(target_col))

                    elif any(kw in target_dtype for kw in ("bool", "boolean")):
                        is_nullable = bool(col_spec.get("nullable", True))
                        def _parse_bool(v, nullable=is_nullable):
                            if v is None:
                                return None if nullable else False
                            if isinstance(v, bool):
                                return v
                            if isinstance(v, (int, float)):
                                return bool(v)
                            s = str(v).strip().lower()
                            if s in ("true", "t", "1", "yes", "y", "enabled", "clear", "cleared", "approved", "pass"):
                                return True
                            if s in ("false", "f", "0", "no", "n", "disabled", "rejected", "failed"):
                                return False
                            return None if nullable else False

                        # Polars map_elements skips Nones; chain fill_null for NOT NULL columns.
                        bool_expr = pl.col(src_name).map_elements(_parse_bool, return_dtype=pl.Boolean)
                        if not is_nullable:
                            bool_expr = bool_expr.fill_null(False)
                        exprs.append(bool_expr.alias(target_col))

                    elif "int" in target_dtype:
                        exprs.append(pl.col(src_name).cast(pl.Int64, strict=False).alias(target_col))

                    elif "decimal" in target_dtype or "numeric" in target_dtype:
                        exprs.append(pl.col(src_name).cast(pl.Utf8).alias(target_col))

                    elif any(kw in target_dtype for kw in ("timestamp", "datetime", "timestamptz")):
                        # Version-agnostic datetime parsing: cast to string, normalize space→T,
                        # then use map_elements to try multiple format patterns without
                        # relying on Polars-version-specific kwargs like use_earliest.
                        def _parse_dt(v):
                            if v is None or str(v).strip() in ("", "None", "null", "0000-00-00 00:00:00", "0000-00-00", "0"):
                                return None
                            s = str(v).strip()
                            if _is_sql_datetime_expr(s):
                                return datetime.now(timezone.utc).isoformat()
                            s = s.replace(" ", "T")
                            for fmt in (
                                "%Y-%m-%dT%H:%M:%S%.f%z",
                                "%Y-%m-%dT%H:%M:%S%z",
                                "%Y-%m-%dT%H:%M:%S%.f",
                                "%Y-%m-%dT%H:%M:%S",
                                "%Y-%m-%d",
                            ):
                                try:
                                    from datetime import datetime as _dt
                                    return _dt.strptime(s[:len(fmt)+5], fmt).isoformat()
                                except Exception:
                                    pass
                            # If parsing fails (e.g. non-date string "1"), fallback to current UTC timestamp
                            return s  # passthrough as string if all formats fail

                        dt_expr = pl.col(src_name).cast(pl.Utf8).map_elements(
                            _parse_dt, return_dtype=pl.Utf8
                        )
                        exprs.append(dt_expr.alias(target_col))

                    else:
                        exprs.append(pl.col(src_name).cast(pl.Utf8).alias(target_col))

                else:
                    exprs.append(_unresolved_expr(target_col, col_spec))

            # ----------------------------------------------------------
            # 3. merge_concat
            # ----------------------------------------------------------
            elif trans_type == "merge_concat":
                # Resolve each source column independently
                resolved = []
                for sc in source_cols:
                    c = sc.get("column_name")
                    r = _resolve_src_col([sc], c or target_col)
                    if r:
                        resolved.append(r)
                # De-duplicate while preserving order
                seen = set()
                resolved = [c for c in resolved if not (c in seen or seen.add(c))]
                if resolved:
                    concat_expr = pl.concat_str(
                        [pl.col(c).cast(pl.Utf8).fill_null("") for c in resolved],
                        separator=" ",
                    )
                    exprs.append(concat_expr.alias(target_col))
                else:
                    exprs.append(pl.lit(None).cast(pl.Utf8).alias(target_col))

            # ----------------------------------------------------------
            # 4. split
            # ----------------------------------------------------------
            elif trans_type == "split":
                if src_name:
                    split_expr = pl.col(src_name).cast(pl.Utf8).str.split(",").list.get(0)
                    exprs.append(split_expr.alias(target_col))
                else:
                    exprs.append(pl.lit(None).cast(pl.Utf8).alias(target_col))

            # ----------------------------------------------------------
            # 5. default_constant
            # ----------------------------------------------------------
            elif trans_type == "default_constant":
                target_dtype = col_spec.get("target_data_type", "").lower()
                is_dt_target = (
                    any(dt_kw in target_dtype for dt_kw in ("time", "date", "timestamp", "datetime"))
                    or any(k in target_col.lower() for k in ("_at", "created_at", "updated_at", "timestamp", "datetime"))
                )
                if _is_sql_datetime_expr(const_val) or (is_dt_target and _is_sql_datetime_expr(const_val)):
                    exprs.append(pl.lit(datetime.now(timezone.utc).isoformat()).alias(target_col))
                else:
                    val = const_val if const_val is not None else ""
                    exprs.append(pl.lit(str(val)).alias(target_col))

            # ----------------------------------------------------------
            # 6. new_column_added
            # ----------------------------------------------------------
            elif trans_type == "new_column_added":
                target_dtype = col_spec.get("target_data_type", "").lower()
                is_dt_target = (
                    any(dt_kw in target_dtype for dt_kw in ("time", "date", "timestamp", "datetime"))
                    or any(k in target_col.lower() for k in ("_at", "created_at", "updated_at", "timestamp", "datetime"))
                )
                if col_spec.get("is_primary_key") or (target_col == "id" and not const_val):
                    pk_list = [_deterministic_fallback_uuid(retry_seed_prefix, row_offset + i) for i in range(df.height)]
                    exprs.append(pl.Series(target_col, pk_list))
                elif _is_sql_datetime_expr(const_val) or (is_dt_target and (_is_sql_datetime_expr(const_val) or const_val is None or str(const_val).strip() == "")):
                    exprs.append(pl.lit(datetime.now(timezone.utc).isoformat()).alias(target_col))
                elif const_val is not None and str(const_val) != "":
                    exprs.append(pl.lit(str(const_val)).alias(target_col))
                elif "uuid" in target_dtype or target_col.endswith("_id"):
                    # NULL FK/UUID columns — let DB default or FK resolution fill them
                    exprs.append(pl.lit(None).cast(pl.Utf8).alias(target_col))
                elif is_dt_target:
                    exprs.append(pl.lit(datetime.now(timezone.utc).isoformat()).alias(target_col))
                else:
                    exprs.append(pl.lit(None).cast(pl.Utf8).alias(target_col))

            # ----------------------------------------------------------
            # 7. expression
            # ----------------------------------------------------------
            elif trans_type == "expression" and expr_tmpl:
                forbidden_sql = [
                    r"\bdrop\b", r"\bdelete\b", r"\bupdate\b", r"\binsert\b",
                    r"\battach\b", r"\bcopy\b", r"\btruncate\b", r"\balter\b", r"\bexecute\b",
                ]
                if any(re.search(pat, expr_tmpl, re.IGNORECASE) for pat in forbidden_sql):
                    logger.warning(
                        f"Expression template '{expr_tmpl}' contained restricted SQL keywords "
                        f"and was safely skipped for '{target_col}'."
                    )
                    if src_name:
                        exprs.append(pl.col(src_name).alias(target_col))
                else:
                    try:
                        calc_df = duckdb.sql(f'SELECT ({expr_tmpl}) AS "{target_col}" FROM df').pl()
                        if target_col in calc_df.columns:
                            exprs.append(calc_df[target_col])
                    except Exception as expr_err:
                        logger.warning(
                            f"Expression calculation notice for '{target_col}' ({expr_tmpl}): {expr_err}"
                        )
                        if src_name:
                            exprs.append(pl.col(src_name).alias(target_col))

            # ----------------------------------------------------------
            # 8. json_flatten
            # ----------------------------------------------------------
            elif trans_type == "json_flatten":
                if src_name:
                    exprs.append(pl.col(src_name).cast(pl.Utf8).alias(target_col))
                elif source_cols and "." in (source_cols[0].get("column_name") or ""):
                    full_path = source_cols[0]["column_name"]
                    parts = full_path.split(".")
                    root_col = parts[0]
                    sub_paths = parts[1:]
                    if root_col in df.columns:
                        def _extract_nested(val, paths=sub_paths):
                            curr = val
                            if hasattr(curr, "to_dict"):
                                curr = curr.to_dict()
                            for p in paths:
                                if isinstance(curr, dict):
                                    curr = curr.get(p, "")
                                elif isinstance(curr, str) and curr.startswith("{"):
                                    try:
                                        curr = json.loads(curr).get(p, "")
                                    except Exception:
                                        return ""
                                else:
                                    return ""
                            return str(curr) if curr is not None else ""

                        exprs.append(
                            pl.col(root_col).map_elements(_extract_nested, return_dtype=pl.Utf8).alias(target_col)
                        )

            # ----------------------------------------------------------
            # 9. json_stringify
            # ----------------------------------------------------------
            elif trans_type == "json_stringify":
                if src_name:
                    def _safe_json_dumps(val):
                        if hasattr(val, "to_dict"):
                            val = val.to_dict()
                        elif hasattr(val, "to_list"):
                            val = val.to_list()
                        if isinstance(val, (dict, list)):
                            return json.dumps(val, default=str)
                        if isinstance(val, str) and (val.startswith("{") or val.startswith("[")):
                            return val
                        if val is not None:
                            return json.dumps(val, default=str)
                        return "{}"

                    exprs.append(
                        pl.col(src_name).map_elements(_safe_json_dumps, return_dtype=pl.Utf8).alias(target_col)
                    )

            # ----------------------------------------------------------
            # 10. array_to_csv
            # ----------------------------------------------------------
            elif trans_type == "array_to_csv":
                if src_name:
                    def _safe_array_to_csv(val):
                        if hasattr(val, "to_list"):
                            val = val.to_list()
                        if isinstance(val, (list, tuple, set, frozenset)):
                            return ",".join(map(str, val))
                        return str(val or "")

                    exprs.append(
                        pl.col(src_name).map_elements(_safe_array_to_csv, return_dtype=pl.Utf8).alias(target_col)
                    )

            # ----------------------------------------------------------
            # 11. array_to_json
            # ----------------------------------------------------------
            elif trans_type == "array_to_json":
                if src_name:
                    def _safe_array_to_json(val):
                        if hasattr(val, "to_list"):
                            val = val.to_list()
                        if isinstance(val, (list, tuple, set, frozenset)):
                            return json.dumps(list(val), default=str)
                        if val is not None:
                            return json.dumps([val], default=str)
                        return "[]"

                    exprs.append(
                        pl.col(src_name).map_elements(_safe_array_to_json, return_dtype=pl.Utf8).alias(target_col)
                    )

            # ----------------------------------------------------------
            # 12. nosql_field_promote
            # ----------------------------------------------------------
            elif trans_type == "nosql_field_promote":
                if src_name:
                    target_dtype = str(col_spec.get("target_data_type", "")).lower()
                    if any(kw in target_dtype for kw in ("bool", "boolean")):
                        is_nullable = bool(col_spec.get("nullable", True))
                        def _parse_bool(v, nullable=is_nullable):
                            if v is None:
                                return None if nullable else False
                            if isinstance(v, bool):
                                return v
                            if isinstance(v, (int, float)):
                                return bool(v)
                            s = str(v).strip().lower()
                            if s in ("true", "t", "1", "yes", "y", "enabled", "clear", "cleared", "approved", "pass"):
                                return True
                            if s in ("false", "f", "0", "no", "n", "disabled", "rejected", "failed"):
                                return False
                            return None if nullable else False

                        # Polars map_elements skips Nones; chain fill_null for NOT NULL columns.
                        bool_expr = pl.col(src_name).map_elements(_parse_bool, return_dtype=pl.Boolean)
                        if not is_nullable:
                            bool_expr = bool_expr.fill_null(False)
                        exprs.append(bool_expr.alias(target_col))
                    else:
                        exprs.append(pl.col(src_name).cast(pl.Utf8).alias(target_col))
                elif source_cols and "." in (source_cols[0].get("column_name") or ""):
                    full_path = source_cols[0]["column_name"]
                    parts = full_path.split(".")
                    root_col = parts[0]
                    sub_paths = parts[1:]
                    if root_col in df.columns:
                        is_nullable = bool(col_spec.get("nullable", True))
                        default_fallback = col_spec.get("constant_value") or "UNKNOWN"
                        def _extract_nested_promote(val, paths=sub_paths, nullable=is_nullable, fallback=default_fallback):
                            curr = val
                            if hasattr(curr, "to_dict"):
                                curr = curr.to_dict()
                            for p in paths:
                                if isinstance(curr, dict):
                                    curr = curr.get(p, "")
                                elif isinstance(curr, str) and curr.startswith("{"):
                                    try:
                                        curr = json.loads(curr).get(p, "")
                                    except Exception:
                                        curr = ""
                                else:
                                    curr = ""
                            res = str(curr) if curr not in (None, "") else ""
                            if not res and not nullable:
                                return fallback
                            return res if res else (None if nullable else fallback)

                        exprs.append(
                            pl.col(root_col).map_elements(_extract_nested_promote, return_dtype=pl.Utf8).alias(target_col)
                        )
                    else:
                        exprs.append(_unresolved_expr(target_col, col_spec))
                else:
                    exprs.append(_unresolved_expr(target_col, col_spec))

            # ----------------------------------------------------------
            # 13. lookup_join / any unknown — best-effort direct copy
            # ----------------------------------------------------------
            else:
                if src_name:
                    exprs.append(pl.col(src_name).alias(target_col))

        # ---------------------------------------------------------------
        # Residual / unmapped field capture → extra_attributes JSONB
        # ---------------------------------------------------------------
        mapped_src_cols: set = set()
        explicit_extra_attr_cols: list = []
        for col_spec in column_mappings:
            target_col = col_spec.get("target_column_name") or col_spec.get("target_column")
            if target_col and col_spec.get("transformation_type") != "drop_column":
                for sc in col_spec.get("source_columns", []):
                    c_name = sc.get("column_name") or sc.get("column") or ""
                    mapped_src_cols.add(c_name)
                    if "." in c_name:
                        mapped_src_cols.add(c_name.split(".")[0])
                    if target_col == "extra_attributes" and c_name in df.columns:
                        explicit_extra_attr_cols.append(c_name)

        unmapped_cols = [c for c in df.columns if c not in mapped_src_cols and c not in ("_seq_id",)]
        all_residual_cols = list(dict.fromkeys(explicit_extra_attr_cols + unmapped_cols))
        all_residual_cols = [c for c in all_residual_cols if c in df.columns and c not in ("_seq_id",)]

        if all_residual_cols:
            if "extra_attributes" not in keep_columns:
                keep_columns.append("extra_attributes")

            # CRITICAL: Remove any prior expression for 'extra_attributes' to prevent Polars DuplicateError
            def _get_expr_name(item):
                if isinstance(item, pl.Series):
                    return item.name
                if isinstance(item, pl.Expr):
                    try:
                        return item.meta.output_name()
                    except Exception:
                        return None
                return None

            exprs = [e for e in exprs if _get_expr_name(e) != "extra_attributes"]

            def _serialize_residual(struct_val):
                res_dict = {}
                if isinstance(struct_val, dict):
                    for k, v in struct_val.items():
                        if v is not None:
                            if hasattr(v, "to_dict"):
                                v = v.to_dict()
                            elif hasattr(v, "to_list"):
                                v = v.to_list()
                            elif isinstance(v, str) and (v.startswith("{") or v.startswith("[")):
                                try:
                                    v = json.loads(v)
                                except Exception:
                                    pass
                            if k in ("extra_attributes", "_extra_attributes", "residual_fields", "unmapped_attributes") and isinstance(v, dict):
                                res_dict.update(v)
                            else:
                                res_dict[k] = v
                return json.dumps(res_dict, default=str) if res_dict else "{}"

            try:
                extra_attr_expr = pl.struct(
                    [pl.col(c) for c in all_residual_cols]
                ).map_elements(_serialize_residual, return_dtype=pl.Utf8)
                exprs.append(extra_attr_expr.alias("extra_attributes"))
            except Exception as exc:
                logger.warning(f"Residual field capture warning: {exc}")


        # ---------------------------------------------------------------
        # Apply all expressions to df
        # ---------------------------------------------------------------
        if exprs:
            try:
                transformed_df = df.with_columns(exprs)

                # Auto-generate UUID primary key 'id' if still missing
                if "id" in keep_columns and "id" not in transformed_df.columns:
                    uuid_list = [_deterministic_fallback_uuid(retry_seed_prefix, row_offset + i) for i in range(len(transformed_df))]
                    transformed_df = transformed_df.with_columns(pl.Series("id", uuid_list))

                def _clean_objects(dframe: pl.DataFrame) -> pl.DataFrame:
                    for col_n, dt in dframe.schema.items():
                        if dt == pl.Object:
                            vals = [str(x) if x is not None else None for x in dframe[col_n].to_list()]
                            dframe = dframe.with_columns(pl.Series(col_n, vals, dtype=pl.Utf8))
                    return dframe

                available_targets = [c for c in keep_columns if c in transformed_df.columns]
                res_df = transformed_df.select(available_targets) if available_targets else transformed_df
                return _clean_objects(res_df), row_errors

            except Exception as exc:
                logger.warning(
                    f"Vectorized transformation warning, falling back with row error tracking: {exc}"
                )
                row_errors += 1

                if "id" in keep_columns and "id" not in df.columns:
                    uuid_list = [_deterministic_fallback_uuid(retry_seed_prefix, row_offset + i) for i in range(len(df))]
                    df = df.with_columns(pl.Series("id", uuid_list))

                available_targets = [c for c in keep_columns if c in df.columns]
                fallback_df = df.select(available_targets) if available_targets else df
                for col_n, dt in fallback_df.schema.items():
                    if dt == pl.Object:
                        vals = [str(x) if x is not None else None for x in fallback_df[col_n].to_list()]
                        fallback_df = fallback_df.with_columns(pl.Series(col_n, vals, dtype=pl.Utf8))
                return fallback_df, row_errors

        # Passthrough: ensure 'id' exists even with no expressions
        if "id" in keep_columns and "id" not in df.columns:
            uuid_list = [_deterministic_fallback_uuid(retry_seed_prefix, row_offset + i) for i in range(len(df))]
            df = df.with_columns(pl.Series("id", uuid_list))

        available_targets = [c for c in keep_columns if c in df.columns]
        pass_df = df.select(available_targets) if available_targets else df
        for col_n, dt in pass_df.schema.items():
            if dt == pl.Object:
                vals = [str(x) if x is not None else None for x in pass_df[col_n].to_list()]
                pass_df = pass_df.with_columns(pl.Series(col_n, vals, dtype=pl.Utf8))
        return pass_df, row_errors
