"""
Execution Domain Pydantic Schemas
"""

from datetime import datetime
from typing import Any, Dict, List, Optional
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field


class ExecutionStartRequest(BaseModel):
    """Request payload to initiate execution for an approved MigrationPlan."""
    chunk_size: Optional[int] = Field(default=50000, ge=1000, le=500000, description="Rows per ETL chunk batch")
    is_dry_run: bool = Field(default=False, description="Simulate migration without executing DDL or writing to target DB")
    truncate_target: bool = Field(default=False, description="Clean wipe/drop all existing tables in target database before executing DDL and migration")
    idempotency_key: Optional[str] = Field(default=None, max_length=255, description="Client idempotency key to prevent duplicate job submissions")


class ExecutionCancelRequest(BaseModel):
    """Optional request payload when cancelling/resetting an active execution job."""
    reason: Optional[str] = Field(default=None, description="Optional cancellation reason or explanation")


class ExecutionProgressUpdate(BaseModel):
    """Progress metrics payload sent periodically by Docker Agent."""
    status: str = Field(..., examples=["running", "completed", "failed", "ddl_executing", "dry_run_completed"])
    agent_run_id: Optional[UUID] = Field(default=None, description="Current agent run identity")
    progress: float = Field(default=0.0, ge=0.0, le=100.0)
    total_rows: int = Field(default=0, ge=0)
    processed_rows: int = Field(default=0, ge=0)
    successful_rows: int = Field(default=0, ge=0)
    failed_rows: int = Field(default=0, ge=0)
    skipped_rows: int = Field(default=0, ge=0)
    current_table: Optional[str] = Field(default=None)
    current_stage: Optional[str] = Field(default=None)
    error_message: Optional[str] = Field(default=None)


class AgentRunResponse(BaseModel):
    """Response model for an AgentRun execution attempt."""
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    migration_job_id: UUID
    agent_id: Optional[UUID] = None
    status: str
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    failure_reason: Optional[str] = None
    agent_version: Optional[str] = None
    execution_engine_version: Optional[str] = None
    created_at: datetime
    updated_at: datetime


class ExecutionEventResponse(BaseModel):
    """Response model for an append-only ExecutionEvent."""
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    event_id: UUID
    agent_run_id: Optional[UUID] = None
    migration_job_id: Optional[UUID] = None
    migration_plan_id: Optional[UUID] = None
    event_type: str
    actor_type: str
    actor_id: Optional[str] = None
    timestamp: datetime
    payload: Dict[str, Any]
    schema_version: int = 1


class ExecutionCheckpointCreate(BaseModel):
    """Payload to save an authoritative checkpoint for a step."""
    source_identifier: str = Field(default="default", max_length=100)
    source_table: str = Field(..., max_length=255)
    target_table: str = Field(..., max_length=255)
    cursor_offset: int = Field(default=0, ge=0)
    rows_processed: int = Field(default=0, ge=0)
    source_position: Optional[Dict[str, Any]] = None


class ExecutionCheckpointResponse(BaseModel):
    """Response model for an ExecutionCheckpoint."""
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    execution_step_id: UUID
    source_identifier: str
    source_table: str
    target_table: str
    cursor_offset: int
    rows_processed: int
    source_position: Optional[Dict[str, Any]] = None
    checkpoint_version: int
    created_at: datetime
    updated_at: datetime


class ExecutionStepResponse(BaseModel):
    """Response model for a granular MigrationExecutionStep."""
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    execution_plan_id: UUID
    step_key: str
    step_type: str
    sequence: int
    dependencies: List[str] = Field(default_factory=list)
    status: str
    attempt_count: int
    max_attempts: int
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    agent_run_id: Optional[UUID] = None
    input_definition: Dict[str, Any] = Field(default_factory=dict)
    output_summary: Dict[str, Any] = Field(default_factory=dict)
    error_type: Optional[str] = None
    error_message: Optional[str] = None
    checkpoints: List[ExecutionCheckpointResponse] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


class ExecutionPlanResponse(BaseModel):
    """Response model for a derived MigrationExecutionPlan."""
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    migration_job_id: UUID
    migration_plan_id: UUID
    migration_plan_version_id: Optional[UUID] = None
    status: str
    concurrency_limit: int
    steps: List[ExecutionStepResponse] = Field(default_factory=list)
    created_at: datetime
    finalized_at: Optional[datetime] = None


class ExecutionStepClaimRequest(BaseModel):
    """Request payload when an agent claims execution steps."""
    agent_id: UUID
    agent_run_id: UUID


class ExecutionStepCompleteRequest(BaseModel):
    """Request payload when completing a step."""
    output_summary: Dict[str, Any] = Field(default_factory=dict)


class ExecutionStepFailRequest(BaseModel):
    """Request payload when reporting failure on a step."""
    error_type: Optional[str] = None
    error_message: str


class ExecutionJobResponse(BaseModel):
    """Response model for a MigrationJob execution entity."""
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    migration_plan_id: UUID
    agent_id: Optional[UUID] = None
    current_run_id: Optional[UUID] = None
    idempotency_key: Optional[str] = None
    status: str
    is_dry_run: bool = False
    truncate_target: bool = False
    progress: float
    total_rows: int
    processed_rows: int
    successful_rows: int
    failed_rows: int
    current_table: Optional[str] = None
    current_stage: Optional[str] = None
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    error_message: Optional[str] = None
    ai_diagnosis: Optional[dict] = None
    created_at: datetime
    updated_at: datetime
    target_tables_with_existing_data: list[dict] = Field(default_factory=list)


class AgentTaskItemResponse(BaseModel):
    """Task item payload returned to Agent when polling GET /api/v1/agents/tasks."""
    job_id: UUID
    migration_plan_id: UUID
    agent_run_id: Optional[UUID] = None
    execution_plan_id: Optional[UUID] = None
    status: str
    is_dry_run: bool = False
    truncate_target: bool = False
    created_at: datetime
