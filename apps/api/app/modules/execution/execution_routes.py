"""
Execution Domain REST API Routes
"""

from typing import List, Optional
from uuid import UUID
from fastapi import APIRouter, Depends, Header, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.modules.agents.agents_dependencies import get_current_agent
from app.modules.agents.agents_models import Agent
from app.modules.execution.execution_schemas import (
    AgentRunResponse,
    AgentTaskItemResponse,
    ExecutionCancelRequest,
    ExecutionCheckpointCreate,
    ExecutionCheckpointResponse,
    ExecutionEventResponse,
    ExecutionJobResponse,
    ExecutionPlanResponse,
    ExecutionProgressUpdate,
    ExecutionStartRequest,
    ExecutionStepClaimRequest,
    ExecutionStepCompleteRequest,
    ExecutionStepFailRequest,
    ExecutionStepResponse,
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
    idempotency_key_header: Optional[str] = Header(None, alias="Idempotency-Key"),
    current_user: User = Depends(get_current_active_user),
    session: AsyncSession = Depends(get_db),
):
    """
    Triggers local migration execution for the specified plan ID.
    Queues a MigrationJob record and notifies the assigned Docker Agent.
    Supports idempotency via Idempotency-Key header or request body.
    """
    effective_idempotency_key = idempotency_key_header or body.idempotency_key
    return await ExecutionService.create_execution_job(
        session=session,
        user_id=current_user.id,
        plan_id=plan_id,
        is_dry_run=body.is_dry_run,
        truncate_target=body.truncate_target,
        idempotency_key=effective_idempotency_key,
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
    "/executions/{id}/runs",
    response_model=List[AgentRunResponse],
    summary="List all agent execution runs for a migration job",
)
async def list_job_runs(
    id: UUID,
    current_user: User = Depends(get_current_active_user),
    session: AsyncSession = Depends(get_db),
):
    """Returns all execution attempts (AgentRuns) for a specific migration job."""
    return await ExecutionService.list_runs_for_job(
        session=session, job_id=id, user_id=current_user.id
    )


@execution_router.get(
    "/executions/{id}/events",
    response_model=List[ExecutionEventResponse],
    summary="List append-only audit event history for a migration job",
)
async def list_job_events(
    id: UUID,
    current_user: User = Depends(get_current_active_user),
    session: AsyncSession = Depends(get_db),
):
    """Returns durable audit event history for a specific migration job."""
    return await ExecutionService.list_events_for_job(
        session=session, job_id=id, user_id=current_user.id
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
            agent_run_id=job.current_run_id,
            execution_plan_id=getattr(job, "execution_plan_id", None),
            status=job.status,
            is_dry_run=job.is_dry_run,
            truncate_target=job.truncate_target,
            created_at=job.created_at,
        )
        for job in jobs
    ]


@execution_router.get(
    "/executions/{id}/plan",
    response_model=Optional[ExecutionPlanResponse],
    summary="Get derived MigrationExecutionPlan with steps and checkpoints",
)
async def get_execution_plan(
    id: UUID,
    current_user: User = Depends(get_current_active_user),
    session: AsyncSession = Depends(get_db),
):
    """Returns the granular execution DAG, steps, and authoritative checkpoints for a job."""
    plan = await ExecutionService.get_execution_plan_for_job(
        session=session, job_id=id, user_id=current_user.id
    )
    if not plan:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Execution plan for job '{id}' not found.",
        )
    return plan


@execution_router.post(
    "/executions/plans/{plan_id}/steps/claim",
    response_model=Optional[ExecutionStepResponse],
    summary="Claim the next eligible execution step for an agent run",
)
async def claim_execution_step(
    plan_id: UUID,
    body: ExecutionStepClaimRequest,
    agent: Agent = Depends(get_current_agent),
    session: AsyncSession = Depends(get_db),
):
    """
    Atomically claims the next eligible step whose dependencies are satisfied.
    Authenticated via X-Agent-Token request header.
    """
    return await ExecutionService.claim_next_step(
        session=session,
        execution_plan_id=plan_id,
        agent_id=agent.id,
        agent_run_id=body.agent_run_id,
    )


@execution_router.post(
    "/executions/steps/{step_id}/checkpoint",
    response_model=ExecutionCheckpointResponse,
    summary="Save an authoritative execution checkpoint in the control plane",
)
async def save_checkpoint(
    step_id: UUID,
    body: ExecutionCheckpointCreate,
    agent: Agent = Depends(get_current_agent),
    session: AsyncSession = Depends(get_db),
):
    """
    Persists an authoritative checkpoint to PostgreSQL/SQLite to support safe resumes.
    Authenticated via X-Agent-Token request header.
    """
    return await ExecutionService.save_step_checkpoint(
        session=session,
        step_id=step_id,
        source_identifier=body.source_identifier,
        source_table=body.source_table,
        target_table=body.target_table,
        cursor_offset=body.cursor_offset,
        rows_processed=body.rows_processed,
        source_position=body.source_position,
    )


@execution_router.get(
    "/executions/steps/{step_id}/checkpoints",
    response_model=List[ExecutionCheckpointResponse],
    summary="Get authoritative checkpoints for an execution step",
)
async def get_step_checkpoints(
    step_id: UUID,
    agent: Agent = Depends(get_current_agent),
    session: AsyncSession = Depends(get_db),
):
    """Fetches all authoritative checkpoints associated with a step."""
    return await ExecutionService.get_step_checkpoints(session=session, step_id=step_id)


@execution_router.post(
    "/executions/steps/{step_id}/complete",
    response_model=ExecutionStepResponse,
    summary="Mark an execution step completed",
)
async def complete_step(
    step_id: UUID,
    body: ExecutionStepCompleteRequest = ExecutionStepCompleteRequest(),
    agent: Agent = Depends(get_current_agent),
    session: AsyncSession = Depends(get_db),
):
    """
    Marks a step completed, advances DAG dependencies, and completes the execution plan if all steps finish.
    """
    return await ExecutionService.complete_step(
        session=session, step_id=step_id, output_summary=body.output_summary
    )


@execution_router.post(
    "/executions/steps/{step_id}/fail",
    response_model=ExecutionStepResponse,
    summary="Report failure on an execution step and evaluate retry policy",
)
async def fail_step(
    step_id: UUID,
    body: ExecutionStepFailRequest,
    agent: Agent = Depends(get_current_agent),
    session: AsyncSession = Depends(get_db),
):
    """
    Evaluates retry policy against the reported error. If retryable, marks step RETRYING; otherwise FAILED.
    """
    return await ExecutionService.fail_step(
        session=session,
        step_id=step_id,
        error_type=body.error_type,
        error_message=body.error_message,
    )


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
