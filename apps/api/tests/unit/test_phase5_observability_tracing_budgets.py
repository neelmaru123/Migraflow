"""
Unit Tests for Phase 5 — Production Observability, Tracing, Budgets, and Operational Controls.
Validates:
1. Hierarchical Execution Tracing (AgentRun -> ExecutionStep -> ToolRun/LLMRun) and OTel span export.
2. Metadata and log credential sanitization (zero password/token leakage).
3. LLM Observability & Cost Tracking (latency, token usage, cost estimation, structured output validation).
4. Deterministic Resource Budget Enforcement (max LLM calls, replans, retries, tokens, cost, duration).
5. Explicit Timeout Policies (LLM, DB, step, verification, job, communication).
6. Credential-safe Structured Logging with Contextual Correlation IDs.
7. Operational Health Decoupling (Agent Health decoupled from Job Health).
8. Prompt and Model Versioning / Plan Provenance retrieval.
"""

import asyncio
from datetime import datetime, timezone
import json
import logging
from unittest.mock import AsyncMock, patch
import uuid
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.db import Base
from app.core.state import (
    BudgetLimitType,
    ExecutionEventType,
    ExecutionLifecycle,
    ExecutionStepLifecycle,
    TraceOperationType,
    TraceStatus,
    BudgetExceededError,
    ExecutionTimeoutError,
)
from app.modules.agents.agents_models import Agent
from app.modules.sources.sources_models import DataSource
from app.modules.metadata.metadata_models import (
    MetadataColumn,
    MetadataSchema,
    MetadataSnapshot,
    MetadataTable,
)
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
from app.modules.migration_plans.migration_plans_models import (
    MigrationPlan,
    MigrationPlanSnapshot,
    MigrationPlanVersion,
)
from app.modules.users.users_models import User
from app.modules.observability.observability_models import (
    ExecutionTrace,
    LLMCallRecord,
    ResourceBudget,
)
from app.modules.observability.tracer import ExecutionTracer
from app.modules.observability.llm_tracker import LLMTracker
from app.modules.observability.budget_manager import ResourceBudgetManager
from app.modules.observability.timeout_policy import TimeoutPolicy, execute_with_timeout
from app.modules.observability.structured_logger import (
    StructuredLogger,
    get_logger,
    set_log_context,
    clear_log_context,
)
from app.modules.observability.health_service import OperationalHealthService
from app.modules.observability.metrics_service import ObservabilityMetricsService
from app.modules.observability.observability_routes import get_plan_provenance


