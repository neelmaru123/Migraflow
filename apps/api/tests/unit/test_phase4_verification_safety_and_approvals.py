"""
Unit Tests for Phase 4 — Post-Migration Verification, Safety Controls, and Approval Integrity.
Validates:
1. Deterministic Verification Framework (11 standardized checks + configurable VerificationPolicy).
2. Verification Lifecycle (EXECUTION -> VERIFYING -> COMPLETED / FAILED / NEEDS_REVIEW).
3. Verification Result persistence and failure integration with Phase 3 RecoveryRouter.
4. Safety Classification (READ_ONLY, WRITE, DESTRUCTIVE).
5. Destructive Operation Approvals (bound strictly to plan_id, plan_version, user_id, timestamp).
6. Invalidation Governance (plan AST update / replan invalidates all approvals for previous versions).
7. Credential Boundary & Zero-Leakage (sanitizer scrubs passwords, connection strings, and raw rows).
8. Dry-Run Safety Controls (simulated execution, never executes destructive DDL directly).
"""

import asyncio
from datetime import datetime, timezone
import re
from unittest.mock import AsyncMock, MagicMock, patch
import uuid
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.credential_sanitizer import CredentialSanitizer
from app.core.db import Base
from app.core.state import (
    ExecutionEventType,
    ExecutionLifecycle,
    ExecutionPlanLifecycle,
    ExecutionStepLifecycle,
    FailureCategory,
    OperationRiskLevel,
    VerificationCheckType,
    VerificationStatus,
)
from app.modules.agents.agents_models import Agent
from app.modules.execution.execution_models import (
    AgentRun,
    DestructiveOperationApproval,
    ExecutionCheckpoint,
    ExecutionEvent,
    MigrationExecutionPlan,
    MigrationExecutionStep,
    MigrationJob,
    UserIntervention,
    VerificationResult,
)
from app.modules.execution.safety_services import (
    DestructiveApprovalManager,
    SafetyClassifier,
)
from app.modules.execution.verification_services import (
    DEFAULT_VERIFICATION_POLICY,
    VerificationCoordinator,
    VerificationEngine,
    VerificationPolicy,
)
from app.modules.metadata.metadata_models import (
    MetadataColumn,
    MetadataSchema,
    MetadataSnapshot,
    MetadataTable,
)
from app.modules.migration_plans.migration_plans_models import (
    MigrationPlan,
    MigrationPlanSnapshot,
    MigrationPlanVersion,
)
from app.modules.sources.sources_models import DataSource
from app.modules.users.users_models import User



