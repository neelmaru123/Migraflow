"""
Unit Tests for Phase 3 — Failure Classification, Recovery, and Agentic Replanning.
Validates:
1. Centralized Failure Taxonomy & Domain Separation (LLM Infra != LLM Output != Execution != Security).
2. Deterministic RecoveryRouter Decisions (RETRY, RECOVER, REPLAN, ASK_USER, FAIL).
3. RetryPolicy backoff, jitter, duration limits, and destructive operation guards.
4. Hard Anti-Infinite Loop Bounds (max replans, max recoveries, max retries).
5. Human-in-the-Loop ASK_USER Intervention lifecycle (creation, pause, resolution).
6. Agentic Replanning via LangGraph with sanitized context (zero credentials / zero raw rows).
7. Approval Invalidation Governance (replan clears approval, blocks execution until re-approved).
"""

import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch
import uuid
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.db import Base
from app.core.state import (
    ExecutionEventType,
    ExecutionLifecycle,
    ExecutionPlanLifecycle,
    ExecutionStepLifecycle,
    FailureCategory,
    FailureDomain,
    FailureSeverity,
    MigrationPlanLifecycle,
    RecoveryDecisionType,
)
from app.modules.agents.agents_models import Agent
from app.modules.execution.agentic_replan_services import AgenticReplanService
from app.modules.execution.execution_models import (
    AgentRun,
    ExecutionCheckpoint,
    ExecutionEvent,
    MigrationExecutionPlan,
    MigrationExecutionStep,
    MigrationJob,
    UserIntervention,
)
from app.modules.execution.execution_plan_services import ExecutionPlanService
from app.modules.execution.execution_services import ExecutionService
from app.modules.execution.failure_taxonomy import (
    ClassifiedFailure,
    FailureClassifier,
    RecoveryDecision,
)
from app.modules.execution.recovery_router import (
    MAX_RECOVERIES_PER_STEP,
    MAX_REPLANS_PER_JOB,
    MAX_RETRIES_PER_STEP,
    RecoveryRouter,
    recovery_router,
)
from app.modules.execution.retry_policy import RetryPolicy
from app.modules.metadata.metadata_models import (
    MetadataColumn,
    MetadataSchema,
    MetadataSnapshot,
    MetadataTable,
)
from app.modules.migration_plans.migration_plans_models import (
    MigrationPlan,
    MigrationPlanVersion,
)
from app.modules.migration_plans.migration_plans_services import MigrationPlanService
from app.modules.sources.sources_models import DataSource
from app.modules.users.users_models import User


# ============================================================================
# 1. Failure Taxonomy & Domain Separation Tests
# ============================================================================

def test_failure_classifier_taxonomy_mappings():
    """Verify that distinct error types map deterministically to standardized categories."""
    # 1. Transient Network
    net_err = TimeoutError("Connection timed out connecting to host.docker.internal:5432")
    net_classified = FailureClassifier.classify_exception(net_err)
    assert net_classified.category == FailureCategory.TIMEOUT
    assert net_classified.retryable is True
    assert net_classified.domain == FailureDomain.MIGRATION_EXECUTION

    # 2. Database Lock Contention
    lock_classified = FailureClassifier.classify_error_payload("OperationalError", "deadlock detected between transactions")
    assert lock_classified.category == FailureCategory.TRANSIENT_DATABASE
    assert lock_classified.retryable is True

    # 3. Authentication Denial
    auth_classified = FailureClassifier.classify_error_payload("InvalidPassword", "password authentication failed for user 'postgres'")
    assert auth_classified.category == FailureCategory.AUTHENTICATION
    assert auth_classified.retryable is False
    assert auth_classified.requires_user is True
    assert auth_classified.severity == FailureSeverity.CRITICAL
    assert auth_classified.domain == FailureDomain.SECURITY_AUTH

    # 4. Schema Drift (Missing Table / Column)
    schema_classified = FailureClassifier.classify_error_payload("ProgrammingError", "relation 'customers' does not exist (SQLSTATE 42P01)")
    assert schema_classified.category in {FailureCategory.SCHEMA_CHANGED, FailureCategory.TARGET_SCHEMA_MISMATCH, FailureCategory.SOURCE_SCHEMA_MISMATCH}
    assert schema_classified.retryable is False
    assert schema_classified.requires_replan is True
    assert schema_classified.domain == FailureDomain.MIGRATION_EXECUTION

    # 5. Target Constraint Violation
    fk_classified = FailureClassifier.classify_error_payload("IntegrityError", "violates foreign key constraint 'fk_orders_customer_id' (SQLSTATE 23503)")
    assert fk_classified.category == FailureCategory.CONSTRAINT_VIOLATION
    assert fk_classified.retryable is False
    assert fk_classified.requires_replan is True
    assert fk_classified.domain == FailureDomain.DATABASE_ENGINE

    # 6. Agent Crash
    crash_classified = FailureClassifier.classify_error_payload("AgentCrashError", "agent heartbeat ceased; container terminated")
    assert crash_classified.category in {FailureCategory.AGENT_LOST, FailureCategory.AGENT_CRASH}
    assert crash_classified.retryable is False
    assert crash_classified.recoverable is True
    assert crash_classified.domain == FailureDomain.SYSTEM_ORCHESTRATION


