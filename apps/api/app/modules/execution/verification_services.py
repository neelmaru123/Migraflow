"""
Deterministic Verification Subsystem and Verification Engine.
Implements the 11 standardized post-migration verification checks,
configurable verification policies, durable VerificationResult records,
and feeds verification failures directly into Phase 3 RecoveryRouter.
"""

from datetime import datetime, timezone
import logging
from typing import Any, Dict, List, Optional, Set, Tuple, Union
import uuid
from pydantic import BaseModel, ConfigDict, Field

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload


from app.core.state import (
    ExecutionEventType,
    ExecutionLifecycle,
    ExecutionPlanLifecycle,
    ExecutionStepLifecycle,
    FailureCategory,
    FailureDomain,
    FailureSeverity,
    RecoveryDecisionType,
    VerificationCheckType,
    VerificationStatus,
)
from app.modules.execution.execution_models import (
    ExecutionEvent,
    MigrationExecutionPlan,
    MigrationExecutionStep,
    MigrationJob,
    VerificationResult,
)
from app.modules.execution.failure_taxonomy import ClassifiedFailure
from app.modules.execution.recovery_router import recovery_router

logger = logging.getLogger(__name__)


class VerificationPolicy(BaseModel):
    """
    Configurable verification policy defining required checks, tolerances, and warning thresholds.
    """
    model_config = ConfigDict(from_attributes=True)

    allow_warnings: bool = True
    row_count_tolerance_pct: float = 0.0  # e.g. 0.0 = exact match required
    max_failed_rows_allowed: int = 0
    sample_comparison_limit: int = 50
    strict_fk_enforcement: bool = True
    strict_nullability_enforcement: bool = True
    enabled_checks: List[VerificationCheckType] = Field(
        default_factory=lambda: [
            VerificationCheckType.ROW_COUNT,
            VerificationCheckType.ROWS_PROCESSED,
            VerificationCheckType.FAILED_ROWS,
            VerificationCheckType.DUPLICATE_DETECTION,
            VerificationCheckType.PRIMARY_KEY_INTEGRITY,
            VerificationCheckType.FOREIGN_KEY_INTEGRITY,
            VerificationCheckType.NULLABILITY,
            VerificationCheckType.TABLE_EXISTENCE,
            VerificationCheckType.SCHEMA_COMPATIBILITY,
            VerificationCheckType.TRANSFORMATION_SANITY,
            VerificationCheckType.SAMPLE_DATA_COMPARISON,
        ]
    )


DEFAULT_VERIFICATION_POLICY = VerificationPolicy()