async def create_test_env():
    """Helper to initialize an in-memory SQLite database and test baseline records."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    session_maker = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    user_id = uuid.uuid4()
    plan_id = uuid.uuid4()
    job_id = uuid.uuid4()
    agent_id = uuid.uuid4()
    agent_run_id = uuid.uuid4()
    step_id = uuid.uuid4()

    async with session_maker() as session:
        user = User(
            id=user_id,
            email="observability@migraflow.io",
            name="Observability Admin",
            password_hash="hashed_test_password",
            is_active=True,
        )

        plan = MigrationPlan(
            id=plan_id,
            user_id=user_id,
            status="APPROVED",
            confidence_score=0.95,
            plan_data={
                "planner_model": "gpt-4o",
                "planner_version": "v3.2",
                "prompt_version": "p-migration-2026.04",
                "schema_version": "v1",
                "table_mappings": [
                    {
                        "source_table": "public.orders",
                        "target_table": "orders_v2",
                        "columns": [{"source": "id", "target": "id", "type": "int", "is_pk": True}],
                    }
                ],
            },
        )

        plan_exec_id = uuid.uuid4()

        agent = Agent(
            id=agent_id,
            user_id=user_id,
            name="agent-worker-01",
            agent_identifier="agent-worker-01-uuid",
            status="online",
            last_seen_at=datetime.now(timezone.utc),
        )

        job = MigrationJob(
            id=job_id,
            migration_plan_id=plan_id,
            status=ExecutionLifecycle.RUNNING.value,
        )

        plan_exec = MigrationExecutionPlan(
            id=plan_exec_id,
            migration_job_id=job_id,
            migration_plan_id=plan_id,
            status=ExecutionLifecycle.RUNNING.value,
        )

        agent_run = AgentRun(
            id=agent_run_id,
            migration_job_id=job_id,
            agent_id=agent_id,
            status=ExecutionLifecycle.RUNNING.value,
        )

        step = MigrationExecutionStep(
            id=step_id,
            execution_plan_id=plan_exec_id,
            step_key="extract_orders",
            step_type="TABLE_COPY",
            sequence=1,
            status=ExecutionStepLifecycle.RUNNING.value,
        )

        session.add_all([user, plan, agent, job, plan_exec, agent_run, step])
        await session.commit()

    return {
        "engine": engine,
        "session_maker": session_maker,
        "user_id": user_id,
        "plan_id": plan_id,
        "job_id": job_id,
        "agent_id": agent_id,
        "agent_run_id": agent_run_id,
        "step_id": step_id,
    }


# ==============================================================================
# 1. EXECUTION TRACING & HIERARCHY TESTS
# ==============================================================================

@pytest.mark.asyncio
async def test_hierarchical_trace_propagation_and_tree():
    """Verify traces form a clean parent-child hierarchy and reconstruct trees accurately."""
    env = await create_test_env()
    session_maker = env["session_maker"]
    job_id = env["job_id"]
    agent_run_id = env["agent_run_id"]
    step_id = env["step_id"]

    async with session_maker() as session:
        # Level 1: AgentRun span
        root_span = await ExecutionTracer.start_span(
            session=session,
            operation_type=TraceOperationType.AGENT_RUN,
            name="AgentRun-MigrateOrders",
            migration_job_id=job_id,
            agent_run_id=agent_run_id,
        )
        assert root_span.parent_run_id is None
        assert root_span.trace_id == root_span.id

        # Level 2: ExecutionStep span
        step_span = await ExecutionTracer.start_span(
            session=session,
            operation_type=TraceOperationType.NODE_RUN,
            name="NodeRun-ExtractOrders",
            migration_job_id=job_id,
            agent_run_id=agent_run_id,
            execution_step_id=step_id,
            parent_run_id=root_span.id,
        )
        assert step_span.parent_run_id == root_span.id
        assert step_span.trace_id == root_span.id

        # Level 3: ToolRun span and LLMRun span
        tool_span = await ExecutionTracer.start_span(
            session=session,
            operation_type=TraceOperationType.TOOL_RUN,
            name="ToolRun-DatabaseReader",
            migration_job_id=job_id,
            parent_run_id=step_span.id,
        )
        llm_span = await ExecutionTracer.start_span(
            session=session,
            operation_type=TraceOperationType.LLM_RUN,
            name="LLMRun-AdaptiveCast",
            migration_job_id=job_id,
            parent_run_id=step_span.id,
        )

        # Finish spans
        await ExecutionTracer.finish_span(session=session, trace_id=llm_span.id, status=TraceStatus.COMPLETED)
        await ExecutionTracer.finish_span(session=session, trace_id=tool_span.id, status=TraceStatus.COMPLETED)
        await ExecutionTracer.finish_span(session=session, trace_id=step_span.id, status=TraceStatus.COMPLETED)
        await ExecutionTracer.finish_span(session=session, trace_id=root_span.id, status=TraceStatus.COMPLETED)

        # Query tree
        tree = await ExecutionTracer.get_trace_tree(session=session, trace_id=root_span.id)
        assert tree is not None
        assert tree.name == "AgentRun-MigrateOrders"
        assert len(tree.children) == 1
        child_step = tree.children[0]
        assert child_step.name == "NodeRun-ExtractOrders"
        assert len(child_step.children) == 2

        child_names = {c.name for c in child_step.children}
        assert "ToolRun-DatabaseReader" in child_names
        assert "LLMRun-AdaptiveCast" in child_names


@pytest.mark.asyncio
async def test_trace_span_async_context_manager_and_error_handling():
    """Verify ExecutionTracer.span() context manager captures duration and exceptions."""
    env = await create_test_env()
    session_maker = env["session_maker"]
    job_id = env["job_id"]

    async with session_maker() as session:
        trace_id = None
        with pytest.raises(ValueError, match="Synthetic failure in step"):
            async with ExecutionTracer.span(
                session=session,
                operation_type=TraceOperationType.EXECUTION_STEP,
                name="FailingStep",
                migration_job_id=job_id,
            ) as span:
                trace_id = span.id
                await asyncio.sleep(0.01)
                raise ValueError("Synthetic failure in step")

        # Verify trace status and error persistence
        stored_trace = await session.get(ExecutionTrace, trace_id)
        assert stored_trace is not None
        assert stored_trace.status == TraceStatus.FAILED.value
        assert "Synthetic failure in step" in stored_trace.error
        assert stored_trace.duration_ms is not None
        assert stored_trace.duration_ms >= 5.0


@pytest.mark.asyncio
async def test_trace_to_otel_span_format():
    """Verify trace converts cleanly to OpenTelemetry-compatible span JSON dictionary."""
    env = await create_test_env()
    session_maker = env["session_maker"]
    job_id = env["job_id"]

    async with session_maker() as session:
        span = await ExecutionTracer.start_span(
            session=session,
            operation_type=TraceOperationType.TOOL_RUN,
            name="VerifyChecksums",
            migration_job_id=job_id,
            metadata={"batch_size": 5000, "table": "orders"},
        )
        await ExecutionTracer.finish_span(session=session, trace_id=span.id, status=TraceStatus.COMPLETED)

        otel_dict = span.to_otel_span()
        assert otel_dict["name"] == "VerifyChecksums"
        assert otel_dict["context"]["trace_id"] == str(span.trace_id)
        assert otel_dict["context"]["span_id"] == str(span.id)
        assert otel_dict["status"]["code"] == "OK"
        assert otel_dict["attributes"]["operation_type"] == TraceOperationType.TOOL_RUN.value
        assert otel_dict["attributes"]["batch_size"] == 5000


@pytest.mark.asyncio
async def test_trace_metadata_credential_scrubbing():
    """Verify traces never store raw credentials in metadata."""
    env = await create_test_env()
    session_maker = env["session_maker"]
    job_id = env["job_id"]

    sensitive_metadata = {
        "db_url": "postgresql://db_user:super_secret_password_123@prod-cluster.internal:5432/finance",
        "api_token": "Bearer sk-proj-1234567890abcdef1234567890abcdef",
        "user_password": "PlaintextPassword!",
        "safe_key": "safe_value",
    }

    async with session_maker() as session:
        span = await ExecutionTracer.start_span(
            session=session,
            operation_type=TraceOperationType.AGENT_RUN,
            name="SanitizedRun",
            migration_job_id=job_id,
            metadata=sensitive_metadata,
        )
        await ExecutionTracer.finish_span(session=session, trace_id=span.id)

        stored = await session.get(ExecutionTrace, span.id)
        meta = stored.trace_metadata
        assert "super_secret_password_123" not in json.dumps(meta)
        assert "PlaintextPassword!" not in json.dumps(meta)
        assert meta["safe_key"] == "safe_value"
        assert meta["user_password"] == "***REDACTED***"


# ==============================================================================
# 2. LLM OBSERVABILITY & PROVENANCE TESTS
# ==============================================================================

@pytest.mark.asyncio
async def test_llm_tracker_recording_and_cost_estimation():
    """Verify LLMTracker records latency, token usage, cost estimation, and validates output."""
    env = await create_test_env()
    session_maker = env["session_maker"]
    job_id = env["job_id"]

    async with session_maker() as session:
        record = await LLMTracker.record_call(
            session=session,
            migration_job_id=job_id,
            provider="openai",
            model="gpt-4o",
            prompt_version="p-migration-v2.1",
            latency_ms=450.5,
            prompt_tokens=1000,
            completion_tokens=250,
            prompt_preview="Convert customer schema with sensitive pass: postgres://admin:secret@host/db",
            structured_output_valid=True,
            metadata={"phase": "planning"},
        )

        assert record.id is not None
        assert record.total_tokens == 1250
        assert record.is_success is True
        assert record.structured_output_valid is True
        assert record.estimated_cost_usd is not None
        assert record.estimated_cost_usd > 0.0
        # Verify secret sanitization in prompt preview
        assert "secret@host" not in record.prompt_preview
        assert "postgres://admin:***REDACTED***@host" in record.prompt_preview


@pytest.mark.asyncio
async def test_plan_model_and_prompt_provenance():
    """Verify answering 'Which model/prompt generated this migration plan?'."""
    env = await create_test_env()
    session_maker = env["session_maker"]
    plan_id = env["plan_id"]

    async with session_maker() as session:
        provenance = await get_plan_provenance(plan_id=plan_id, session=session)
        assert provenance.plan_id == plan_id
        assert provenance.model == "gpt-4o"
        assert provenance.prompt_version == "p-migration-2026.04"
        assert provenance.planner_version == "v3.2"
        assert provenance.schema_version == "v1"


# ==============================================================================
# 3. RESOURCE BUDGETS TESTS
# ==============================================================================

@pytest.mark.asyncio
async def test_resource_budget_deterministic_enforcement():
    """Verify deterministic Python code enforces resource budgets and raises BudgetExceededError."""
    env = await create_test_env()
    session_maker = env["session_maker"]
    job_id = env["job_id"]

    async with session_maker() as session:
        # Create budget with tight limits
        budget = await ResourceBudgetManager.get_or_create_budget(
            session=session,
            migration_job_id=job_id,
            max_llm_calls=2,
            max_replans=1,
            max_tokens=5000,
            max_cost_usd=0.05,
        )
        assert budget.max_llm_calls == 2
        assert budget.max_replans == 1

        # 1st LLM call: allowed
        await ResourceBudgetManager.record_usage(session=session, migration_job_id=job_id, llm_calls=1, tokens=1000)
        # 2nd LLM call: allowed
        await ResourceBudgetManager.record_usage(session=session, migration_job_id=job_id, llm_calls=1, tokens=1000)

        # 3rd LLM call: MUST raise BudgetExceededError deterministically
        with pytest.raises(BudgetExceededError) as exc_info:
            await ResourceBudgetManager.record_usage(
                session=session,
                migration_job_id=job_id,
                llm_calls=1,
                tokens=1000,
            )
        assert exc_info.value.limit_type == BudgetLimitType.MAX_LLM_CALLS.value

        # Reset budget and test token overflow
        budget.current_llm_calls = 0
        budget.current_tokens = 4900
        await session.commit()

        with pytest.raises(BudgetExceededError) as exc_info_tokens:
            await ResourceBudgetManager.record_usage(
                session=session,
                migration_job_id=job_id,
                tokens=200,
            )
        assert exc_info_tokens.value.limit_type == BudgetLimitType.MAX_TOKENS.value

        # Test replan overflow
        with pytest.raises(BudgetExceededError) as exc_info_replan:
            await ResourceBudgetManager.record_usage(
                session=session,
                migration_job_id=job_id,
                replans=2,
            )
        assert exc_info_replan.value.limit_type == BudgetLimitType.MAX_REPLANS.value


# ==============================================================================
# 4. TIMEOUT POLICY TESTS
# ==============================================================================

@pytest.mark.asyncio
async def test_explicit_timeout_policies_and_enforcement():
    """Verify TimeoutPolicy enforces explicit limits and raises ExecutionTimeoutError."""
    # Audit constant values
    assert TimeoutPolicy.LLM_CALL == 60.0
    assert TimeoutPolicy.DB_CONNECTION == 10.0
    assert TimeoutPolicy.METADATA_INSPECTION == 120.0
    assert TimeoutPolicy.MIGRATION_STEP == 600.0
    assert TimeoutPolicy.VERIFICATION == 180.0
    assert TimeoutPolicy.JOB == 7200.0
    assert TimeoutPolicy.AGENT_COMMUNICATION == 30.0

    # 1. Successful execution within timeout
    async def fast_op():
        await asyncio.sleep(0.01)
        return "completed"

    result = await execute_with_timeout(
        coro=fast_op(),
        timeout_seconds=1.0,
        operation_type="FastOperation",
    )
    assert result == "completed"

    # 2. Hanging execution triggering ExecutionTimeoutError
    async def slow_op():
        await asyncio.sleep(1.0)
        return "too_late"

    with pytest.raises(ExecutionTimeoutError) as exc_info:
        await execute_with_timeout(
            coro=slow_op(),
            timeout_seconds=0.05,
            operation_type="HangingDBQuery",
        )
    assert exc_info.value.timeout_seconds == 0.05
    assert exc_info.value.operation_type == "HangingDBQuery"
    assert "timed out after" in str(exc_info.value)



# ==============================================================================
# 5. CREDENTIAL-SAFE STRUCTURED LOGGING TESTS
# ==============================================================================

def test_structured_logger_context_correlation_and_sanitization():
    """Verify structured logger injects correlation IDs and sanitizes credentials."""
    logger = get_logger("test_observability")
    job_id = uuid.uuid4()
    agent_run_id = uuid.uuid4()
    step_id = uuid.uuid4()
    trace_id = uuid.uuid4()

    set_log_context(
        migration_job_id=job_id,
        agent_run_id=agent_run_id,
        execution_step_id=step_id,
        trace_id=trace_id,
    )

    sensitive_msg = "Connecting to postgresql://admin:SuperSecretPass1@127.0.0.1:5432/migradb with Bearer my-jwt-secret-token"

    with patch.object(logger.logger, "info") as mock_info:
        logger.info(sensitive_msg, extra_data={"user_password": "MySecretPassword123"})
        mock_info.assert_called_once()
        formatted_call = mock_info.call_args[0][0]
        payload = json.loads(formatted_call)

        # Check correlation identifiers
        assert payload["migration_job_id"] == str(job_id)
        assert payload["agent_run_id"] == str(agent_run_id)
        assert payload["execution_step_id"] == str(step_id)
        assert payload["trace_id"] == str(trace_id)

        # Check credential scrubbing
        assert "SuperSecretPass1" not in formatted_call
        assert "my-jwt-secret-token" not in formatted_call
        assert "MySecretPassword123" not in formatted_call
        assert payload["extra_data"]["user_password"] == "***REDACTED***"

    clear_log_context()


# ==============================================================================
# 6. OPERATIONAL HEALTH SEPARATION TESTS
# ==============================================================================

@pytest.mark.asyncio
async def test_operational_health_agent_and_job_decoupling():
    """Verify strict decoupling: online agent with failed job, or offline agent with finished job."""
    env = await create_test_env()
    session_maker = env["session_maker"]
    agent_id = env["agent_id"]
    job_id = env["job_id"]

    async with session_maker() as session:
        # Scenario 1: Agent is online and recently heartbeated, but Job is FAILED
        agent = await session.get(Agent, agent_id)
        agent.status = "online"
        agent.last_seen_at = datetime.now(timezone.utc)

        job = await session.get(MigrationJob, job_id)
        job.status = ExecutionLifecycle.FAILED.value

        await session.commit()

        health = await OperationalHealthService.evaluate_health(session=session)

        # Agent health must be HEALTHY
        assert health.agent_health_summary["health_verdict"] == "HEALTHY"
        assert health.agent_health_summary["online_agents"] >= 1
        assert health.agent_health_summary["offline_agents"] == 0

        # Job health must be DEGRADED/UNHEALTHY
        assert health.job_health_summary["health_verdict"] in ("DEGRADED", "UNHEALTHY")
        assert health.job_health_summary["failed_jobs"] >= 1

        # Scenario 2: Agent is offline, but Job is healthy/running
        agent.status = "offline"
        agent.last_seen_at = None
        job.status = ExecutionLifecycle.RUNNING.value
        await session.commit()

        health2 = await OperationalHealthService.evaluate_health(session=session)

        # Agent health is UNHEALTHY
        assert health2.agent_health_summary["health_verdict"] == "UNHEALTHY"
        assert health2.agent_health_summary["online_agents"] == 0

        # Job health is HEALTHY
        assert health2.job_health_summary["health_verdict"] == "HEALTHY"
        assert health2.job_health_summary["failed_jobs"] == 0


# ==============================================================================
# 7. OBSERVABILITY METRICS AGGREGATION TESTS
# ==============================================================================

@pytest.mark.asyncio
async def test_observability_metrics_aggregation():
    """Verify metrics service computes summary counts, durations, and LLM expenditures."""
    env = await create_test_env()
    session_maker = env["session_maker"]
    job_id = env["job_id"]

    async with session_maker() as session:
        # Record an LLM call and trace
        await LLMTracker.record_call(
            session=session,
            migration_job_id=job_id,
            provider="anthropic",
            model="claude-3-5-sonnet",
            prompt_version="p1",
            latency_ms=300.0,
            prompt_tokens=500,
            completion_tokens=100,
            prompt_preview="Sample prompt",
            structured_output_valid=True,
        )

        metrics = await ObservabilityMetricsService.get_metrics_summary(session=session)
        assert metrics.total_llm_calls >= 1
        assert metrics.total_tokens_consumed >= 600
        assert metrics.estimated_ai_cost_usd > 0.0
        assert metrics.agent_availability_percentage >= 0.0
