"""
Evaluation Domain Pydantic Schemas & DTOs
Phase 6: Defines schemas for evaluation runs, scenario outcomes, regression diffs, and quality gates.
"""

from datetime import datetime
from typing import Any, Dict, List, Optional
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field


class EvaluationScenarioDTO(BaseModel):
    """Metadata and definition of an evaluation scenario."""
    scenario_id: str
    scenario_name: str
    description: str
    category: str
    source_dialects: List[str]
    target_dialect: str
    key_challenges: List[str]
    expected_outcome: str


class EvaluationSuiteRunRequest(BaseModel):
    """Payload to trigger an evaluation benchmark run."""
    suite_name: str = "phase6_representative_suite"
    model: str = "gpt-4o"
    model_version: Optional[str] = None
    prompt_version: str = "p-migration-2026.04"
    planner_version: str = "migraflow-planner-v2.0"
    selected_scenario_ids: Optional[List[str]] = None  # None evaluates all 15 scenarios


class EvaluationScenarioResultResponse(BaseModel):
    """Detailed result for an individual evaluation scenario."""
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    suite_run_id: UUID
    scenario_id: str
    scenario_name: str
    scenario_category: str
    status: str
    score: float
    planning_metrics: Dict[str, Any] = Field(default_factory=dict)
    recovery_metrics: Dict[str, Any] = Field(default_factory=dict)
    llm_metrics: Dict[str, Any] = Field(default_factory=dict)
    cost_latency_metrics: Dict[str, Any] = Field(default_factory=dict)
    failure_injection_metrics: Dict[str, Any] = Field(default_factory=dict)
    errors: List[str] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)
    created_at: datetime


class QualityGateRule(BaseModel):
    """A single quality gate threshold check."""
    gate_name: str
    target_threshold: str
    actual_value: Any
    passed: bool
    description: str


class QualityGateEvaluationResponse(BaseModel):
    """Overall outcome of the engineering quality gates."""
    overall_passed: bool
    timestamp: datetime
    gates: List[QualityGateRule] = Field(default_factory=list)


class EvaluationSuiteRunResponse(BaseModel):
    """Comprehensive summary of an entire evaluation suite run."""
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    suite_name: str
    model: str
    model_version: Optional[str] = None
    prompt_version: str
    planner_version: str
    status: str
    total_scenarios: int
    passed_scenarios: int
    failed_scenarios: int
    pass_rate: float
    average_planning_score: float
    recovery_routing_accuracy: float
    llm_output_valid_rate: float
    total_tokens: int
    total_llm_calls: int
    total_cost_usd: float
    total_duration_ms: float
    quality_gate_passed: bool
    quality_gate_details: Dict[str, Any] = Field(default_factory=dict)
    summary_report: Dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    updated_at: datetime
    scenario_results: List[EvaluationScenarioResultResponse] = Field(default_factory=list)


class RegressionComparisonResponse(BaseModel):
    """Diff and regression analysis between two evaluation runs."""
    baseline_run_id: UUID
    target_run_id: UUID
    baseline_version: str
    target_version: str
    pass_rate_delta: float
    planning_score_delta: float
    cost_delta_usd: float
    latency_delta_ms: float
    regressed_scenarios: List[str] = Field(default_factory=list)
    improved_scenarios: List[str] = Field(default_factory=list)
    unchanged_scenarios: List[str] = Field(default_factory=list)
    verdict: str  # "NO_REGRESSION", "REGRESSION_DETECTED", "IMPROVED"
    summary: str
