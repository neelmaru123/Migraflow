"""
High-level ETL migration orchestrator module for agent execution engine.
"""

import logging
import os
import re
from typing import Any, Dict
import duckdb

from .db import dispose_all_engines
from .checkpoint import CheckpointManager
from .ddl_executor import DDLExecutor
from .progress_reporter import ProgressReporter
from .connectors.source_factory import SourceConnectorFactory
from .transformers.ast_transformer import ASTTransformer
from .staging.duckdb_staging import TableMerger
from .writers.target_writer import TargetWriterFactory

logger = logging.getLogger("docker-agent-execution")


class JobCancelledException(Exception):
    """Raised when the backend signals that the user cancelled the execution job."""
    pass


def _report_progress(backend_url: str, agent_token: str, job_id: str, *args, **kwargs) -> Any:
    """Helper that reports progress and raises JobCancelledException if job was cancelled."""
    res = ProgressReporter.report(backend_url, agent_token, job_id, *args, **kwargs)
    if res and isinstance(res, dict) and res.get("status") == "cancelled":
        raise JobCancelledException(f"Execution job '{job_id}' was cancelled by user.")
    return res


class ExecutionOrchestrator:
    """Main orchestrator executing local ETL migration pipeline for an AST plan."""

    @staticmethod
    def run_job(
        backend_url: str,
        agent_token: str,
        job_id: str,
        plan_ast: Dict[str, Any],
        source_db_urls: Dict[str, str],
        target_db_url: str,
        target_engine_type: str = "postgresql",
        is_dry_run: bool = False,
        truncate_target: bool = False,
    ):
        plan_data = plan_ast.get("plan_data", {})
        if not is_dry_run:
            is_dry_run = bool(plan_ast.get("is_dry_run", False) or plan_data.get("is_dry_run", False))

        logger.info(f"=== STARTING LOCAL ETL EXECUTION FOR JOB '{job_id}' (Dry Run: {is_dry_run}, Clean Wipe: {truncate_target}) ===")
        pre_ddl = plan_data.get("pre_migration_ddl", [])
        post_ddl = plan_data.get("post_migration_ddl", [])
        table_mappings = plan_data.get("table_mappings", [])

        # Clean up stale DuckDB staging files from OTHER completed/abandoned jobs
        # (EC-20). Do NOT delete a staging file belonging to THIS job_id here —
        # if one exists, it means this job crashed mid-merge on a previous attempt,
        # and the per-table logic further below (which checks for and resets stale
        # checkpoints before removing it) needs to see it first, or the crash
        # recovery / checkpoint reset logic never fires and rows get silently
        # skipped on resume.
        tmp_dir = os.getenv("CHECKPOINT_DIR", "/tmp")
        if os.path.exists(tmp_dir):
            try:
                for fname in os.listdir(tmp_dir):
                    if (
                        fname.startswith("staging_")
                        and not fname.startswith(f"staging_{job_id}_")
                        and fname.endswith(".duckdb")
                    ):
                        try:
                            os.remove(os.path.join(tmp_dir, fname))
                            logger.info(f"Cleaned up stale staging file: {fname}")
                        except Exception:
                            pass
            except Exception:
                pass

        if not table_mappings:
            raise ValueError("Transformation plan contains 0 target table mappings. At least 1 table mapping is required.")

        total_tables = len(table_mappings)
        total_processed = 0
        total_successful = 0
        total_failed = 0
        total_skipped = 0

        total_estimated_rows = 0
        for tm in table_mappings:
            for st in tm.get("source_tables", []):
                total_estimated_rows += int(st.get("row_count", 0) or 0)

        try:
            # Step 0: Ensure Target Database Exists & Live Pre-Flight Check / Clean Wipe
            if not is_dry_run and target_db_url:
                DDLExecutor._ensure_database_exists(target_db_url)
                existing_tables = DDLExecutor.get_existing_tables_and_counts(target_db_url, target_engine_type)
                if existing_tables:
                    existing_summary = ", ".join(f"{t['table_name']} ({t['row_count']} rows)" for t in existing_tables)
                    if truncate_target:
                        logger.warning(
                            f"=== CLEAN WIPE TARGET DATABASE REQUESTED ===\n"
                            f"Dropping all {len(existing_tables)} existing table(s) in target database: {existing_summary}"
                        )
                        _report_progress(
                            backend_url, agent_token, job_id, "running", 5.0, 0, 0, 0, 0,
                            total_rows=total_estimated_rows, current_stage="target_clean_wipe"
                        )
                        dropped = DDLExecutor.clean_wipe_target_database(target_db_url, target_engine_type)
                        logger.info(f"Clean wipe complete. Dropped target tables: {dropped}")
                    else:
                        logger.warning(
                            f"=== TARGET DATABASE CONTAINS EXISTING DATA ===\n"
                            f"Existing tables detected: {existing_summary}\n"
                            f"User did not select Clean Wipe. Proceeding with APPEND mode (ON CONFLICT DO NOTHING)."
                        )

            # Step 1: Pre-Migration DDL
            _report_progress(
                backend_url, agent_token, job_id, "running", 10.0, 0, 0, 0, 0,
                total_rows=total_estimated_rows, current_stage="pre_ddl"
            )
            if not is_dry_run:
                DDLExecutor.execute_ddl_list(target_db_url, pre_ddl, "Pre-Migration DDL")
            else:
                logger.info(f"[DRY RUN] Pre-Migration DDL skipped ({len(pre_ddl)} statements).")

            # Step 2: Data Extraction, AST Transformation, Merge, and Target Loading
            for idx, table_spec in enumerate(table_mappings, start=1):
                target_table = table_spec.get("target_table_name")
                source_tables = table_spec.get("source_tables", [])
                column_mappings = table_spec.get("column_mappings", [])
                conflict_res = table_spec.get("conflict_resolution")

                logger.info(f"Processing table [{idx}/{total_tables}]: '{target_table}'...")
                pct = 10.0 + (float(idx) / float(total_tables) * 80.0)
                _report_progress(
                    backend_url, agent_token, job_id, "running", pct,
                    total_processed, total_successful, total_failed, total_skipped,
                    total_rows=total_estimated_rows if total_estimated_rows > 0 else (total_processed if total_processed > 0 else 0),
                    current_table=target_table, current_stage="data_streaming"
                )

                staging_conn = None
                staging_db_file = None
                staging_table_name = "staging_data"
                start_seq = 0

                if len(source_tables) > 1:
                    staging_dir = os.getenv("CHECKPOINT_DIR", "/tmp")
                    os.makedirs(staging_dir, exist_ok=True)
                    staging_db_file = os.path.join(staging_dir, f"staging_{job_id}_{target_table}.duckdb")
                    if os.path.exists(staging_db_file):
                        # A staging file already exists for this table — this means a
                        # previous run of this job crashed mid-merge before it could
                        # clean up. Since we are about to delete and recreate this
                        # staging file, any per-source checkpoints that advanced past
                        # offset 0 for this table are now stale (they'd point past
                        # rows that no longer exist in the fresh staging DB). Reset
                        # them so every source re-stages from the beginning, keeping
                        # checkpoint state and staging-file state consistent.
                        logger.warning(
                            f"Found leftover staging file for table '{target_table}' from a previous "
                            f"crashed run. Resetting per-source checkpoints for this table and re-staging "
                            f"from scratch to avoid silent data loss."
                        )
                        CheckpointManager.clear_table_checkpoints(job_id, target_table)
                        try:
                            os.remove(staging_db_file)
                        except Exception:
                            pass
                    staging_conn = duckdb.connect(staging_db_file)

                try:
                    for src_ref in source_tables:
                        src_ident = src_ref.get("identifier")
                        src_table = src_ref.get("table_name")

                        # Match source DB URL and engine type
                        db_url = None
                        if src_ident:
                            clean_id = str(src_ident).lower().strip()
                            norm_id = re.sub(r'^(src_|dest_|source_|target_)', '', clean_id)

                            # 1. Exact or normalized match
                            for k, v in source_db_urls.items():
                                k_norm = re.sub(r'^(src_|dest_|source_|target_)', '', str(k).lower().strip())
                                if str(k).lower().strip() == clean_id or k_norm == norm_id:
                                    db_url = v
                                    break

                            # 2. Numbered suffix match (e.g. "source_db_2" -> "db_2")
                            if not db_url:
                                id_numbers = re.findall(r'\d+', norm_id)
                                if id_numbers:
                                    target_num = id_numbers[-1]
                                    for k, v in source_db_urls.items():
                                        k_numbers = re.findall(r'\d+', str(k))
                                        if k_numbers and k_numbers[-1] == target_num:
                                            db_url = v
                                            break

                        if not db_url:
                            if target_db_url and any(tag in norm_id for tag in ["dst", "dest", "target"]):
                                db_url = target_db_url
                            else:
                                raise ValueError(
                                    f"Could not match source identifier '{src_ident}' to any configured "
                                    f"source database. Configured sources: {list(source_db_urls.keys())}. "
                                    f"Check that the migration plan's identifier matches an env var like "
                                    f"SRC_{{name}}_URL, or that the naming convention matches."
                                )

                        src_engine = "postgresql"
                        if db_url:
                            if "mysql" in db_url:
                                src_engine = "mysql"
                            elif "mongo" in db_url:
                                src_engine = "mongodb"
                            elif "sqlite" in db_url:
                                src_engine = "sqlite"

                        # Detect primary key column if available for keyset pagination
                        pk_col = None
                        for col in column_mappings:
                            if col.get("is_primary_key"):
                                src_cols = col.get("source_columns", [])
                                for sc in src_cols:
                                    sc_tbl = sc.get("table_name")
                                    sc_ident = sc.get("identifier")
                                    if (sc_tbl and sc_tbl == src_table) or (sc_ident and (sc_ident == src_ident or sc_ident in str(src_ident))):
                                        pk_col = sc.get("column_name")
                                        break
                                if not pk_col and src_cols and "column_name" in src_cols[0]:
                                    pk_col = src_cols[0]["column_name"]
                                if pk_col:
                                    break

                        if src_engine == "mongodb" and not pk_col:
                            pk_col = "_id"

                        # Resumable Checkpoint offset & cursor
                        offset = CheckpointManager.get_last_offset(job_id, target_table, source_identifier=src_ident, source_table=src_table)
                        last_pk_val = None
                        has_more = True

                        while has_more:
                            df_raw, has_more, next_pk = SourceConnectorFactory.read_source_chunk(
                                db_url=db_url,
                                engine_type=src_engine,
                                table_or_file_name=src_table,
                                offset=offset,
                                chunk_size=50000,
                                pk_col=pk_col,
                                last_pk_val=last_pk_val,
                            )
                            if next_pk is not None:
                                last_pk_val = next_pk

                            if df_raw.is_empty():
                                break

                            logger.info(f"Extracted chunk of {len(df_raw)} rows from source '{src_ident}.{src_table}' (Offset: {offset}).")
                            retry_seed_prefix = f"{job_id}:{target_table}:{src_ident}:{src_table}"
                            src_origin_tag = f"{src_ident}.{src_table}" if src_ident else str(src_table)
                            df_trans, trans_errors = ASTTransformer.transform_chunk(
                                df_raw,
                                column_mappings,
                                retry_seed_prefix=retry_seed_prefix,
                                row_offset=offset,
                                source_origin=src_origin_tag,
                            )
                            total_failed += trans_errors

                            # If single source table, load directly to target (OOM-free streaming)
                            if len(source_tables) == 1:
                                if not is_dry_run:
                                    succ, fail, skip = TargetWriterFactory.bulk_load(target_db_url, target_engine_type, target_table, df_trans)
                                    total_processed += (succ + fail + skip)
                                    total_successful += succ
                                    total_failed += fail
                                    total_skipped += skip
                                else:
                                    succ = len(df_trans)
                                    total_processed += (succ + trans_errors)
                                    total_successful += succ
                            else:
                                start_seq = TableMerger.append_to_duckdb_staging(staging_conn, staging_table_name, df_trans, start_seq)

                            offset += len(df_raw)
                            CheckpointManager.save_checkpoint(job_id, target_table, offset, total_processed, source_identifier=src_ident, source_table=src_table)

                    # For multi-source merges, stream deduplicated results from DuckDB staging area in bounded batches
                    if len(source_tables) > 1 and staging_conn:
                        for chunk_df in TableMerger.stream_deduplicated_chunks(staging_conn, staging_table_name, conflict_res, chunk_size=50000):
                            if not is_dry_run:
                                succ, fail, skip = TargetWriterFactory.bulk_load(target_db_url, target_engine_type, target_table, chunk_df)
                                total_processed += (succ + fail + skip)
                                total_successful += succ
                                total_failed += fail
                                total_skipped += skip
                            else:
                                succ = len(chunk_df)
                                total_processed += succ
                                total_successful += succ

                finally:
                    if staging_conn:
                        try:
                            staging_conn.close()
                        except Exception:
                            pass
                    if staging_db_file and os.path.exists(staging_db_file):
                        try:
                            os.remove(staging_db_file)
                        except Exception:
                            pass

            # Step 3: Post-Migration DDL (Foreign Keys)
            _report_progress(
                backend_url, agent_token, job_id, "running", 95.0,
                total_processed, total_successful, total_failed, total_skipped,
                total_rows=total_estimated_rows, current_stage="post_ddl"
            )
            if not is_dry_run:
                DDLExecutor.execute_ddl_list(target_db_url, post_ddl, "Post-Migration DDL")
            else:
                logger.info(f"[DRY RUN] Post-Migration DDL skipped ({len(post_ddl)} statements).")

            # Step 4: Verify write success rate before declaring the job complete.
            # Without this check, a target database that is completely unreachable
            # (e.g. dead MongoDB target) can cause every single row to fail while
            # the job still reports "completed" with 100% failure — this check
            # prevents that.
            if total_processed > 0:
                failure_rate = total_failed / total_processed
                if failure_rate > 0.50:
                    raise RuntimeError(
                        f"Migration job '{job_id}' aborted at completion check: "
                        f"{total_failed}/{total_processed} rows failed ({failure_rate*100:.1f}%), "
                        f"exceeding the 50% failure threshold. Target database may be unreachable "
                        f"or schema/mapping may be misconfigured."
                    )

            # Step 5: Mark Job Complete and Clean Checkpoints
            if not is_dry_run:
                CheckpointManager.clear_job_checkpoints(job_id)
                final_status = "completed"
            else:
                logger.info(f"[DRY RUN] Preserving checkpoints (no-op) for dry run job '{job_id}'.")
                final_status = "dry_run_completed"

            final_total_rows = max(total_estimated_rows, total_successful, total_processed)
            _report_progress(
                backend_url, agent_token, job_id, final_status, 100.0,
                total_processed, total_successful, total_failed, total_skipped,
                total_rows=final_total_rows, current_stage=final_status
            )
            logger.info(f"=== MIGRATION JOB '{job_id}' ({final_status.upper()})! (Processed: {total_processed}, Would Write: {total_successful}, Failed: {total_failed}, Skipped: {total_skipped}) ===")

        except JobCancelledException:
            logger.warning(f"=== MIGRATION JOB '{job_id}' WAS CANCELLED BY USER. Halting pipeline. ===")
            return
        except Exception as exc:
            err_msg = f"Migration job '{job_id}' failed: {exc}"
            logger.error(err_msg, exc_info=True)
            ProgressReporter.report(
                backend_url, agent_token, job_id, "failed", 0.0,
                total_processed, total_successful, total_failed, total_skipped,
                total_rows=total_estimated_rows, current_stage="failed", error_message=err_msg
            )
            raise
        finally:
            try:
                dispose_all_engines()
            except Exception:
                pass