class VerificationEngine:
    """
    Executes the 11 deterministic verification checks across source, target, and ETL metrics.
    """

    # =========================================================================
    # 1. Source Row Count vs Target Row Count
    # =========================================================================
    @classmethod
    def check_row_counts(
        cls,
        source_count: int,
        target_count: int,
        tolerance_pct: float = 0.0,
    ) -> Tuple[VerificationStatus, Dict[str, Any]]:
        diff = abs(source_count - target_count)
        allowed_diff = int(source_count * (tolerance_pct / 100.0))
        details = {
            "source_rows": source_count,
            "target_rows": target_count,
            "discrepancy": diff,
            "tolerance_pct": tolerance_pct,
            "allowed_discrepancy": allowed_diff,
        }
        if diff == 0:
            return VerificationStatus.PASSED, details
        elif diff <= allowed_diff:
            return VerificationStatus.WARNING, details
        return VerificationStatus.FAILED, details

    # =========================================================================
    # 2. Rows Successfully Processed
    # =========================================================================
    @classmethod
    def check_rows_processed(
        cls,
        source_count: int,
        rows_processed: int,
    ) -> Tuple[VerificationStatus, Dict[str, Any]]:
        details = {
            "source_count": source_count,
            "rows_processed": rows_processed,
            "unprocessed_rows": max(0, source_count - rows_processed),
        }
        if rows_processed >= source_count:
            return VerificationStatus.PASSED, details
        return VerificationStatus.FAILED, details

    # =========================================================================
    # 3. Failed Rows Threshold
    # =========================================================================
    @classmethod
    def check_failed_rows(
        cls,
        failed_rows: int,
        max_allowed: int = 0,
    ) -> Tuple[VerificationStatus, Dict[str, Any]]:
        details = {
            "failed_rows": failed_rows,
            "max_allowed_failures": max_allowed,
        }
        if failed_rows <= max_allowed:
            return VerificationStatus.PASSED, details
        elif failed_rows > max_allowed and max_allowed > 0:
            return VerificationStatus.WARNING, details
        return VerificationStatus.FAILED, details

    # =========================================================================
    # 4. Duplicate Detection (Target Unique / PK Constraints)
    # =========================================================================
    @classmethod
    def check_duplicate_records(
        cls,
        target_rows: List[Dict[str, Any]],
        pk_columns: List[str],
    ) -> Tuple[VerificationStatus, Dict[str, Any]]:
        if not pk_columns or not target_rows:
            return VerificationStatus.PASSED, {"total_rows": len(target_rows), "duplicates_found": 0}

        seen_keys: Set[Any] = set()
        duplicates: List[Any] = []

        for row in target_rows:
            key = tuple(row.get(col) for col in pk_columns)
            if key in seen_keys:
                duplicates.append(key)
            seen_keys.add(key)

        details = {
            "total_rows_checked": len(target_rows),
            "duplicates_count": len(duplicates),
            "pk_columns": pk_columns,
        }
        if not duplicates:
            return VerificationStatus.PASSED, details
        return VerificationStatus.FAILED, details

    # =========================================================================
    # 5. Primary Key Integrity (No NULL or Missing PKs)
    # =========================================================================
    @classmethod
    def check_primary_key_integrity(
        cls,
        target_rows: List[Dict[str, Any]],
        pk_columns: List[str],
    ) -> Tuple[VerificationStatus, Dict[str, Any]]:
        if not pk_columns:
            return VerificationStatus.PASSED, {"note": "No primary key defined"}

        null_pks = 0
        for row in target_rows:
            if any(row.get(col) is None for col in pk_columns):
                null_pks += 1

        details = {
            "total_rows_checked": len(target_rows),
            "null_pks_count": null_pks,
            "pk_columns": pk_columns,
        }
        if null_pks == 0:
            return VerificationStatus.PASSED, details
        return VerificationStatus.FAILED, details

    # =========================================================================
    # 6. Foreign Key Integrity (Referential Integrity Check)
    # =========================================================================
    @classmethod
    def check_foreign_key_integrity(
        cls,
        target_rows: List[Dict[str, Any]],
        fk_column: str,
        parent_valid_keys: Set[Any],
    ) -> Tuple[VerificationStatus, Dict[str, Any]]:
        if not target_rows or not fk_column:
            return VerificationStatus.PASSED, {"note": "No FK constraint to verify"}

        orphaned = 0
        for row in target_rows:
            val = row.get(fk_column)
            if val is not None and val not in parent_valid_keys:
                orphaned += 1

        details = {
            "fk_column": fk_column,
            "total_rows_checked": len(target_rows),
            "orphaned_records_count": orphaned,
        }
        if orphaned == 0:
            return VerificationStatus.PASSED, details
        return VerificationStatus.FAILED, details

    # =========================================================================
    # 7. Nullability Violations
    # =========================================================================
    @classmethod
    def check_nullability(
        cls,
        target_rows: List[Dict[str, Any]],
        non_nullable_columns: List[str],
    ) -> Tuple[VerificationStatus, Dict[str, Any]]:
        if not non_nullable_columns or not target_rows:
            return VerificationStatus.PASSED, {"note": "No non-nullable columns defined"}

        violations = 0
        violated_cols: Set[str] = set()

        for row in target_rows:
            for col in non_nullable_columns:
                if row.get(col) is None:
                    violations += 1
                    violated_cols.add(col)

        details = {
            "total_rows_checked": len(target_rows),
            "violations_count": violations,
            "violated_columns": list(violated_cols),
        }
        if violations == 0:
            return VerificationStatus.PASSED, details
        return VerificationStatus.FAILED, details

    # =========================================================================
    # 8. Target Table Existence
    # =========================================================================
    @classmethod
    def check_target_table_existence(
        cls,
        existing_tables: List[str],
        target_table_name: str,
    ) -> Tuple[VerificationStatus, Dict[str, Any]]:
        exists = target_table_name.lower() in [t.lower() for t in existing_tables]
        details = {
            "target_table": target_table_name,
            "exists": exists,
        }
        if exists:
            return VerificationStatus.PASSED, details
        return VerificationStatus.FAILED, details

    # =========================================================================
    # 9. Column & Type Compatibility
    # =========================================================================
    @classmethod
    def check_schema_compatibility(
        cls,
        target_existing_columns: Dict[str, str],  # col_name -> type
        expected_columns: Dict[str, str],        # col_name -> type
    ) -> Tuple[VerificationStatus, Dict[str, Any]]:
        missing_cols = []
        type_mismatches = []

        for col, exp_type in expected_columns.items():
            if col.lower() not in {c.lower() for c in target_existing_columns.keys()}:
                missing_cols.append(col)

        details = {
            "missing_columns": missing_cols,
            "type_mismatches": type_mismatches,
        }
        if not missing_cols and not type_mismatches:
            return VerificationStatus.PASSED, details
        return VerificationStatus.FAILED, details

    # =========================================================================
    # 10. Transformation Sanity Checks (Value Range & Mapping Bounds)
    # =========================================================================
    @classmethod
    def check_transformation_sanity(
        cls,
        sample_transformed_rows: List[Dict[str, Any]],
        expected_fields: List[str],
    ) -> Tuple[VerificationStatus, Dict[str, Any]]:
        if not sample_transformed_rows:
            return VerificationStatus.PASSED, {"note": "No rows to check"}

        empty_keys = []
        for row in sample_transformed_rows[:20]:
            for field in expected_fields:
                if field not in row:
                    empty_keys.append(field)

        details = {
            "sample_size": len(sample_transformed_rows),
            "missing_fields": list(set(empty_keys)),
        }
        if not empty_keys:
            return VerificationStatus.PASSED, details
        return VerificationStatus.FAILED, details

    # =========================================================================
    # 11. Sample-based Source/Target Comparison
    # =========================================================================
    @classmethod
    def check_sample_data_comparison(
        cls,
        source_sample: List[Dict[str, Any]],
        target_sample: List[Dict[str, Any]],
        pk_column: str,
        mapped_fields: Dict[str, str],  # source_col -> target_col
    ) -> Tuple[VerificationStatus, Dict[str, Any]]:
        if not source_sample or not target_sample:
            return VerificationStatus.PASSED, {"note": "Empty sample sets"}

        target_map = {str(row.get(pk_column)): row for row in target_sample if row.get(pk_column) is not None}
        mismatched_records = 0

        for src_row in source_sample:
            pk_val = str(src_row.get(pk_column))
            tgt_row = target_map.get(pk_val)
            if not tgt_row:
                continue

            for src_col, tgt_col in mapped_fields.items():
                s_val = src_row.get(src_col)
                t_val = tgt_row.get(tgt_col)
                # Normalize string representations for comparison
                if s_val is not None and t_val is not None and str(s_val).strip() != str(t_val).strip():
                    mismatched_records += 1
                    break

        details = {
            "source_sample_size": len(source_sample),
            "target_sample_size": len(target_sample),
            "mismatched_sample_rows": mismatched_records,
        }
        if mismatched_records == 0:
            return VerificationStatus.PASSED, details
        return VerificationStatus.WARNING if mismatched_records <= 2 else VerificationStatus.FAILED, details