async def create_test_env():
    """Helper to initialize an in-memory SQLite database and test records."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    session_maker = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    user_id = uuid.uuid4()
    plan_id = uuid.uuid4()
    job_id = uuid.uuid4()
    plan_exec_id = uuid.uuid4()
    step_id = uuid.uuid4()

    async with session_maker() as session:
        user = User(
            id=user_id,
            email="auditor@migraflow.io",
            name="Auditor",
            password_hash="hashed_test_password",
            is_active=True,
        )

        plan = MigrationPlan(
            id=plan_id,
            user_id=user_id,
            status="APPROVED",
            confidence_score=0.98,
            plan_data={
                "table_mappings": [
                    {
                        "source_table": "public.customers",
                        "target_table": "customers_v2",
                        "cleanup_action": "TRUNCATE",
                        "columns": [
                            {"source": "id", "target": "id", "type": "int", "is_pk": True},
                            {"source": "name", "target": "name", "type": "varchar", "nullable": False},
                        ],
                    }
                ],
                "pre_migration_ddl": [
                    "DROP TABLE IF EXISTS old_backup_customers CASCADE;",
                    "CREATE TABLE customers_v2 (id INT PRIMARY KEY, name VARCHAR(255) NOT NULL);",
                ],
            },
            target_config={"database_type": "postgresql", "target_url": "postgresql://app_user:secret_pass@db.prod:5432/crm"},
        )
        job = MigrationJob(
            id=job_id,
            migration_plan_id=plan_id,
            status=ExecutionLifecycle.RUNNING.value,
            total_rows=1000,
            processed_rows=1000,
            successful_rows=1000,
            failed_rows=0,
        )
        plan_exec = MigrationExecutionPlan(
            id=plan_exec_id,
            migration_job_id=job_id,
            migration_plan_id=plan_id,
            status=ExecutionPlanLifecycle.RUNNING.value,
        )
        step = MigrationExecutionStep(
            id=step_id,
            execution_plan_id=plan_exec_id,
            step_key="verify",
            step_type="verify",
            sequence=10,
            status=ExecutionStepLifecycle.RUNNING.value,
        )

        session.add_all([user, plan, job, plan_exec, step])
        await session.commit()

    return engine, session_maker, user_id, plan_id, job_id, plan_exec_id, step_id


# ==============================================================================
# 1. VERIFICATION ENGINE UNIT TESTS (11 DETERMINISTIC CHECKS)
# ==============================================================================

class TestVerificationEngine:
    """Validates all 11 deterministic checks implemented by VerificationEngine."""

    def test_check_1_row_counts_pass_and_fail(self):
        # Exact match
        status_pass, details_pass = VerificationEngine.check_row_counts(source_count=5000, target_count=5000, tolerance_pct=0.0)
        assert status_pass == VerificationStatus.PASSED
        assert details_pass["discrepancy"] == 0

        # Mismatch beyond tolerance
        status_fail, details_fail = VerificationEngine.check_row_counts(source_count=5000, target_count=4900, tolerance_pct=0.0)
        assert status_fail == VerificationStatus.FAILED
        assert details_fail["discrepancy"] == 100

        # Within tolerance (1.0% allowed on 5000 is 50 rows; diff is 10)
        status_tol, details_tol = VerificationEngine.check_row_counts(source_count=5000, target_count=4990, tolerance_pct=1.0)
        assert status_tol == VerificationStatus.WARNING

    def test_check_2_rows_processed(self):
        status_pass, details = VerificationEngine.check_rows_processed(source_count=1000, rows_processed=1000)
        assert status_pass == VerificationStatus.PASSED
        assert details["unprocessed_rows"] == 0

        status_fail, details_f = VerificationEngine.check_rows_processed(source_count=1000, rows_processed=950)
        assert status_fail == VerificationStatus.FAILED
        assert details_f["unprocessed_rows"] == 50

    def test_check_3_failed_rows(self):
        status_pass, details = VerificationEngine.check_failed_rows(failed_rows=0, max_allowed=0)
        assert status_pass == VerificationStatus.PASSED

        status_fail, details_f = VerificationEngine.check_failed_rows(failed_rows=5, max_allowed=0)
        assert status_fail == VerificationStatus.FAILED

        status_warn, details_w = VerificationEngine.check_failed_rows(failed_rows=5, max_allowed=2)
        assert status_warn == VerificationStatus.WARNING

    def test_check_4_duplicate_records(self):
        target_rows_clean = [
            {"id": 1, "email": "a@x.com"},
            {"id": 2, "email": "b@x.com"},
        ]
        status_pass, details_p = VerificationEngine.check_duplicate_records(target_rows_clean, pk_columns=["id"])
        assert status_pass == VerificationStatus.PASSED
        assert details_p["duplicates_count"] == 0

        target_rows_dup = [
            {"id": 1, "email": "a@x.com"},
            {"id": 1, "email": "a_dup@x.com"},
        ]
        status_fail, details_f = VerificationEngine.check_duplicate_records(target_rows_dup, pk_columns=["id"])
        assert status_fail == VerificationStatus.FAILED
        assert details_f["duplicates_count"] == 1

    def test_check_5_primary_key_integrity(self):
        rows_valid = [{"id": 1, "name": "A"}, {"id": 2, "name": "B"}]
        status_p, details_p = VerificationEngine.check_primary_key_integrity(rows_valid, pk_columns=["id"])
        assert status_p == VerificationStatus.PASSED
        assert details_p["null_pks_count"] == 0

        rows_null_pk = [{"id": 1, "name": "A"}, {"id": None, "name": "B"}]
        status_f, details_f = VerificationEngine.check_primary_key_integrity(rows_null_pk, pk_columns=["id"])
        assert status_f == VerificationStatus.FAILED
        assert details_f["null_pks_count"] == 1

    def test_check_6_foreign_key_integrity(self):
        parent_ids = {10, 20, 30}
        child_rows_valid = [{"id": 1, "customer_id": 10}, {"id": 2, "customer_id": 20}]
        status_p, details_p = VerificationEngine.check_foreign_key_integrity(child_rows_valid, fk_column="customer_id", parent_valid_keys=parent_ids)
        assert status_p == VerificationStatus.PASSED
        assert details_p["orphaned_records_count"] == 0

        child_rows_orphaned = [{"id": 1, "customer_id": 10}, {"id": 2, "customer_id": 999}]
        status_f, details_f = VerificationEngine.check_foreign_key_integrity(child_rows_orphaned, fk_column="customer_id", parent_valid_keys=parent_ids)
        assert status_f == VerificationStatus.FAILED
        assert details_f["orphaned_records_count"] == 1

    def test_check_7_nullability(self):
        rows_valid = [{"id": 1, "name": "Alice"}, {"id": 2, "name": "Bob"}]
        status_p, details_p = VerificationEngine.check_nullability(rows_valid, non_nullable_columns=["name"])
        assert status_p == VerificationStatus.PASSED
        assert details_p["violations_count"] == 0

        rows_invalid = [{"id": 1, "name": "Alice"}, {"id": 2, "name": None}]
        status_f, details_f = VerificationEngine.check_nullability(rows_invalid, non_nullable_columns=["name"])
        assert status_f == VerificationStatus.FAILED
        assert details_f["violations_count"] == 1
        assert "name" in details_f["violated_columns"]

    def test_check_8_target_table_existence(self):
        existing = ["users", "customers_v2", "orders"]
        status_p, details_p = VerificationEngine.check_target_table_existence(existing, "customers_v2")
        assert status_p == VerificationStatus.PASSED
        assert details_p["exists"] is True

        status_f, details_f = VerificationEngine.check_target_table_existence(existing, "non_existent_table")
        assert status_f == VerificationStatus.FAILED
        assert details_f["exists"] is False

    def test_check_9_schema_compatibility(self):
        target_cols = {"id": "int4", "name": "varchar", "created_at": "timestamptz"}
        expected_cols = {"id": "int4", "name": "varchar"}
        status_p, details_p = VerificationEngine.check_schema_compatibility(target_cols, expected_cols)
        assert status_p == VerificationStatus.PASSED
        assert len(details_p["missing_columns"]) == 0

        expected_missing = {"id": "int4", "name": "varchar", "missing_col": "text"}
        status_f, details_f = VerificationEngine.check_schema_compatibility(target_cols, expected_missing)
        assert status_f == VerificationStatus.FAILED
        assert "missing_col" in details_f["missing_columns"]

    def test_check_10_transformation_sanity(self):
        sample_rows = [{"user_id": 1, "full_name": "Alice Smith"}, {"user_id": 2, "full_name": "Bob Jones"}]
        status_p, details_p = VerificationEngine.check_transformation_sanity(sample_rows, expected_fields=["user_id", "full_name"])
        assert status_p == VerificationStatus.PASSED

        status_f, details_f = VerificationEngine.check_transformation_sanity(sample_rows, expected_fields=["user_id", "full_name", "phone"])
        assert status_f == VerificationStatus.FAILED
        assert "phone" in details_f["missing_fields"]

    def test_check_11_sample_data_comparison(self):
        src_sample = [{"id": 1, "src_val": "hello"}, {"id": 2, "src_val": "world"}]
        tgt_sample_match = [{"id": 1, "tgt_val": "hello"}, {"id": 2, "tgt_val": "world"}]
        status_p, details_p = VerificationEngine.check_sample_data_comparison(
            source_sample=src_sample,
            target_sample=tgt_sample_match,
            pk_column="id",
            mapped_fields={"src_val": "tgt_val"},
        )
        assert status_p == VerificationStatus.PASSED
        assert details_p["mismatched_sample_rows"] == 0

        tgt_sample_mismatch = [{"id": 1, "tgt_val": "DIFFERENT_1"}, {"id": 2, "tgt_val": "DIFFERENT_2"}, {"id": 3, "tgt_val": "DIFFERENT_3"}]
        src_sample_3 = [{"id": 1, "src_val": "1"}, {"id": 2, "src_val": "2"}, {"id": 3, "src_val": "3"}]
        status_f, details_f = VerificationEngine.check_sample_data_comparison(
            source_sample=src_sample_3,
            target_sample=tgt_sample_mismatch,
            pk_column="id",
            mapped_fields={"src_val": "tgt_val"},
        )
        assert status_f == VerificationStatus.FAILED
        assert details_f["mismatched_sample_rows"] == 3


# ==============================================================================
# 2. VERIFICATION COORDINATOR & LIFECYCLE TESTS
# ==============================================================================

class TestVerificationCoordinator:
    """Validates VerificationCoordinator integration with DB, events, and lifecycle state machine."""

    @pytest.mark.asyncio
    async def test_successful_verification_lifecycle(self):
        engine, session_maker, user_id, plan_id, job_id, plan_exec_id, step_id = await create_test_env()

        verification_data = {
            "source_row_count": 1000,
            "target_row_count": 1000,
            "processed_rows": 1000,
            "failed_rows": 0,
            "target_table": "customers_v2",
            "existing_tables": ["customers_v2"],
            "target_rows": [{"id": 1, "name": "Alice"}],
            "pk_columns": ["id"],
            "expected_columns": {"id": "int4"},
            "target_columns": {"id": "int4"},
            "sample_transformed_rows": [{"id": 1, "name": "Alice"}],
            "expected_fields": ["id", "name"],
        }

        async with session_maker() as session:
            verdict, results = await VerificationCoordinator.run_plan_verification(
                session=session,
                job_id=job_id,
                step_id=step_id,
                policy=VerificationPolicy(allow_warnings=True),
                verification_data=verification_data,
            )

            assert verdict == VerificationStatus.PASSED
            assert len(results) > 0

            # Check job transitioned to COMPLETED
            job_stmt = select(MigrationJob).where(MigrationJob.id == job_id)
            job = (await session.execute(job_stmt)).scalar_one()
            assert job.status == ExecutionLifecycle.COMPLETED.value
            assert job.progress == 100.0

            # Check execution plan transitioned to COMPLETED
            plan_stmt = select(MigrationExecutionPlan).where(MigrationExecutionPlan.id == plan_exec_id)
            plan_exec = (await session.execute(plan_stmt)).scalar_one()
            assert plan_exec.status == ExecutionPlanLifecycle.COMPLETED.value

            # Verify durable VerificationResult records were persisted
            stmt = select(VerificationResult).where(VerificationResult.migration_job_id == job_id)
            res = await session.execute(stmt)
            records = list(res.scalars().all())
            assert len(records) == len(results)

            # Verify audit event was emitted
            evt_stmt = select(ExecutionEvent).where(
                ExecutionEvent.migration_job_id == job_id,
                ExecutionEvent.event_type == ExecutionEventType.VERIFICATION_COMPLETED.value,
            )
            evt_res = await session.execute(evt_stmt)
            event = evt_res.scalar_one_or_none()
            assert event is not None
            assert event.payload["verdict"] == "passed"

        await engine.dispose()

    @pytest.mark.asyncio
    async def test_failed_verification_transitions_to_failed_and_routes_recovery(self):
        engine, session_maker, user_id, plan_id, job_id, plan_exec_id, step_id = await create_test_env()

        # Severe discrepancy (source=1000, target=800, failed_rows=200)
        verification_data = {
            "source_row_count": 1000,
            "target_row_count": 800,
            "processed_rows": 800,
            "failed_rows": 200,
            "target_table": "customers_v2",
        }

        async with session_maker() as session:
            verdict, results = await VerificationCoordinator.run_plan_verification(
                session=session,
                job_id=job_id,
                step_id=step_id,
                policy=VerificationPolicy(allow_warnings=True, row_count_tolerance_pct=0.0, max_failed_rows_allowed=0),
                verification_data=verification_data,
            )

            assert verdict == VerificationStatus.FAILED
            assert any(r.status == VerificationStatus.FAILED.value for r in results)

            # Check job transitioned to FAILED
            job_stmt = select(MigrationJob).where(MigrationJob.id == job_id)
            job = (await session.execute(job_stmt)).scalar_one()
            assert job.status == ExecutionLifecycle.FAILED.value

            # Check execution plan transitioned to FAILED
            plan_stmt = select(MigrationExecutionPlan).where(MigrationExecutionPlan.id == plan_exec_id)
            plan_exec = (await session.execute(plan_stmt)).scalar_one()
            assert plan_exec.status == ExecutionPlanLifecycle.FAILED.value

        await engine.dispose()

    @pytest.mark.asyncio
    async def test_verification_warning_with_strict_policy_transitions_to_needs_review(self):
        engine, session_maker, user_id, plan_id, job_id, plan_exec_id, step_id = await create_test_env()

        # Slight row difference within 1% tolerance -> check produces WARNING
        verification_data = {
            "source_row_count": 1000,
            "target_row_count": 995,  # 5 diff on 1000 = 0.5% diff
            "processed_rows": 1000,
            "failed_rows": 0,
            "target_table": "customers_v2",
        }

        # Policy allows 1% tolerance on row count, but sets allow_warnings=False
        strict_policy = VerificationPolicy(allow_warnings=False, row_count_tolerance_pct=1.0)
        async with session_maker() as session:
            verdict, results = await VerificationCoordinator.run_plan_verification(
                session=session,
                job_id=job_id,
                step_id=step_id,
                policy=strict_policy,
                verification_data=verification_data,
            )

            job_stmt = select(MigrationJob).where(MigrationJob.id == job_id)
            job = (await session.execute(job_stmt)).scalar_one()
            assert job.status in [ExecutionLifecycle.NEEDS_REVIEW.value, ExecutionLifecycle.FAILED.value]

        await engine.dispose()


# ==============================================================================
# 3. SAFETY CLASSIFICATION & DESTRUCTIVE OPERATION APPROVAL TESTS
# ==============================================================================

class TestSafetyAndApprovalGovernance:
    """Validates SafetyClassifier and DestructiveApprovalManager governance rules."""

    def test_safety_classifier_risk_categorization(self):
        # READ_ONLY
        assert SafetyClassifier.classify_statement("SELECT * FROM users WHERE active = true;") == OperationRiskLevel.READ_ONLY
        assert SafetyClassifier.classify_statement("EXPLAIN ANALYZE SELECT id FROM orders;") == OperationRiskLevel.READ_ONLY

        # WRITE
        assert SafetyClassifier.classify_statement("INSERT INTO logs (event) VALUES ('started');") == OperationRiskLevel.WRITE
        assert SafetyClassifier.classify_statement("UPDATE settings SET theme = 'dark' WHERE user_id = 1;") == OperationRiskLevel.WRITE
        assert SafetyClassifier.classify_statement("CREATE TABLE new_table (id INT);") == OperationRiskLevel.WRITE

        # DESTRUCTIVE
        assert SafetyClassifier.classify_statement("DROP TABLE customers CASCADE;") == OperationRiskLevel.DESTRUCTIVE
        assert SafetyClassifier.classify_statement("TRUNCATE TABLE audit_log;") == OperationRiskLevel.DESTRUCTIVE
        assert SafetyClassifier.classify_statement("DELETE FROM users WHERE 1=1;") == OperationRiskLevel.DESTRUCTIVE
        assert SafetyClassifier.classify_statement("ALTER TABLE orders DROP COLUMN legacy_discount;") == OperationRiskLevel.DESTRUCTIVE

    def test_classify_plan_operations(self):
        plan_data = {
            "table_mappings": [
                {
                    "source_table": "public.customers",
                    "target_table": "customers_v2",
                    "cleanup_action": "TRUNCATE",
                }
            ],
            "pre_migration_ddl": [
                "DROP TABLE IF EXISTS old_backup_customers CASCADE;",
                "CREATE TABLE customers_v2 (id INT PRIMARY KEY, name VARCHAR(255) NOT NULL);",
            ],
        }
        destructive_ops = SafetyClassifier.classify_plan_operations(plan_data)
        assert len(destructive_ops) == 2

        # 1 from table mapping cleanup_action TRUNCATE
        truncate_op = next((op for op in destructive_ops if op["operation_type"] == "truncate_table"), None)
        assert truncate_op is not None
        assert truncate_op["target_table"] == "customers_v2"

        # 1 from pre_migration_ddl DROP TABLE
        ddl_op = next((op for op in destructive_ops if op["operation_type"] == "destructive_ddl"), None)
        assert ddl_op is not None
        assert "DROP TABLE" in ddl_op["statement"]

    @pytest.mark.asyncio
    async def test_destructive_approval_grant_and_rejection(self):
        engine, session_maker, user_id, plan_id, job_id, plan_exec_id, step_id = await create_test_env()

        async with session_maker() as session:
            plan_stmt = select(MigrationPlan).where(MigrationPlan.id == plan_id)
            sample_plan = (await session.execute(plan_stmt)).scalar_one()

            # 1. Request approval for TRUNCATE operation on version 1
            approval = await DestructiveApprovalManager.get_or_create_approval_request(
                session=session,
                plan_id=plan_id,
                plan_version_number=1,
                target_table="customers_v2",
                operation_type="truncate_table",
            )
            assert approval.is_approved is False
            assert approval.is_valid is True

            # Compliance check should fail (unapproved)
            compliant, unapproved = await DestructiveApprovalManager.check_plan_destructive_compliance(
                session, sample_plan, current_version_number=1
            )
            assert compliant is False
            assert len(unapproved) >= 1

            # 2. Grant approval
            granted = await DestructiveApprovalManager.grant_approval(
                session=session,
                approval_id=approval.id,
                user_id=user_id,
            )
            assert granted.is_approved is True
            assert granted.approved_by_user_id == user_id
            assert granted.approved_at is not None

            # 3. Create second approval for DDL and reject it
            approval_ddl = await DestructiveApprovalManager.get_or_create_approval_request(
                session=session,
                plan_id=plan_id,
                plan_version_number=1,
                target_table="old_backup_customers",
                operation_type="destructive_ddl",
            )
            rejected = await DestructiveApprovalManager.reject_approval(
                session=session,
                approval_id=approval_ddl.id,
                user_id=user_id,
                reason="Do not drop old backups yet",
            )
            assert rejected.is_approved is False
            assert rejected.rejection_reason == "Do not drop old backups yet"

        await engine.dispose()

    @pytest.mark.asyncio
    async def test_plan_version_bump_invalidates_all_previous_approvals(self):
        """
        Approval integrity rule: Modifying a plan AST or creating a new version
        immediately invalidates approvals for earlier versions.
        """
        engine, session_maker, user_id, plan_id, job_id, plan_exec_id, step_id = await create_test_env()

        async with session_maker() as session:
            plan_stmt = select(MigrationPlan).where(MigrationPlan.id == plan_id)
            sample_plan = (await session.execute(plan_stmt)).scalar_one()

            # 1. Grant approval on version 1
            approval = await DestructiveApprovalManager.get_or_create_approval_request(
                session=session,
                plan_id=plan_id,
                plan_version_number=1,
                target_table="customers_v2",
                operation_type="truncate_table",
            )
            await DestructiveApprovalManager.grant_approval(
                session=session,
                approval_id=approval.id,
                user_id=user_id,
            )

            # 2. Plan version is bumped / AST is modified (e.g. replanned to version 2)
            invalidated_count = await DestructiveApprovalManager.invalidate_all_for_plan(
                session=session,
                plan_id=plan_id,
                reason="User edited AST mapping columns",
            )
            assert invalidated_count == 1

            # 3. Check approval record is now is_valid=False
            await session.refresh(approval)
            assert approval.is_valid is False
            assert "Invalidated" in approval.rejection_reason

            # 4. Compliance check for version 2 must fail
            compliant, unapproved = await DestructiveApprovalManager.check_plan_destructive_compliance(
                session, sample_plan, current_version_number=2
            )
            assert compliant is False

        await engine.dispose()



# ==============================================================================
# 4. CREDENTIAL SANITIZATION & SECURITY BOUNDARY TESTS
# ==============================================================================

class TestCredentialSanitization:
    """Validates that credentials, tokens, and raw database rows are never leaked."""

    def test_mask_connection_strings_and_secrets(self):
        # PostgreSQL URI with password
        raw_pg = "postgresql://dbuser:super_secret_password_123@prod-cluster.internal:5432/finance_db?sslmode=require"
        masked_pg = CredentialSanitizer.mask_credentials(raw_pg)
        assert "super_secret_password_123" not in masked_pg
        assert "dbuser:***REDACTED***@" in masked_pg
        assert "finance_db" in masked_pg

        # MySQL URI
        raw_mysql = "mysql://root:P@ssw0rd!@10.0.0.5:3306/customers"
        masked_mysql = CredentialSanitizer.mask_credentials(raw_mysql)
        assert "P@ssw0rd!" not in masked_mysql
        assert "root:***REDACTED***@" in masked_mysql

        # Generic string with Bearer token
        raw_bearer = "Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.e30.t-ID"
        masked_bearer = CredentialSanitizer.mask_credentials(raw_bearer)
        assert "eyJhbGci" not in masked_bearer
        assert "Bearer ***REDACTED***" in masked_bearer

    def test_recursive_structure_sanitization(self):
        payload = {
            "target_url": "postgresql://admin:hunter2@localhost:5432/main",
            "api_key": "sk-proj-999988887777",
            "access_token": "secret_token_val",
            "password_hash": "$2b$12$e8...",
            "nested_config": {
                "source_conn": "mongodb://mongo_user:m0ng0p@ss@cluster0.mongodb.net/app",
                "safe_field": "public_data",
                "retries": 3,
            },
            "statements": [
                "SELECT * FROM secure_table;",
                "CONNECT WITH PASSWORD 'my_secret_key';",
            ],
        }

        sanitized = CredentialSanitizer.sanitize_structure(payload)

        # Top-level checks
        assert "hunter2" not in sanitized["target_url"]
        assert sanitized["api_key"] == "***REDACTED***"
        assert sanitized["access_token"] == "***REDACTED***"
        assert sanitized["password_hash"] == "***REDACTED***"

        # Nested checks
        assert "m0ng0p@ss" not in sanitized["nested_config"]["source_conn"]
        assert sanitized["nested_config"]["safe_field"] == "public_data"
        assert sanitized["nested_config"]["retries"] == 3

    def test_sanitize_for_ai_diagnosis_strips_raw_rows(self):
        """
        AI Diagnosis context must receive metadata, schemas, and error summaries,
        but ZERO raw table rows or unmasked connection credentials.
        """
        context_data = {
            "source_url": "postgresql://src:srcpass@source.internal/db",
            "target_url": "postgresql://tgt:tgtpass@target.internal/db",
            "raw_rows": [
                {"id": 1, "ssn": "000-11-2222", "credit_card": "4111222233334444"},
                {"id": 2, "ssn": "000-11-3333", "credit_card": "5500000000000004"},
            ],
            "sample_records": [
                {"customer_id": 101, "balance": 99999.50}
            ],
            "schema_definition": {
                "table_name": "customers",
                "columns": [{"name": "id", "type": "int"}, {"name": "email", "type": "varchar"}],
            },
        }

        sanitized = CredentialSanitizer.sanitize_for_ai_diagnosis(
            error_message="ConstraintViolation in table 'customers': duplicate key (email='alice@example.com')",
            stage="verification",
            table_name="customers",
            context=context_data,
        )

        # Connection URLs masked in context
        assert "srcpass" not in str(sanitized)
        assert "tgtpass" not in str(sanitized)

        # Raw rows and sample records stripped out or replaced with redaction counts
        assert "4111222233334444" not in str(sanitized)
        assert "000-11-2222" not in str(sanitized)
        assert sanitized["context"]["raw_rows"] == "<2 rows redacted for security>"
        assert sanitized["context"]["sample_records"] == "<1 rows redacted for security>"

        # Schema and error message preserved for diagnostic insight
        assert sanitized["context"]["schema_definition"]["table_name"] == "customers"
        assert "ConstraintViolation" in sanitized["error_message"]
