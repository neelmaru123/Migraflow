"""
Observability Domain Pydantic Schemas & DTOs
"""

from datetime import datetime
from typing import Any, Dict, List, Optional, Union
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field


class ExecutionTraceResponse(BaseModel):
    """DTO for an individual execution trace span."""
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    trace_id: UUID
    parent_run_id: Optional[UUID] = None
    migration_job_id: Optional[UUID] = None
    agent_run_id: Optional[UUID] = None
    execution_step_id: Optional[UUID] = None
    operation_type: str
    name: str
    status: str
    started_at: datetime
    finished_at: Optional[datetime] = None
    duration_ms: Optional[float] = None
    error_type: Optional[str] = None
    error_message: Optional[str] = None
    metadata_snapshot: Dict[str, Any] = Field(default_factory=dict)
    created_at: datetime


class TraceTreeResponse(BaseModel):
    """Hierarchical trace tree (AgentRun -> ExecutionStep -> ToolRun/LLMRun)."""
    span: ExecutionTraceResponse
    children: List["TraceTreeResponse"] = Field(default_factory=list)
    otel_span: Dict[str, Any] = Field(default_factory=dict)

    @property
    def name(self) -> str:
        return self.span.name



class LLMCallRecordResponse(BaseModel):
    """DTO for a tracked LLM call record."""
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    trace_id: UUID
    execution_trace_id: Optional[UUID] = None
    migration_plan_id: Optional[UUID] = None
    migration_job_id: Optional[UUID] = None
    model: str
    provider: str
    model_version: Optional[str] = None
    prompt_version: str
    planner_version: str
    schema_version: int
    request_timestamp: datetime
    latency_ms: float
    prompt_tokens: Optional[int] = None
    completion_tokens: Optional[int] = None
    total_tokens: Optional[int] = None
    estimated_cost_usd: Optional[float] = None
    status: str
    structured_output_valid: bool
    validation_error: Optional[str] = None
    sanitized_prompt_preview: Optional[str] = None
    error_message: Optional[str] = None
    created_at: datetime


class ResourceBudgetResponse(BaseModel):
    """DTO for a migration job resource budget and real-time consumption."""
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    migration_job_id: Optional[UUID] = None
    user_id: Optional[UUID] = None
    max_llm_calls: int
    max_replans: int
    max_retries: int
    max_execution_duration_seconds: int
    max_concurrent_steps: int
    max_tokens: int
    max_cost_usd: float
    current_llm_calls: int
    current_replans: int
    current_retries: int
    current_tokens: int
    current_cost_usd: float
    is_exceeded: bool
    exceeded_limit_type: Optional[str] = None
    created_at: datetime
    updated_at: datetime


class ResourceBudgetUpdateRequest(BaseModel):
    """Payload to configure resource budget limits."""
    max_llm_calls: Optional[int] = None
    max_replans: Optional[int] = None
    max_retries: Optional[int] = None
    max_execution_duration_seconds: Optional[int] = None
    max_concurrent_steps: Optional[int] = None
    max_tokens: Optional[int] = None
    max_cost_usd: Optional[float] = None


class ObservabilityMetricsResponse(BaseModel):
    """Aggregated actionable operational metrics."""
    jobs_started_total: int = 0
    jobs_completed_total: int = 0
    jobs_failed_total: int = 0
    jobs_cancelled_total: int = 0
    average_execution_duration_seconds: float = 0.0
    retry_count_total: int = 0
    recovery_count_total: int = 0
    replan_count_total: int = 0
    verification_failures_total: int = 0
    agent_availability_ratio: float = 1.0
    average_step_duration_seconds: float = 0.0
    llm_calls_total: int = 0
    llm_average_latency_ms: float = 0.0
    llm_tokens_total: int = 0
    llm_estimated_cost_usd_total: float = 0.0

    @property
    def total_llm_calls(self) -> int:
        return self.llm_calls_total

    @property
    def total_tokens_consumed(self) -> int:
        return self.llm_tokens_total

    @property
    def estimated_ai_cost_usd(self) -> float:
        return self.llm_estimated_cost_usd_total

    @property
    def agent_availability_percentage(self) -> float:
        return self.agent_availability_ratio * 100.0



class AgentHealthStatus(BaseModel):
    """Health status of an individual execution agent."""
    agent_id: UUID
    name: str
    status: str
    is_healthy: bool
    last_seen_at: Optional[datetime] = None
    version: Optional[str] = None


class JobHealthStatus(BaseModel):
    """Health status of a migration execution job."""
    job_id: UUID
    status: str
    is_healthy: bool
    progress: float
    failed_rows: int
    active_step: Optional[str] = None
    error_message: Optional[str] = None


class OperationalHealthResponse(BaseModel):
    """
    Segregated Operational Health View.
    Decouples Agent Health (Docker container connectivity) from Job Health (DAG execution state).
    """
    overall_health: str  # HEALTHY, DEGRADED, UNHEALTHY
    agent_health_summary: Dict[str, Any]
    job_health_summary: Dict[str, Any]
    agents: List[AgentHealthStatus] = Field(default_factory=list)
    active_jobs: List[JobHealthStatus] = Field(default_factory=list)


class PlanProvenanceResponse(BaseModel):
    """Answers: 'Which model/prompt generated this migration plan?'"""
    plan_id: UUID
    version_number: int
    ai_model: Optional[str] = None
    model_version: Optional[str] = None
    prompt_version: Optional[str] = None
    planner_version: str = "migraflow-planner-v2.0"
    schema_version: Union[int, str] = 1
    confidence_score: float = 1.0
    generated_at: datetime

    @property
    def model(self) -> Optional[str]:
        return self.ai_model

