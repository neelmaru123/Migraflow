"""
Metrics Service
Aggregates focused, actionable operational metrics across jobs, steps, agents, and LLM calls.
"""

from datetime import datetime, timezone, timedelta
import logging
from typing import Dict, Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.state import (
    AgentLifecycle,
    ExecutionLifecycle,
    TraceOperationType,
    VerificationStatus,
)
from app.modules.agents.agents_models import Agent
from app.modules.execution.execution_models import (
    MigrationExecutionPlan,
    MigrationExecutionStep,
    MigrationJob,
    VerificationResult,
)
from app.modules.observability.observability_models import ExecutionTrace, LLMCallRecord
from app.modules.observability.observability_schemas import ObservabilityMetricsResponse

logger = logging.getLogger(__name__)


class ObservabilityMetricsService:
    """
    Computes real-time platform metrics from PostgreSQL/SQLite metadata stores.
    """

    @classmethod
    async def get_metrics(cls, session: AsyncSession) -> ObservabilityMetricsResponse:
        """Collects clean operational metrics."""
        # 1. Job counters
        stmt_started = select(func.count(MigrationJob.id))
        stmt_completed = select(func.count(MigrationJob.id)).where(MigrationJob.status == ExecutionLifecycle.COMPLETED.value)
        stmt_failed = select(func.count(MigrationJob.id)).where(MigrationJob.status == ExecutionLifecycle.FAILED.value)
        stmt_cancelled = select(func.count(MigrationJob.id)).where(MigrationJob.status == ExecutionLifecycle.CANCELLED.value)

        jobs_started = (await session.execute(stmt_started)).scalar_one() or 0
        jobs_completed = (await session.execute(stmt_completed)).scalar_one() or 0
        jobs_failed = (await session.execute(stmt_failed)).scalar_one() or 0
        jobs_cancelled = (await session.execute(stmt_cancelled)).scalar_one() or 0

        # 2. Replan, recovery, retry counters from ExecutionPlan / Steps
        stmt_replans = select(func.sum(MigrationExecutionPlan.replan_count))
        stmt_recoveries = select(func.sum(MigrationExecutionPlan.recovery_count))
        stmt_retries = select(func.sum(MigrationExecutionStep.attempt_count))

        replan_count = (await session.execute(stmt_replans)).scalar_one() or 0
        recovery_count = (await session.execute(stmt_recoveries)).scalar_one() or 0
        retry_count = (await session.execute(stmt_retries)).scalar_one() or 0

        # 3. Verification failure count
        stmt_verif_fails = select(func.count(VerificationResult.id)).where(
            VerificationResult.status == VerificationStatus.FAILED.value
        )
        verif_fails = (await session.execute(stmt_verif_fails)).scalar_one() or 0

        # 4. Agent availability ratio
        now = datetime.now(timezone.utc)
        agent_cutoff = now - timedelta(seconds=60)
        stmt_agents_total = select(func.count(Agent.id))
        stmt_agents_online = select(func.count(Agent.id)).where(
            Agent.status.in_([AgentLifecycle.ONLINE.value, AgentLifecycle.BUSY.value]),
            Agent.last_seen_at >= agent_cutoff,
        )
        total_agents = (await session.execute(stmt_agents_total)).scalar_one() or 0
        online_agents = (await session.execute(stmt_agents_online)).scalar_one() or 0
        avail_ratio = round(online_agents / total_agents, 2) if total_agents > 0 else 1.0

        # 5. Average step duration from execution traces
        stmt_step_dur = select(func.avg(ExecutionTrace.duration_ms)).where(
            ExecutionTrace.operation_type == TraceOperationType.EXECUTION_STEP.value,
            ExecutionTrace.duration_ms.is_not(None),
        )
        avg_step_ms = (await session.execute(stmt_step_dur)).scalar_one() or 0.0
        avg_step_sec = round(avg_step_ms / 1000.0, 2)

        # 6. LLM aggregations
        stmt_llm_calls = select(func.count(LLMCallRecord.id))
        stmt_llm_lat = select(func.avg(LLMCallRecord.latency_ms))
        stmt_llm_tokens = select(func.sum(LLMCallRecord.total_tokens))
        stmt_llm_cost = select(func.sum(LLMCallRecord.estimated_cost_usd))

        llm_calls = (await session.execute(stmt_llm_calls)).scalar_one() or 0
        llm_lat = round((await session.execute(stmt_llm_lat)).scalar_one() or 0.0, 2)
        llm_tokens = (await session.execute(stmt_llm_tokens)).scalar_one() or 0
        llm_cost = round((await session.execute(stmt_llm_cost)).scalar_one() or 0.0, 4)

        return ObservabilityMetricsResponse(
            jobs_started_total=jobs_started,
            jobs_completed_total=jobs_completed,
            jobs_failed_total=jobs_failed,
            jobs_cancelled_total=jobs_cancelled,
            average_execution_duration_seconds=0.0,
            retry_count_total=int(retry_count),
            recovery_count_total=int(recovery_count),
            replan_count_total=int(replan_count),
            verification_failures_total=verif_fails,
            agent_availability_ratio=avail_ratio,
            average_step_duration_seconds=avg_step_sec,
            llm_calls_total=llm_calls,
            llm_average_latency_ms=llm_lat,
            llm_tokens_total=int(llm_tokens),
            llm_estimated_cost_usd_total=llm_cost,
        )

    get_metrics_summary = get_metrics

