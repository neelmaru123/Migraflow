"""
DuckDB disk-backed staging area and multi-source deduplication module.
"""

import logging
from typing import Any, Dict, List, Optional
import duckdb
import polars as pl

logger = logging.getLogger("docker-agent-execution")


class TableMerger:
    """Performs multi-database table merging and deduplication."""

    @staticmethod
    def merge_and_deduplicate(
        dfs: List[pl.DataFrame],
        conflict_res: Optional[Dict[str, Any]] = None,
    ) -> pl.DataFrame:
        if not dfs:
            return pl.DataFrame()

        combined = pl.concat(dfs, how="diagonal")

        if not conflict_res:
            return combined

        dedup_key = conflict_res.get("deduplication_key")
        strategy = conflict_res.get("deduplication_strategy", "first_wins")

        if dedup_key and dedup_key in combined.columns:
            keep_opt = "first" if strategy == "first_wins" else "last"
            # Null-Exempt Deduplication: Only unique non-null/non-empty business keys
            valid_mask = (pl.col(dedup_key).is_not_null()) & (pl.col(dedup_key).cast(pl.Utf8).str.strip_chars() != "")
            df_valid = combined.filter(valid_mask).unique(subset=[dedup_key], keep=keep_opt)
            df_nulls = combined.filter(~valid_mask)
            combined = pl.concat([df_valid, df_nulls], how="vertical")
            logger.info(f"Deduplicated combined table on '{dedup_key}' using '{strategy}' strategy (Remaining rows: {len(combined)}).")

        return combined

    @staticmethod
    def append_to_duckdb_staging(
        conn: duckdb.DuckDBPyConnection,
        staging_table_name: str,
        df: pl.DataFrame,
        start_seq: int = 0,
    ) -> int:
        if df.is_empty():
            return start_seq

        # Convert any residual pl.Object columns to pl.Utf8 to prevent DuckDB BLOB casting errors
        for col_name, dtype in df.schema.items():
            if dtype == pl.Object:
                df = df.with_columns(pl.col(col_name).cast(pl.Utf8, strict=False))

        num_rows = len(df)
        seq_series = pl.Series("_seq_id", range(start_seq, start_seq + num_rows), dtype=pl.Int64)
        df_with_seq = df.with_columns(seq_series)

        conn.register("df_chunk_temp", df_with_seq)

        table_exists = conn.execute(
            "SELECT COUNT(*) FROM information_schema.tables WHERE table_name = ?", [staging_table_name]
        ).fetchone()[0] > 0

        if not table_exists:
            conn.execute(f"CREATE TABLE {staging_table_name} AS SELECT * FROM df_chunk_temp")
        else:
            existing_cols = [r[0] for r in conn.execute(f"DESCRIBE {staging_table_name}").fetchall()]
            new_cols = [c for c in df_with_seq.columns if c not in existing_cols]
            for c in new_cols:
                conn.execute(f'ALTER TABLE {staging_table_name} ADD COLUMN "{c}" VARCHAR')

            conn.execute(f"INSERT INTO {staging_table_name} BY NAME SELECT * FROM df_chunk_temp")

        conn.unregister("df_chunk_temp")
        return start_seq + num_rows

    @classmethod
    def stream_deduplicated_chunks(
        cls,
        conn: duckdb.DuckDBPyConnection,
        staging_table_name: str,
        conflict_res: Optional[Dict[str, Any]] = None,
        chunk_size: int = 50000,
    ):
        table_exists = conn.execute(
            "SELECT COUNT(*) FROM information_schema.tables WHERE table_name = ?", [staging_table_name]
        ).fetchone()[0] > 0

        if not table_exists:
            return

        cols = [r[0] for r in conn.execute(f"DESCRIBE {staging_table_name}").fetchall() if r[0] != "_seq_id"]
        if not cols:
            return

        quoted_cols = [f'"{c}"' for c in cols]
        col_select = ", ".join(quoted_cols)

        dedup_key = (conflict_res.get("deduplication_key") if isinstance(conflict_res, dict) else getattr(conflict_res, "deduplication_key", None)) if conflict_res else None
        strategy = (conflict_res.get("deduplication_strategy", "first_wins") if isinstance(conflict_res, dict) else getattr(conflict_res, "deduplication_strategy", "first_wins")) if conflict_res else "first_wins"

        if dedup_key and dedup_key in cols:
            quoted_key = f'"{dedup_key}"'
            order_dir = "ASC" if strategy == "first_wins" else "DESC"

            dedup_sql = f"""
                CREATE TEMP TABLE _dedup_final AS
                WITH valid_rows AS (
                    SELECT {col_select}, _seq_id,
                           ROW_NUMBER() OVER (PARTITION BY {quoted_key} ORDER BY _seq_id {order_dir}) as _rn
                    FROM {staging_table_name}
                    WHERE {quoted_key} IS NOT NULL AND TRIM(CAST({quoted_key} AS VARCHAR)) != ''
                ),
                null_rows AS (
                    SELECT {col_select}, _seq_id
                    FROM {staging_table_name}
                    WHERE {quoted_key} IS NULL OR TRIM(CAST({quoted_key} AS VARCHAR)) = ''
                )
                SELECT {col_select}, _seq_id FROM valid_rows WHERE _rn = 1
                UNION ALL
                SELECT {col_select}, _seq_id FROM null_rows
                ORDER BY _seq_id ASC
            """
            conn.execute("DROP TABLE IF EXISTS _dedup_final")
            conn.execute(dedup_sql)
            target_relation = "_dedup_final"
        else:
            target_relation = staging_table_name

        total_rows = conn.execute(f"SELECT COUNT(*) FROM {target_relation}").fetchone()[0]
        logger.info(f"Streaming {total_rows} deduplicated rows from DuckDB staging area in chunks of {chunk_size}...")

        for offset in range(0, total_rows, chunk_size):
            chunk_df = conn.execute(
                f"SELECT {col_select} FROM {target_relation} ORDER BY _seq_id ASC LIMIT {chunk_size} OFFSET {offset}"
            ).pl()
            yield chunk_df
