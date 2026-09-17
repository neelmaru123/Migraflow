"""
Execution Domain REST API Routes
"""

from typing import List
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.modules.agents.agents_dependencies import get_current_agent
from app.modules.agents.agents_models import Agent
from app.modules.execution.execution_schemas import (
    AgentTaskItemResponse,
    ExecutionCancelRequest,
    ExecutionJobResponse,
    ExecutionProgressUpdate,
    ExecutionStartRequest,
)
from app.modules.execution.execution_services import ExecutionService
from app.modules.users.users_models import User
from app.modules.users.users_routes import get_current_active_user

execution_router = APIRouter(tags=["Execution"])


@execution_router.post(
    "/plans/{plan_id}/execute",
    response_model=ExecutionJobResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Initiate execution for an approved MigrationPlan",
)
async def start_plan_execution(
    plan_id: UUID,
    body: ExecutionStartRequest = ExecutionStartRequest(),
    current_user: User = Depends(get_current_active_user),
    session: AsyncSession = Depends(get_db),
):
    """
    Triggers local migration execution for the specified plan ID.
    Queues a MigrationJob record and notifies the assigned Docker Agent.
    """
    return await ExecutionService.create_execution_job(
        session=session,
        user_id=current_user.id,
        plan_id=plan_id,
        is_dry_run=body.is_dry_run,
        truncate_target=body.truncate_target,
    )


@execution_router.post(
    "/executions/{id}/cancel",
    response_model=ExecutionJobResponse,
    summary="Cancel an active migration or dry-run execution job",
)
async def cancel_execution(
    id: UUID,
    body: ExecutionCancelRequest = ExecutionCancelRequest(),
    current_user: User = Depends(get_current_active_user),
    session: AsyncSession = Depends(get_db),
):
    """
    Cancels an active execution job, resets the assigned agent status,
    and unblocks the migration plan so it can be re-run immediately.
    """
    return await ExecutionService.cancel_execution_job(
        session=session,
        user_id=current_user.id,
        job_id=id,
        reason=body.reason,
    )


@execution_router.get(
    "/executions",
    response_model=List[ExecutionJobResponse],
    summary="List all execution jobs for current user",
)
async def list_user_executions(
    current_user: User = Depends(get_current_active_user),
    session: AsyncSession = Depends(get_db),
):
    """Returns execution history across all migration plans owned by the user."""
    return await ExecutionService.list_jobs_for_user(
        session=session, user_id=current_user.id
    )


@execution_router.get(
    "/executions/{id}",
    response_model=ExecutionJobResponse,
    summary="Get execution job details by ID",
)
async def get_execution_details(
    id: UUID,
    current_user: User = Depends(get_current_active_user),
    session: AsyncSession = Depends(get_db),
):
    """Returns detailed real-time progress and row counts for a specific execution job."""
    return await ExecutionService.get_job_by_id(
        session=session, user_id=current_user.id, job_id=id
    )


@execution_router.get(
    "/agents/tasks",
    response_model=List[AgentTaskItemResponse],
    summary="Poll for pending tasks (Docker Agent authentication required)",
)
async def poll_agent_tasks(
    agent: Agent = Depends(get_current_agent),
    session: AsyncSession = Depends(get_db),
):
    """
    Endpoint polled by Docker Agent to discover queued execution jobs.
    Authenticated via X-Agent-Token request header.
    """
    jobs = await ExecutionService.get_pending_tasks_for_agent(
        session=session, agent_id=agent.id
    )
    return [
        AgentTaskItemResponse(
            job_id=job.id,
            migration_plan_id=job.migration_plan_id,
            status=job.status,
            is_dry_run=job.is_dry_run,
            truncate_target=job.truncate_target,
            created_at=job.created_at,
        )
        for job in jobs
    ]


@execution_router.get(
    "/plans/{plan_id}/jobs",
    response_model=List[ExecutionJobResponse],
    summary="List all execution jobs for a specific migration plan",
)
async def list_plan_jobs(
    plan_id: UUID,
    current_user: User = Depends(get_current_active_user),
    session: AsyncSession = Depends(get_db),
):
    """Returns historical execution runs for a migration plan ordered newest first."""
    return await ExecutionService.list_jobs_for_plan(
        session=session, plan_id=plan_id, user_id=current_user.id
    )


@execution_router.post(
    "/executions/{id}/diagnose",
    response_model=ExecutionJobResponse,
    summary="Synthesize AI failure diagnosis for an execution job",
)
async def diagnose_execution_failure(
    id: UUID,
    current_user: User = Depends(get_current_active_user),
    session: AsyncSession = Depends(get_db),
):
    """Triggers AI failure analysis and synthesizes plain English remediation steps."""
    return await ExecutionService.diagnose_job_failure(session=session, job_id=id)


@execution_router.post(
    "/executions/{id}/progress",
    response_model=ExecutionJobResponse,
    summary="Update execution job progress (Docker Agent authentication required)",
)
async def update_execution_progress(
    id: UUID,
    update: ExecutionProgressUpdate,
    agent: Agent = Depends(get_current_agent),
    session: AsyncSession = Depends(get_db),
):
    """
    Endpoint used by Docker Agent to report live row processing stats and stage updates.
    Authenticated via X-Agent-Token request header.
    """
    return await ExecutionService.update_job_progress(
        session=session, job_id=id, update=update, agent_id=agent.id
    )
