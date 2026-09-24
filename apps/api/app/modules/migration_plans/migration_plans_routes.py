"""
Migration Plans Domain — FastAPI REST API Routes
"""

import uuid
from typing import Any, Dict, List, Optional
from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.db import get_db
from app.modules.agents.agents_dependencies import hash_agent_token
from app.modules.agents.agents_models import Agent
from app.modules.migration_plans.migration_plans_schemas import (
    PlanDetailResponse,
    PlanGenerationJobResponse,
    PlanGenerationRequest,
    PlanGenerationStatusResponse,
    PlanRefineRequest,
    PlanRefinementJobResponse,
    PlanRefinementStatusResponse,
    PlanResponse,
    PlanValidationResultResponse,
    PlanVersionDetailResponse,
    PlanVersionListItem,
)
from app.modules.migration_plans.migration_plans_services import MigrationPlanService
from app.modules.users.users_dependencies import get_current_active_user, get_current_user
from app.modules.users.users_models import User

router = APIRouter(prefix="/plans", tags=["Migration Plans & AI Generation"])


def _to_plan_detail_response(plan) -> PlanDetailResponse:
    warnings = []
    if plan.validation_errors and isinstance(plan.validation_errors, dict):
        warnings = plan.validation_errors.get("warnings", [])

    plan_data = plan.plan_data or {}
    if isinstance(plan_data, dict) and "pre_migration_ddl" in plan_data:
        import re
        target_type = "postgresql"
        if plan.target_config and isinstance(plan.target_config, dict):
            target_type = plan.target_config.get("database_type", "postgresql").lower()
        if "postgres" in target_type:
            sanitized_ddl = []
            for stmt in plan_data.get("pre_migration_ddl", []):
                stmt_clean = re.sub(r'\bDEFAULT\s+(?:uuid_v4|uuidv4)\(\)', 'DEFAULT gen_random_uuid()', str(stmt), flags=re.IGNORECASE)
                stmt_clean = re.sub(r'\b(?:uuid_v4|uuidv4)\(\)', 'gen_random_uuid()', stmt_clean, flags=re.IGNORECASE)
                sanitized_ddl.append(stmt_clean)
            plan_data = dict(plan_data)
            plan_data["pre_migration_ddl"] = sanitized_ddl

    return PlanDetailResponse(
        id=plan.id,
        agent_id=plan.agent_id,
        status=plan.status,
        ai_model=plan.ai_model,
        confidence_score=plan.confidence_score,
        is_valid=plan.is_valid,
        validation_errors=plan.validation_errors,
        validation_warnings=warnings,
        created_at=plan.created_at,
        updated_at=plan.updated_at,
        plan_data=plan_data,
        target_config=plan.target_config,
        prompt_version=plan.prompt_version,
    )


async def _get_agent_for_user(
    agent_id: uuid.UUID,
    current_user: User,
    session: AsyncSession,
) -> Agent:
    """Fetch and verify agent ownership."""
    from sqlalchemy import select
    from app.modules.sources.sources_models import DataSource

    stmt = (
        select(Agent)
        .where(Agent.id == agent_id, Agent.user_id == current_user.id)
        .options(selectinload(Agent.data_sources))
    )
    res = await session.execute(stmt)
    agent = res.scalar_one_or_none()
    if not agent:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Agent '{agent_id}' not found or does not belong to your account.",
        )
    return agent


@router.post("/generate", response_model=PlanDetailResponse, status_code=status.HTTP_201_CREATED)
async def generate_migration_plan(
    payload: PlanGenerationRequest,
    current_user: User = Depends(get_current_active_user),
    session: AsyncSession = Depends(get_db),
):
    """
    Generate an AI Migration Plan using LangGraph StateGraph engine.
    """
    agent = await _get_agent_for_user(payload.agent_id, current_user, session)
    plan = await MigrationPlanService.create_plan_for_agent(
        session=session, agent=agent, target_config=payload.target_config
    )
    return _to_plan_detail_response(plan)


