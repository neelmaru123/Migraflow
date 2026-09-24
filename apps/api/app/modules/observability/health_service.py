"""
Operational Health Service
Decouples Agent Health (Docker container connectivity & heartbeat status)
from Job Health (DAG execution state, error rates, and verification outcomes).
"""

from datetime import datetime, timezone, timedelta
import logging
from typing import Any, Dict, List
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.state import AgentLifecycle, ExecutionLifecycle
from app.modules.agents.agents_models import Agent
from app.modules.execution.execution_models import MigrationJob
from app.modules.observability.observability_schemas import (
    AgentHealthStatus,
    JobHealthStatus,
    OperationalHealthResponse,
)

logger = logging.getLogger(__name__)


class OperationalHealthService:
    """
    Evaluates segregated health metrics for agents and migration jobs.
    """

    @classmethod
    async def evaluate_health(cls, session: AsyncSession) -> OperationalHealthResponse:
        """
        Calculates separate agent and job health summaries.
        Rule: An online agent does not guarantee its job is healthy,
        and a failed job does not mean the assigned agent is unhealthy.
        """
        now = datetime.now(timezone.utc)
        agent_cutoff = now - timedelta(seconds=60)

        # 1. Evaluate Agent Health
        stmt_agents = select(Agent)
        res_agents = await session.execute(stmt_agents)
        agents = list(res_agents.scalars().all())

        agent_statuses: List[AgentHealthStatus] = []
        online_count = 0
        offline_count = 0

        for a in agents:
            is_recent = a.last_seen_at is not None and (
                a.last_seen_at if a.last_seen_at.tzinfo else a.last_seen_at.replace(tzinfo=timezone.utc)
            ) >= agent_cutoff
            is_healthy = a.status in (AgentLifecycle.ONLINE.value, AgentLifecycle.BUSY.value) and is_recent

            if is_healthy:
                online_count += 1
            else:
                offline_count += 1

            agent_statuses.append(
                AgentHealthStatus(
                    agent_id=a.id,
                    name=a.name,
                    status=a.status,
                    is_healthy=is_healthy,
                    last_seen_at=a.last_seen_at,
                    version=a.version,
                )
            )

        agent_summary = {
            "total_agents": len(agents),
            "online_agents": online_count,
            "offline_agents": offline_count,
            "health_verdict": "HEALTHY" if offline_count == 0 and len(agents) > 0 else ("DEGRADED" if online_count > 0 else "UNHEALTHY"),
        }

        # 2. Evaluate Active Job Health
        stmt_jobs = (
            select(MigrationJob)
            .where(MigrationJob.status.in_([
                ExecutionLifecycle.QUEUED.value,
                ExecutionLifecycle.CLAIMED.value,
                ExecutionLifecycle.PREPARING.value,
                ExecutionLifecycle.RUNNING.value,
                ExecutionLifecycle.VERIFYING.value,
                ExecutionLifecycle.ASK_USER.value,
                ExecutionLifecycle.NEEDS_REVIEW.value,
                ExecutionLifecycle.FAILED.value,
            ]))
            .order_by(MigrationJob.created_at.desc())
            .limit(20)
        )
        res_jobs = await session.execute(stmt_jobs)
        jobs = list(res_jobs.scalars().all())

        job_statuses: List[JobHealthStatus] = []
        healthy_jobs = 0
        degraded_jobs = 0
        failed_jobs = 0

        for j in jobs:
            is_job_healthy = j.status in (
                ExecutionLifecycle.QUEUED.value,
                ExecutionLifecycle.CLAIMED.value,
                ExecutionLifecycle.PREPARING.value,
                ExecutionLifecycle.RUNNING.value,
                ExecutionLifecycle.VERIFYING.value,
            )
            if j.status == ExecutionLifecycle.FAILED.value:
                failed_jobs += 1
            elif j.status in (ExecutionLifecycle.ASK_USER.value, ExecutionLifecycle.NEEDS_REVIEW.value):
                degraded_jobs += 1
            else:
                healthy_jobs += 1

            job_statuses.append(
                JobHealthStatus(
                    job_id=j.id,
                    status=j.status,
                    is_healthy=is_job_healthy,
                    progress=j.progress,
                    failed_rows=j.failed_rows,
                    active_step=j.current_table,
                    error_message=j.error_message,
                )
            )

        job_summary = {
            "total_tracked_jobs": len(jobs),
            "healthy_jobs": healthy_jobs,
            "degraded_jobs": degraded_jobs,
            "failed_jobs": failed_jobs,
            "health_verdict": "HEALTHY" if failed_jobs == 0 and degraded_jobs == 0 else ("DEGRADED" if healthy_jobs > 0 else "UNHEALTHY"),
        }

        # Overall operational assessment
        if agent_summary["health_verdict"] == "HEALTHY" and job_summary["health_verdict"] == "HEALTHY":
            overall = "HEALTHY"
        elif agent_summary["health_verdict"] == "UNHEALTHY" or job_summary["health_verdict"] == "UNHEALTHY":
            overall = "UNHEALTHY"
        else:
            overall = "DEGRADED"

        return OperationalHealthResponse(
            overall_health=overall,
            agent_health_summary=agent_summary,
            job_health_summary=job_summary,
            agents=agent_statuses,
            active_jobs=job_statuses,
        )