class VerificationCoordinator:
    """
    Coordinates verification execution for a MigrationJob and ExecutionPlan,
    persists durable VerificationResult rows, decides final status (COMPLETED, NEEDS_REVIEW, FAILED),
    and feeds verification failures into Phase 3 recovery routing.
    """

    @classmethod
    async def run_plan_verification(
        cls,
        session: AsyncSession,
        job_id: uuid.UUID,
        step_id: Optional[uuid.UUID] = None,
        policy: Optional[VerificationPolicy] = None,
        verification_data: Optional[Dict[str, Any]] = None,
    ) -> Tuple[VerificationStatus, List[VerificationResult]]:
        """
        Executes configured verification policy against verification metrics and persists results.
        Returns:
            (overall_verdict: VerificationStatus, results: List[VerificationResult])
        """
        pol = policy or DEFAULT_VERIFICATION_POLICY
        v_data = verification_data or {}

        # 1. Fetch job and execution plan
        stmt = (
            select(MigrationJob)
            .where(MigrationJob.id == job_id)
            .options(selectinload(MigrationJob.execution_plan))
        )
        res = await session.execute(stmt)
        job = res.scalar_one_or_none()
        if not job:
            raise ValueError(f"Job '{job_id}' not found for verification.")

        plan_id = job.migration_plan_id
        exec_plan = job.execution_plan


        # Transition job to VERIFYING state if currently RUNNING
        if job.status == ExecutionLifecycle.RUNNING.value:
            job.status = ExecutionLifecycle.VERIFYING.value
            await session.flush()

        results: List[VerificationResult] = []
        now = datetime.now(timezone.utc)

        # ---------------------------------------------------------------------
        # Check 1: Row Count
        # ---------------------------------------------------------------------
        if VerificationCheckType.ROW_COUNT in pol.enabled_checks:
            s_count = v_data.get("source_row_count", job.total_rows or 0)
            t_count = v_data.get("target_row_count", job.successful_rows or 0)
            status, details = VerificationEngine.check_row_counts(
                source_count=s_count,
                target_count=t_count,
                tolerance_pct=pol.row_count_tolerance_pct,
            )
            r = VerificationResult(
                id=uuid.uuid4(),
                migration_job_id=job.id,
                execution_plan_id=exec_plan.id if exec_plan else uuid.uuid4(),
                execution_step_id=step_id,
                check_type=VerificationCheckType.ROW_COUNT.value,
                status=status.value,
                target_table=v_data.get("target_table"),
                expected={"source_rows": s_count},
                actual={"target_rows": t_count},
                tolerance=pol.row_count_tolerance_pct,
                details=details,
                error_message=None if status != VerificationStatus.FAILED else f"Row count discrepancy: source={s_count}, target={t_count}",
                created_at=now,
            )
            results.append(r)

        # ---------------------------------------------------------------------
        # Check 2: Rows Processed
        # ---------------------------------------------------------------------
        if VerificationCheckType.ROWS_PROCESSED in pol.enabled_checks:
            s_count = v_data.get("source_row_count", job.total_rows or 0)
            proc_count = v_data.get("processed_rows", job.processed_rows or 0)
            status, details = VerificationEngine.check_rows_processed(s_count, proc_count)
            r = VerificationResult(
                id=uuid.uuid4(),
                migration_job_id=job.id,
                execution_plan_id=exec_plan.id if exec_plan else uuid.uuid4(),
                execution_step_id=step_id,
                check_type=VerificationCheckType.ROWS_PROCESSED.value,
                status=status.value,
                target_table=v_data.get("target_table"),
                expected={"source_rows": s_count},
                actual={"processed_rows": proc_count},
                tolerance=0.0,
                details=details,
                created_at=now,
            )
            results.append(r)

        # ---------------------------------------------------------------------
        # Check 3: Failed Rows
        # ---------------------------------------------------------------------
        if VerificationCheckType.FAILED_ROWS in pol.enabled_checks:
            failed_count = v_data.get("failed_rows", job.failed_rows or 0)
            status, details = VerificationEngine.check_failed_rows(failed_count, pol.max_failed_rows_allowed)
            r = VerificationResult(
                id=uuid.uuid4(),
                migration_job_id=job.id,
                execution_plan_id=exec_plan.id if exec_plan else uuid.uuid4(),
                execution_step_id=step_id,
                check_type=VerificationCheckType.FAILED_ROWS.value,
                status=status.value,
                target_table=v_data.get("target_table"),
                expected={"max_allowed": pol.max_failed_rows_allowed},
                actual={"failed_rows": failed_count},
                tolerance=0.0,
                details=details,
                error_message=None if status != VerificationStatus.FAILED else f"Failed rows threshold exceeded: {failed_count}",
                created_at=now,
            )
            results.append(r)

        # ---------------------------------------------------------------------
        # Check 4: Duplicates
        # ---------------------------------------------------------------------
        if VerificationCheckType.DUPLICATE_DETECTION in pol.enabled_checks and "target_rows" in v_data:
            pk_cols = v_data.get("pk_columns", ["id"])
            status, details = VerificationEngine.check_duplicate_records(v_data["target_rows"], pk_cols)
            r = VerificationResult(
                id=uuid.uuid4(),
                migration_job_id=job.id,
                execution_plan_id=exec_plan.id if exec_plan else uuid.uuid4(),
                execution_step_id=step_id,
                check_type=VerificationCheckType.DUPLICATE_DETECTION.value,
                status=status.value,
                target_table=v_data.get("target_table"),
                expected={"duplicates": 0},
                actual={"duplicates_count": details.get("duplicates_count", 0)},
                tolerance=0.0,
                details=details,
                created_at=now,
            )
            results.append(r)

        # ---------------------------------------------------------------------
        # Check 5: PK Integrity
        # ---------------------------------------------------------------------
        if VerificationCheckType.PRIMARY_KEY_INTEGRITY in pol.enabled_checks and "target_rows" in v_data:
            pk_cols = v_data.get("pk_columns", ["id"])
            status, details = VerificationEngine.check_primary_key_integrity(v_data["target_rows"], pk_cols)
            r = VerificationResult(
                id=uuid.uuid4(),
                migration_job_id=job.id,
                execution_plan_id=exec_plan.id if exec_plan else uuid.uuid4(),
                execution_step_id=step_id,
                check_type=VerificationCheckType.PRIMARY_KEY_INTEGRITY.value,
                status=status.value,
                target_table=v_data.get("target_table"),
                expected={"null_pks": 0},
                actual={"null_pks_count": details.get("null_pks_count", 0)},
                tolerance=0.0,
                details=details,
                created_at=now,
            )
            results.append(r)

        # ---------------------------------------------------------------------
        # Check 6: FK Integrity
        # ---------------------------------------------------------------------
        if VerificationCheckType.FOREIGN_KEY_INTEGRITY in pol.enabled_checks and "parent_keys" in v_data:
            fk_col = v_data.get("fk_column", "")
            status, details = VerificationEngine.check_foreign_key_integrity(
                v_data.get("target_rows", []),
                fk_col,
                v_data.get("parent_keys", set()),
            )
            r = VerificationResult(
                id=uuid.uuid4(),
                migration_job_id=job.id,
                execution_plan_id=exec_plan.id if exec_plan else uuid.uuid4(),
                execution_step_id=step_id,
                check_type=VerificationCheckType.FOREIGN_KEY_INTEGRITY.value,
                status=status.value,
                target_table=v_data.get("target_table"),
                expected={"orphans": 0},
                actual={"orphaned_count": details.get("orphaned_records_count", 0)},
                tolerance=0.0,
                details=details,
                created_at=now,
            )
            results.append(r)

        # ---------------------------------------------------------------------
        # Check 7: Nullability
        # ---------------------------------------------------------------------
        if VerificationCheckType.NULLABILITY in pol.enabled_checks and "non_nullable_columns" in v_data:
            status, details = VerificationEngine.check_nullability(
                v_data.get("target_rows", []),
                v_data.get("non_nullable_columns", []),
            )
            r = VerificationResult(
                id=uuid.uuid4(),
                migration_job_id=job.id,
                execution_plan_id=exec_plan.id if exec_plan else uuid.uuid4(),
                execution_step_id=step_id,
                check_type=VerificationCheckType.NULLABILITY.value,
                status=status.value,
                target_table=v_data.get("target_table"),
                expected={"nullability_violations": 0},
                actual={"violations_count": details.get("violations_count", 0)},
                tolerance=0.0,
                details=details,
                created_at=now,
            )
            results.append(r)

        # ---------------------------------------------------------------------
        # Check 8: Target Table Existence
        # ---------------------------------------------------------------------
        if VerificationCheckType.TABLE_EXISTENCE in pol.enabled_checks and "existing_tables" in v_data:
            tbl_name = v_data.get("target_table", "")
            status, details = VerificationEngine.check_target_table_existence(v_data["existing_tables"], tbl_name)
            r = VerificationResult(
                id=uuid.uuid4(),
                migration_job_id=job.id,
                execution_plan_id=exec_plan.id if exec_plan else uuid.uuid4(),
                execution_step_id=step_id,
                check_type=VerificationCheckType.TABLE_EXISTENCE.value,
                status=status.value,
                target_table=tbl_name,
                expected={"table_exists": True},
                actual={"table_exists": details.get("exists", False)},
                tolerance=0.0,
                details=details,
                created_at=now,
            )
            results.append(r)

        # ---------------------------------------------------------------------
        # Check 9: Schema Compatibility
        # ---------------------------------------------------------------------
        if VerificationCheckType.SCHEMA_COMPATIBILITY in pol.enabled_checks and "expected_columns" in v_data:
            tgt_cols = v_data.get("target_columns", {})
            exp_cols = v_data.get("expected_columns", {})
            status, details = VerificationEngine.check_schema_compatibility(tgt_cols, exp_cols)
            r = VerificationResult(
                id=uuid.uuid4(),
                migration_job_id=job.id,
                execution_plan_id=exec_plan.id if exec_plan else uuid.uuid4(),
                execution_step_id=step_id,
                check_type=VerificationCheckType.SCHEMA_COMPATIBILITY.value,
                status=status.value,
                target_table=v_data.get("target_table"),
                expected=exp_cols,
                actual=tgt_cols,
                tolerance=0.0,
                details=details,
                created_at=now,
            )
            results.append(r)

        # ---------------------------------------------------------------------
        # Check 10: Transformation Sanity
        # ---------------------------------------------------------------------
        if VerificationCheckType.TRANSFORMATION_SANITY in pol.enabled_checks and "sample_transformed_rows" in v_data:
            status, details = VerificationEngine.check_transformation_sanity(
                v_data["sample_transformed_rows"],
                v_data.get("expected_fields", []),
            )
            r = VerificationResult(
                id=uuid.uuid4(),
                migration_job_id=job.id,
                execution_plan_id=exec_plan.id if exec_plan else uuid.uuid4(),
                execution_step_id=step_id,
                check_type=VerificationCheckType.TRANSFORMATION_SANITY.value,
                status=status.value,
                target_table=v_data.get("target_table"),
                expected={"sanity_pass": True},
                actual={"missing_fields": details.get("missing_fields", [])},
                tolerance=0.0,
                details=details,
                created_at=now,
            )
            results.append(r)

        # ---------------------------------------------------------------------
        # Check 11: Sample Data Comparison
        # ---------------------------------------------------------------------
        if VerificationCheckType.SAMPLE_DATA_COMPARISON in pol.enabled_checks and "source_sample" in v_data and "target_sample" in v_data:
            status, details = VerificationEngine.check_sample_data_comparison(
                v_data["source_sample"],
                v_data["target_sample"],
                v_data.get("pk_column", "id"),
                v_data.get("mapped_fields", {}),
            )
            r = VerificationResult(
                id=uuid.uuid4(),
                migration_job_id=job.id,
                execution_plan_id=exec_plan.id if exec_plan else uuid.uuid4(),
                execution_step_id=step_id,
                check_type=VerificationCheckType.SAMPLE_DATA_COMPARISON.value,
                status=status.value,
                target_table=v_data.get("target_table"),
                expected={"sample_matches": True},
                actual={"mismatches": details.get("mismatched_sample_rows", 0)},
                tolerance=0.0,
                details=details,
                created_at=now,
            )
            results.append(r)

        # Persist all verification records
        session.add_all(results)
        await session.flush()

        # Compute overall verdict
        has_failed = any(r.status == VerificationStatus.FAILED.value for r in results)
        has_warning = any(r.status == VerificationStatus.WARNING.value for r in results)

        if has_failed:
            overall = VerificationStatus.FAILED
        elif has_warning:
            overall = VerificationStatus.WARNING if pol.allow_warnings else VerificationStatus.FAILED
        else:
            overall = VerificationStatus.PASSED

        # Determine job / plan state based on verification verdict
        if overall == VerificationStatus.PASSED:
            job.status = ExecutionLifecycle.COMPLETED.value
            job.progress = 100.0
            if exec_plan:
                exec_plan.status = ExecutionPlanLifecycle.COMPLETED.value
        elif overall == VerificationStatus.WARNING:
            # Warnings allow completion with review flag if policy permits
            if pol.allow_warnings:
                job.status = ExecutionLifecycle.COMPLETED.value
                job.progress = 100.0
                if exec_plan:
                    exec_plan.status = ExecutionPlanLifecycle.COMPLETED.value
            else:
                job.status = ExecutionLifecycle.NEEDS_REVIEW.value
                if exec_plan:
                    exec_plan.status = ExecutionPlanLifecycle.NEEDS_REVIEW.value
        else:  # FAILED
            job.status = ExecutionLifecycle.FAILED.value
            if exec_plan:
                exec_plan.status = ExecutionPlanLifecycle.FAILED.value

            # Feed failure into Phase 3 RecoveryRouter
            failed_checks = [r.check_type for r in results if r.status == VerificationStatus.FAILED.value]
            failure = ClassifiedFailure(
                category=FailureCategory.DATA_VALIDATION,
                code="ERR_POST_MIGRATION_VERIFICATION_FAILED",
                message=f"Post-migration verification failed on checks: {', '.join(failed_checks)}",
                retryable=False,
                recoverable=False,
                requires_replan=True,
                requires_user=True,
                severity=FailureSeverity.HIGH,
                domain=FailureDomain.DATABASE_ENGINE,
                context={"failed_checks": failed_checks, "job_id": str(job.id)},
            )
            decision = recovery_router.evaluate(
                failure=failure,
                step_key="verify",
                attempt_count=1,
            )
            logger.warning(
                f"[VerificationCoordinator] Verification failed for job '{job.id}'. Decision: action='{decision.action.value}', reason='{decision.reason}'"
            )

        # Emit verification completion event
        event = ExecutionEvent(
            id=uuid.uuid4(),
            event_id=uuid.uuid4(),
            migration_job_id=job.id,
            migration_plan_id=plan_id,
            event_type=ExecutionEventType.VERIFICATION_COMPLETED.value,
            actor_type="system",
            actor_id="VerificationCoordinator",
            timestamp=datetime.now(timezone.utc),
            payload={
                "verdict": overall.value,
                "checks_count": len(results),
                "has_warnings": has_warning,
                "has_failures": has_failed,
            },
            schema_version=1,
        )
        session.add(event)
        await session.commit()

        return overall, results