@router.post(
    "/generate-async",
    response_model=PlanGenerationJobResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def generate_migration_plan_async(
    payload: PlanGenerationRequest,
    current_user: User = Depends(get_current_active_user),
    session: AsyncSession = Depends(get_db),
):
    """
    Initiate asynchronous AI Migration Plan generation via detached background task (returns 202 Accepted).
    """
    agent = await _get_agent_for_user(payload.agent_id, current_user, session)
    task_id, plan_id = await MigrationPlanService.start_async_generation(
        session=session, agent=agent, target_config=payload.target_config
    )
    return PlanGenerationJobResponse(
        task_id=task_id,
        agent_id=agent.id,
        plan_id=plan_id,
        status="processing",
        message="AI plan generation started in background",
    )


@router.get(
    "/agent/{agent_id}/generation-status",
    response_model=PlanGenerationStatusResponse,
)
async def get_agent_generation_status(
    agent_id: uuid.UUID,
    current_user: User = Depends(get_current_active_user),
    session: AsyncSession = Depends(get_db),
):
    """
    Check the status of an ongoing or recently completed background plan generation for an agent.
    """
    await _get_agent_for_user(agent_id, current_user, session)
    return await MigrationPlanService.get_generation_status(session, agent_id)


@router.get("", response_model=List[PlanResponse])
async def list_migration_plans(
    current_user: User = Depends(get_current_active_user),
    session: AsyncSession = Depends(get_db),
):
    """List all migration plans for the authenticated user."""
    plans = await MigrationPlanService.list_plans_for_user(
        session=session, user_id=current_user.id
    )
    return [
        PlanResponse(
            id=p.id,
            agent_id=p.agent_id,
            status=p.status,
            ai_model=p.ai_model,
            confidence_score=p.confidence_score,
            is_valid=p.is_valid,
            created_at=p.created_at,
            updated_at=p.updated_at,
        )
        for p in plans
    ]


@router.get("/{plan_id}", response_model=PlanDetailResponse)
async def get_migration_plan(
    plan_id: uuid.UUID,
    request: Request,
    x_agent_token: Optional[str] = Header(None, alias="X-Agent-Token"),
    session: AsyncSession = Depends(get_db),
):
    """
    Fetch the complete migration plan including full TransformationPlanAST JSON for UI rendering.
    """
    plan = await MigrationPlanService.get_plan_by_id(session, plan_id)
    if not plan:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Migration plan '{plan_id}' not found.",
        )

    # 1. Agent Token Auth
    if x_agent_token:
        token_hash = hash_agent_token(x_agent_token)
        from sqlalchemy import select
        res_agent = await session.execute(select(Agent).where(Agent.api_token_hash == token_hash))
        agent = res_agent.scalar_one_or_none()
        if agent and plan.agent_id == agent.id:
            return _to_plan_detail_response(plan)

    # 2. User Auth Fallback
    try:
        current_user = await get_current_user(request=request, bearer_token=None, db=session)
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required. Provide valid user session or X-Agent-Token.",
        )

    if not current_user or plan.user_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You do not have permission to view this migration plan.",
        )

    return _to_plan_detail_response(plan)


@router.put("/{plan_id}", response_model=PlanDetailResponse)
async def update_migration_plan(
    plan_id: uuid.UUID,
    plan_data: Dict[str, Any],
    current_user: User = Depends(get_current_active_user),
    session: AsyncSession = Depends(get_db),
):
    """Save user-edited plan data and re-evaluate feasibility validation."""
    plan = await MigrationPlanService.get_plan_by_id(session, plan_id)
    if not plan:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Migration plan '{plan_id}' not found.",
        )
    if plan.user_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You do not have permission to edit this migration plan.",
        )
    updated = await MigrationPlanService.update_plan_data(session, plan, plan_data)
    return _to_plan_detail_response(updated)


@router.post("/{plan_id}/refine", response_model=PlanDetailResponse)
async def refine_migration_plan(
    plan_id: uuid.UUID,
    payload: PlanRefineRequest,
    current_user: User = Depends(get_current_active_user),
    session: AsyncSession = Depends(get_db),
):
    """Refine migration plan using natural language feedback instruction."""
    plan = await MigrationPlanService.get_plan_by_id(session, plan_id)
    if not plan:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Migration plan '{plan_id}' not found.",
        )
    if plan.user_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You do not have permission to refine this migration plan.",
        )
    refined = await MigrationPlanService.refine_plan(session, plan, payload.user_feedback)
    return _to_plan_detail_response(refined)


@router.post(
    "/{plan_id}/refine-async",
    response_model=PlanRefinementJobResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def refine_migration_plan_async(
    plan_id: uuid.UUID,
    payload: PlanRefineRequest,
    current_user: User = Depends(get_current_active_user),
    session: AsyncSession = Depends(get_db),
):
    """Refine migration plan asynchronously via background task (returns 202 Accepted)."""
    plan = await MigrationPlanService.get_plan_by_id(session, plan_id)
    if not plan:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Migration plan '{plan_id}' not found.",
        )
    if plan.user_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You do not have permission to refine this migration plan.",
        )
    task_id = await MigrationPlanService.start_async_refinement(
        session, plan, payload.user_feedback
    )
    return PlanRefinementJobResponse(
        task_id=task_id,
        plan_id=plan.id,
        status="processing",
        message="AI plan refinement started in background",
    )