def test_failure_classifier_domain_separation():
    """Verify clean separation between LLM infra errors, LLM output errors, and database errors."""
    # A. LLM Infrastructure Error (OpenAI 429 Rate Limit / 503)
    class RateLimitError(Exception):
        pass
    llm_infra_err = RateLimitError("Rate limit exceeded for model gpt-4. Please retry after 20s.")
    llm_infra_classified = FailureClassifier.classify_exception(llm_infra_err)
    assert llm_infra_classified.domain == FailureDomain.LLM_INFRASTRUCTURE
    assert llm_infra_classified.category == FailureCategory.RESOURCE_EXHAUSTION
    assert llm_infra_classified.retryable is True
    assert llm_infra_classified.requires_replan is False

    # B. LLM Output Validation Error (Invalid Plan AST syntax)
    class OutputParserException(Exception):
        pass
    llm_output_err = OutputParserException("Failed to parse TransformationPlan JSON: missing required field 'table_mappings'")
    llm_output_classified = FailureClassifier.classify_exception(llm_output_err)
    assert llm_output_classified.domain == FailureDomain.LLM_OUTPUT_VALIDATION
    assert llm_output_classified.category == FailureCategory.PLAN_INVALID
    assert llm_output_classified.retryable is False
    assert llm_output_classified.requires_replan is True

    # C. Database Engine Execution Error
    class UniqueViolation(Exception):
        pass
    db_err = UniqueViolation("duplicate key value violates unique constraint 'users_email_key'")
    db_classified = FailureClassifier.classify_exception(db_err)
    assert db_classified.domain == FailureDomain.DATABASE_ENGINE
    assert db_classified.category == FailureCategory.CONSTRAINT_VIOLATION


# ============================================================================
# 2. Deterministic Recovery Router & Loop Bounds Tests
# ============================================================================

def test_recovery_router_decisions():
    """Verify deterministic decision matrix for all failure scenarios."""
    router = RecoveryRouter()

    # 1. Transient network timeout -> RETRY with backoff
    net_fail = FailureClassifier.classify_error_payload("TimeoutError", "connection timed out to target DB")
    d_net = router.evaluate(net_fail, attempt_count=1)
    assert d_net.action == RecoveryDecisionType.RETRY
    assert d_net.delay_seconds > 0

    # 2. Agent lost / heartbeat ceased -> RECOVER
    agent_fail = FailureClassifier.classify_error_payload("AgentLost", "agent heartbeat ceased")
    d_agent = router.evaluate(agent_fail, attempt_count=1)
    assert d_agent.action == RecoveryDecisionType.RECOVER

    # 3. Schema changed / drift -> REPLAN
    schema_fail = FailureClassifier.classify_error_payload("ProgrammingError", "column 'first_name' does not exist in source table")
    d_schema = router.evaluate(schema_fail, replan_count=0)
    assert d_schema.action == RecoveryDecisionType.REPLAN
    assert d_schema.replan_context is not None

    # 4. Authentication error -> ASK_USER
    auth_fail = FailureClassifier.classify_error_payload("InvalidPassword", "password authentication failed for user 'postgres'")
    d_auth = router.evaluate(auth_fail)
    assert d_auth.action == RecoveryDecisionType.ASK_USER
    assert d_auth.user_prompt is not None

    # 5. User Cancelled -> FAIL
    cancel_fail = FailureClassifier.classify_error_payload("CancelledError", "User cancelled job")
    d_cancel = router.evaluate(cancel_fail)
    assert d_cancel.action == RecoveryDecisionType.FAIL


def test_recovery_router_anti_infinite_loop_bounds():
    """Verify that hard operational bounds prevent infinite agentic cycles."""
    router = RecoveryRouter()

    schema_fail = FailureClassifier.classify_error_payload("ProgrammingError", "table structure changed")

    # Replan limit exceeded (3 replans reached) -> terminates with FAIL / ASK_USER
    d_exhausted_replan = router.evaluate(schema_fail, replan_count=MAX_REPLANS_PER_JOB)
    assert d_exhausted_replan.action in {RecoveryDecisionType.FAIL, RecoveryDecisionType.ASK_USER}
    assert "Max" in d_exhausted_replan.reason

    # Recovery limit exceeded (2 recoveries reached) -> terminates with FAIL
    agent_fail = FailureClassifier.classify_error_payload("AgentLost", "agent heartbeat ceased")
    d_exhausted_rec = router.evaluate(agent_fail, recovery_count=MAX_RECOVERIES_PER_STEP)
    assert d_exhausted_rec.action == RecoveryDecisionType.FAIL
    assert "Max" in d_exhausted_rec.reason

    # Retry attempts exceeded (3 attempts reached) -> transitions to FAIL (or replan if replan applicable)
    net_fail = FailureClassifier.classify_error_payload("TimeoutError", "connection timeout")
    d_exhausted_retry = router.evaluate(net_fail, attempt_count=MAX_RETRIES_PER_STEP)
    assert d_exhausted_retry.action == RecoveryDecisionType.FAIL


