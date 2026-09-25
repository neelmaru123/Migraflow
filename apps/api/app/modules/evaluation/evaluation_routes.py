"""
Evaluation REST API Endpoints
Phase 6: Exposes evaluation datasets, benchmark runs, regression analysis, and failure simulation.
"""

from typing import Any, Dict, List, Optional
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.modules.evaluation.evaluation_schemas import (
    EvaluationScenarioDTO,
    EvaluationSuiteRunRequest,
    EvaluationSuiteRunResponse,
    RegressionComparisonResponse,
)
from app.modules.evaluation.evaluation_service import EvaluationService
from app.modules.users.users_dependencies import get_current_active_user
from app.modules.users.users_models import User

router = APIRouter(prefix="/evaluation", tags=["Agentic Evaluation & Regression Testing"])


class RegressionComparisonRequest(BaseModel):
    baseline_run_id: uuid.UUID
    target_run_id: uuid.UUID


@router.get("/scenarios", response_model=List[EvaluationScenarioDTO])
async def list_evaluation_scenarios(
    current_user: User = Depends(get_current_active_user),
):
    """Lists all 15 representative synthetic migration scenarios in the evaluation dataset."""
    return EvaluationService.get_available_scenarios()


@router.post("/runs", response_model=EvaluationSuiteRunResponse, status_code=status.HTTP_201_CREATED)
async def trigger_evaluation_suite_run(
    request: EvaluationSuiteRunRequest,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
):
    """Triggers an evaluation benchmark run across the evaluation dataset."""
    suite_run = await EvaluationService.run_evaluation_suite(db=db, request=request)
    return suite_run


@router.get("/runs", response_model=List[EvaluationSuiteRunResponse])
async def list_evaluation_suite_runs(
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
):
    """Lists historical evaluation suite benchmark runs."""
    return await EvaluationService.list_suite_runs(db=db, limit=limit, offset=offset)


@router.get("/runs/{run_id}", response_model=EvaluationSuiteRunResponse)
async def get_evaluation_suite_run(
    run_id: uuid.UUID,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
):
    """Retrieves detailed results for a specific evaluation suite run."""
    suite_run = await EvaluationService.get_suite_run(db=db, run_id=run_id)
    if not suite_run:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Evaluation suite run with ID {run_id} not found",
        )
    return suite_run


@router.post("/regression/compare", response_model=RegressionComparisonResponse)
async def compare_evaluation_runs(
    request: RegressionComparisonRequest,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
):
    """Compares baseline and target evaluation runs to detect regressions in quality, cost, and latency."""
    diff = await EvaluationService.compare_suite_runs(
        db=db,
        baseline_run_id=request.baseline_run_id,
        target_run_id=request.target_run_id,
    )
    if not diff:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="One or both evaluation runs not found for comparison",
        )
    return diff


@router.post("/failures/simulate", response_model=Dict[str, Any])
async def simulate_operational_failures(
    current_user: User = Depends(get_current_active_user),
):
    """Executes controlled simulation of all 7 operational failure scenarios."""
    return EvaluationService.run_all_failure_injections()
