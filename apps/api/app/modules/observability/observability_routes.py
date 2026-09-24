"""
Observability REST API Routes
Exposes traces, hierarchical trace trees, LLM call logs, budgets, operational metrics, and health.
"""

from typing import Any, Dict, List, Optional
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.modules.migration_plans.migration_plans_models import MigrationPlan
from app.modules.observability.budget_manager import ResourceBudgetManager
from app.modules.observability.health_service import OperationalHealthService
from app.modules.observability.metrics_service import ObservabilityMetricsService
from app.modules.observability.observability_models import ExecutionTrace, LLMCallRecord
from app.modules.observability.observability_schemas import (
    ExecutionTraceResponse,
    LLMCallRecordResponse,
    ObservabilityMetricsResponse,
    OperationalHealthResponse,
    PlanProvenanceResponse,
    ResourceBudgetResponse,
    ResourceBudgetUpdateRequest,
    TraceTreeResponse,
)
from app.modules.observability.tracer import ExecutionTracer
from app.modules.users.users_dependencies import get_current_active_user
from app.modules.users.users_models import User

router = APIRouter(prefix="/observability", tags=["Observability, Tracing & Operational Controls"])


@router.get("/traces", response_model=List[ExecutionTraceResponse])
async def list_traces(
    job_id: Optional[uuid.UUID] = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    current_user: User = Depends(get_current_active_user),
    session: AsyncSession = Depends(get_db),
):
    """List execution trace spans with optional job_id filter."""
    stmt = select(ExecutionTrace).order_by(ExecutionTrace.started_at.desc()).limit(limit)
    if job_id:
        stmt = stmt.where(ExecutionTrace.migration_job_id == job_id)
    res = await session.execute(stmt)
    return list(res.scalars().all())


@router.get("/traces/{trace_id}", response_model=TraceTreeResponse)
async def get_trace_tree(
    trace_id: uuid.UUID,
    current_user: User = Depends(get_current_active_user),
    session: AsyncSession = Depends(get_db),
):
    """Retrieve full hierarchical execution trace tree (AgentRun -> ExecutionStep -> ToolRun/LLMRun)."""
    tree = await ExecutionTracer.get_trace_tree(session, trace_id)
    if not tree:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Trace with root trace_id '{trace_id}' not found.",
        )
    return tree


@router.get("/llm-calls", response_model=List[LLMCallRecordResponse])
async def list_llm_calls(
    job_id: Optional[uuid.UUID] = Query(default=None),
    plan_id: Optional[uuid.UUID] = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    current_user: User = Depends(get_current_active_user),
    session: AsyncSession = Depends(get_db),
):
    """List recorded LLM invocations with model provenance, tokens, and cost estimates."""
    stmt = select(LLMCallRecord).order_by(LLMCallRecord.request_timestamp.desc()).limit(limit)
    if job_id:
        stmt = stmt.where(LLMCallRecord.migration_job_id == job_id)
    if plan_id:
        stmt = stmt.where(LLMCallRecord.migration_plan_id == plan_id)
    res = await session.execute(stmt)
    return list(res.scalars().all())


@router.get("/budgets/{job_id}", response_model=ResourceBudgetResponse)
async def get_job_budget(
    job_id: uuid.UUID,
    current_user: User = Depends(get_current_active_user),
    session: AsyncSession = Depends(get_db),
):
    """Get the resource budget limits and live usage counters for a migration job."""
    budget = await ResourceBudgetManager.get_or_create_budget(session, job_id, user_id=current_user.id)
    return budget


@router.put("/budgets/{job_id}", response_model=ResourceBudgetResponse)
async def update_job_budget(
    job_id: uuid.UUID,
    payload: ResourceBudgetUpdateRequest,
    current_user: User = Depends(get_current_active_user),
    session: AsyncSession = Depends(get_db),
):
    """Update configurable resource budget limits for a migration job."""
    budget = await ResourceBudgetManager.get_or_create_budget(session, job_id, user_id=current_user.id)
    update_data = payload.model_dump(exclude_unset=True)
    for field, val in update_data.items():
        if val is not None and hasattr(budget, field):
            setattr(budget, field, val)
    await session.commit()
    await session.refresh(budget)
    return budget


@router.get("/metrics", response_model=ObservabilityMetricsResponse)
async def get_platform_metrics(
    current_user: User = Depends(get_current_active_user),
    session: AsyncSession = Depends(get_db),
):
    """Retrieve actionable platform metrics (jobs, retries, replans, agent availability, LLM tokens/costs)."""
    metrics = await ObservabilityMetricsService.get_metrics(session)
    return metrics


@router.get("/health", response_model=OperationalHealthResponse)
async def get_operational_health(
    current_user: User = Depends(get_current_active_user),
    session: AsyncSession = Depends(get_db),
):
    """Retrieve segregated operational health, cleanly separating Agent health from Job health."""
    health = await OperationalHealthService.evaluate_health(session)
    return health


@router.get("/plans/{plan_id}/provenance", response_model=PlanProvenanceResponse)
async def get_plan_provenance(
    plan_id: uuid.UUID,
    current_user: User = Depends(get_current_active_user),
    session: AsyncSession = Depends(get_db),
):
    """
    Answers: 'Which model/prompt generated this migration plan?'
    Returns exact model, model_version, prompt_version, planner_version, and schema_version.
    """
    stmt = select(MigrationPlan).where(MigrationPlan.id == plan_id)
    res = await session.execute(stmt)
    plan = res.scalar_one_or_none()
    if not plan:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Migration plan '{plan_id}' not found.",
        )
    plan_data = plan.plan_data or {}
    ai_model = plan.ai_model or plan_data.get("planner_model") or plan_data.get("model") or "gpt-4o"
    model_version = plan_data.get("model_version")
    prompt_version = plan.prompt_version or plan_data.get("prompt_version") or "v1.1.0"
    planner_version = plan_data.get("planner_version") or "migraflow-planner-v2.0"
    schema_version = plan_data.get("schema_version") or 1

    return PlanProvenanceResponse(
        plan_id=plan.id,
        version_number=plan.approved_version_number or 1,
        ai_model=ai_model,
        model_version=model_version,
        prompt_version=prompt_version,
        planner_version=planner_version,
        schema_version=schema_version,
        confidence_score=plan.confidence_score,
        generated_at=plan.created_at,
    )