def test_retry_policy_duration_and_destructive_guards():
    """Verify RetryPolicy guards against blind retries on destructive operations and duration limits."""
    policy = RetryPolicy(max_attempts=3, max_total_retry_duration=60.0)

    # Destructive operation guard
    assert policy.is_destructive_operation("TRUNCATE TABLE customers", "load") is True
    assert policy.is_destructive_operation("INSERT INTO customers", "load") is False
    assert policy.should_retry(current_attempt=1, error_type="NETWORK_TIMEOUT", is_destructive=True) is False

    # Duration limit guard
    assert policy.should_retry(current_attempt=1, error_type="NETWORK_TIMEOUT", total_retry_duration=70.0) is False
    assert policy.should_retry(current_attempt=1, error_type="NETWORK_TIMEOUT", total_retry_duration=30.0) is True


# ============================================================================
# 3. ASK_USER State & User Intervention Lifecycle Tests
# ============================================================================

@pytest.mark.asyncio
async def test_ask_user_intervention_creation_and_resolution():
    """Test the full ASK_USER lifecycle: pause execution, wait for user, resolve, and resume."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async_session = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    user_id = uuid.uuid4()
    job_id = uuid.uuid4()
    plan_id = uuid.uuid4()
    step_id = uuid.uuid4()
    exec_plan_id = uuid.uuid4()

    async with async_session() as session:
        user = User(id=user_id, email="ask_user@example.com", password_hash="pw", name="Tester")
        plan = MigrationPlan(
            id=plan_id, user_id=user_id, status="approved", is_valid=True, plan_data={"table_mappings": []}
        )
        job = MigrationJob(
            id=job_id, migration_plan_id=plan_id, status=ExecutionLifecycle.RUNNING.value, progress=30.0
        )
        exec_plan = MigrationExecutionPlan(
            id=exec_plan_id, migration_job_id=job_id, migration_plan_id=plan_id, status=ExecutionPlanLifecycle.RUNNING.value
        )
        step = MigrationExecutionStep(
            id=step_id, execution_plan_id=exec_plan_id, step_key="load:orders", step_type="load", sequence=1, status=ExecutionStepLifecycle.RUNNING.value
        )
        session.add_all([user, plan, job, exec_plan, step])
        await session.commit()

    async with async_session() as session:
        # 1. Trigger ASK_USER intervention via fail_step with an ambiguous authentication failure
        failed_step = await ExecutionPlanService.fail_step(
            session=session,
            step_id=step_id,
            error_type="InvalidPassword",
            error_message="password authentication failed for user 'postgres'",
        )
        await session.commit()

        # Step, execution plan, and job should now be in ASK_USER status
        assert failed_step.status == ExecutionStepLifecycle.ASK_USER.value

        stmt_j = select(MigrationJob).where(MigrationJob.id == job_id)
        job_updated = (await session.execute(stmt_j)).scalar_one()
        assert job_updated.status == ExecutionLifecycle.ASK_USER.value

        # Verify UserIntervention record exists
        interventions = await ExecutionPlanService.list_user_interventions(session, job_id)
        assert len(interventions) == 1
        assert interventions[0].status == "pending"
        assert interventions[0].failure_category == FailureCategory.AUTHENTICATION.value

        intervention_id = interventions[0].id

        # 2. Resolve Intervention with action 'retry'
        resolved = await ExecutionPlanService.resolve_user_intervention(
            session=session,
            intervention_id=intervention_id,
            user_id=user_id,
            action="retry",
            response_data={"updated_password": "new_secret_password"},
        )
        assert resolved.status == "resolved"
        assert resolved.resolved_by_user_id == user_id

        # Job and step should be resumed to running / retrying
        stmt_j2 = select(MigrationJob).where(MigrationJob.id == job_id)
        job_resumed = (await session.execute(stmt_j2)).scalar_one()
        assert job_resumed.status == ExecutionLifecycle.RUNNING.value

        stmt_s2 = select(MigrationExecutionStep).where(MigrationExecutionStep.id == step_id)
        step_resumed = (await session.execute(stmt_s2)).scalar_one()
        assert step_resumed.status == ExecutionStepLifecycle.RETRYING.value


# ============================================================================
# 4. Agentic Replanning & Approval Invalidation Governance Tests
# ============================================================================

@pytest.mark.asyncio
async def test_agentic_replan_invalidates_approval():
    """Verify that agentic replanning creates a new version, strips credentials, and revokes plan approval."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async_session = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    user_id = uuid.uuid4()
    job_id = uuid.uuid4()
    plan_id = uuid.uuid4()
    step_id = uuid.uuid4()
    exec_plan_id = uuid.uuid4()

    async with async_session() as session:
        user = User(id=user_id, email="replan@example.com", password_hash="pw", name="ReplanUser")
        plan = MigrationPlan(
            id=plan_id,
            user_id=user_id,
            status="approved",
            approved_version_number=1,
            approved_by_user_id=user_id,
            approved_at=datetime.now(timezone.utc),
            is_valid=True,
            plan_data={"table_mappings": [{"target_table_name": "customers"}]},
        )
        ver1 = MigrationPlanVersion(
            migration_plan_id=plan_id,
            version_number=1,
            edit_type="initial",
            plan_data={"table_mappings": [{"target_table_name": "customers"}]},
            is_approved=True,
            approved_by_user_id=user_id,
            approved_at=datetime.now(timezone.utc),
        )
        job = MigrationJob(
            id=job_id, migration_plan_id=plan_id, status=ExecutionLifecycle.RUNNING.value
        )
        exec_plan = MigrationExecutionPlan(
            id=exec_plan_id, migration_job_id=job_id, migration_plan_id=plan_id, status=ExecutionPlanLifecycle.RUNNING.value
        )
        step = MigrationExecutionStep(
            id=step_id,
            execution_plan_id=exec_plan_id,
            step_key="load:customers",
            step_type="load",
            input_definition={"target_table_name": "customers"},
            sequence=1,
            status=ExecutionStepLifecycle.RUNNING.value,
        )
        session.add_all([user, plan, ver1, job, exec_plan, step])
        await session.commit()

    # Verify context sanitization strips credentials
    raw_error_with_creds = "Failed to connect to postgresql://admin:super_secret_pwd@10.0.0.5:5432/db with password=my_raw_password"
    classified = FailureClassifier.classify_error_payload("ProgrammingError", raw_error_with_creds)
    sanitized_prompt = AgenticReplanService.sanitize_error_context(classified, step_key="load:customers", table_name="customers")

    assert "super_secret_pwd" not in sanitized_prompt
    assert "my_raw_password" not in sanitized_prompt
    assert "***REDACTED***" in sanitized_prompt

    # Mock LangGraph plan refinement
    mock_refined_ast = MagicMock()
    mock_refined_ast.model_dump.return_value = {
        "table_mappings": [{"target_table_name": "customers_v2"}],
        "confidence_score": 0.95,
    }

    with patch("app.modules.migration_plans.migration_plans_services.llm_plan_generator.refine", return_value=mock_refined_ast):
        with patch("app.modules.migration_plans.migration_plans_engine.migration_plans_validator.MigrationPlanValidator.validate") as mock_val:
            mock_val_res = MagicMock()
            mock_val_res.is_valid = True
            mock_val_res.model_dump.return_value = {"is_valid": True, "errors": [], "warnings": []}
            mock_val.return_value = mock_val_res

            async with async_session() as session:
                replanned_plan = await AgenticReplanService.replan_execution_failure(
                    session=session,
                    job_id=job_id,
                    step_id=step_id,
                    failure=classified,
                    user=user,
                )

                # Approval MUST be revoked / invalidated
                assert replanned_plan.status == MigrationPlanLifecycle.AWAITING_APPROVAL.value
                assert replanned_plan.approved_version_number is None
                assert replanned_plan.approved_by_user_id is None
                assert replanned_plan.approved_at is None

                # Plan versions list should now have version 2
                stmt_v = select(MigrationPlanVersion).where(MigrationPlanVersion.migration_plan_id == plan_id).order_by(MigrationPlanVersion.version_number.desc())
                versions = list((await session.execute(stmt_v)).scalars().all())
                assert len(versions) == 2
                assert versions[0].version_number == 2
                assert versions[0].is_approved is False

                # Trying to execute this plan in unapproved state MUST be rejected
                with pytest.raises(Exception) as exc_info:
                    await ExecutionService.create_execution_job(session, user_id=user_id, plan_id=plan_id)
                assert "unapproved" in str(exc_info.value).lower()

                # Approving version 2 restores approved state with approver details
                approved_plan = await MigrationPlanService.approve_plan(session, replanned_plan, user=user)
                assert approved_plan.status == "approved"
                assert approved_plan.approved_version_number == 2
                assert approved_plan.approved_by_user_id == user_id
                assert approved_plan.approved_at is not None