@router.get("/{plan_id}/refine/status", response_model=PlanRefinementStatusResponse)
async def get_refinement_status(
    plan_id: uuid.UUID,
    task_id: Optional[str] = Query(None, description="Specific refinement task ID"),
    current_user: User = Depends(get_current_active_user),
    session: AsyncSession = Depends(get_db),
):
    """Get current status and elapsed time of an ongoing or completed plan refinement."""
    plan = await MigrationPlanService.get_plan_by_id(session, plan_id)
    if not plan:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Migration plan '{plan_id}' not found.",
        )
    if plan.user_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You do not have permission to inspect this migration plan.",
        )
    return await MigrationPlanService.get_refinement_status(session, plan, task_id=task_id)


@router.post("/{plan_id}/validate", response_model=PlanValidationResultResponse)
async def validate_migration_plan(
    plan_id: uuid.UUID,
    current_user: User = Depends(get_current_active_user),
    session: AsyncSession = Depends(get_db),
):
    """Run standalone feasibility check on a plan blueprint."""
    plan = await MigrationPlanService.get_plan_by_id(session, plan_id)
    if not plan:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Migration plan '{plan_id}' not found.",
        )
    if plan.user_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You do not have permission to validate this migration plan.",
        )
    val_dict = await MigrationPlanService.validate_plan_by_id(session, plan)
    return PlanValidationResultResponse(
        is_valid=val_dict.get("is_valid", False),
        errors=val_dict.get("errors", []),
        warnings=val_dict.get("warnings", []),
        explanation=val_dict.get("explanation", ""),
    )


@router.post("/{plan_id}/approve", response_model=PlanDetailResponse)
async def approve_migration_plan(
    plan_id: uuid.UUID,
    current_user: User = Depends(get_current_active_user),
    session: AsyncSession = Depends(get_db),
):
    """Approve a migration plan for execution."""
    plan = await MigrationPlanService.get_plan_by_id(session, plan_id)
    if not plan:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Migration plan '{plan_id}' not found.",
        )
    if plan.user_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access forbidden: You do not own this migration plan.",
        )
    approved = await MigrationPlanService.approve_plan(session, plan, user=current_user)
    return _to_plan_detail_response(approved)


@router.get("/{plan_id}/versions", response_model=List[PlanVersionListItem])
async def list_migration_plan_versions(
    plan_id: uuid.UUID,
    current_user: User = Depends(get_current_active_user),
    session: AsyncSession = Depends(get_db),
):
    """List all version snapshots for a migration plan (metadata list only, no plan_data payloads)."""
    plan = await MigrationPlanService.get_plan_by_id(session, plan_id)
    if not plan:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Migration plan '{plan_id}' not found.",
        )
    if plan.user_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You do not have permission to view versions for this migration plan.",
        )
    versions = await MigrationPlanService.list_plan_versions(session, plan_id)
    return versions


@router.get("/{plan_id}/versions/{version_number}", response_model=PlanVersionDetailResponse)
async def get_migration_plan_version(
    plan_id: uuid.UUID,
    version_number: int,
    current_user: User = Depends(get_current_active_user),
    session: AsyncSession = Depends(get_db),
):
    """Fetch full AST detail of a specific historical version snapshot for read-only preview."""
    plan = await MigrationPlanService.get_plan_by_id(session, plan_id)
    if not plan:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Migration plan '{plan_id}' not found.",
        )
    if plan.user_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You do not have permission to view versions for this migration plan.",
        )
    version = await MigrationPlanService.get_plan_version(session, plan_id, version_number)
    if not version:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Version {version_number} not found for migration plan '{plan_id}'.",
        )
    return version


@router.post("/{plan_id}/versions/{version_number}/restore", response_model=PlanDetailResponse)
async def restore_migration_plan_version(
    plan_id: uuid.UUID,
    version_number: int,
    current_user: User = Depends(get_current_active_user),
    session: AsyncSession = Depends(get_db),
):
    """Restore active migration plan blueprint AST to a historical version."""
    plan = await MigrationPlanService.get_plan_by_id(session, plan_id)
    if not plan:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Migration plan '{plan_id}' not found.",
        )
    if plan.user_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You do not have permission to modify this migration plan.",
        )
    restored_plan = await MigrationPlanService.restore_plan_version(session, plan, version_number)
    return _to_plan_detail_response(restored_plan)

