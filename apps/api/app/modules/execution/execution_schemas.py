"""
Execution Domain Pydantic Schemas
"""

from datetime import datetime
from typing import Optional
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field


class ExecutionStartRequest(BaseModel):
    """Request payload to initiate execution for an approved MigrationPlan."""
    chunk_size: Optional[int] = Field(default=50000, ge=1000, le=500000, description="Rows per ETL chunk batch")
    is_dry_run: bool = Field(default=False, description="Simulate migration without executing DDL or writing to target DB")
    truncate_target: bool = Field(default=False, description="Clean wipe/drop all existing tables in target database before executing DDL and migration")


class ExecutionCancelRequest(BaseModel):
    """Optional request payload when cancelling/resetting an active execution job."""
    reason: Optional[str] = Field(default=None, description="Optional cancellation reason or explanation")


class ExecutionProgressUpdate(BaseModel):
    """Progress metrics payload sent periodically by Docker Agent."""
    status: str = Field(..., examples=["running", "completed", "failed", "ddl_executing", "dry_run_completed"])
    progress: float = Field(default=0.0, ge=0.0, le=100.0)
    total_rows: int = Field(default=0, ge=0)
    processed_rows: int = Field(default=0, ge=0)
    successful_rows: int = Field(default=0, ge=0)
    failed_rows: int = Field(default=0, ge=0)
    skipped_rows: int = Field(default=0, ge=0)
    current_table: Optional[str] = Field(default=None)
    current_stage: Optional[str] = Field(default=None)
    error_message: Optional[str] = Field(default=None)


class ExecutionJobResponse(BaseModel):
    """Response model for a MigrationJob execution entity."""
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    migration_plan_id: UUID
    agent_id: Optional[UUID] = None
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
    status: str
    is_dry_run: bool = False
    truncate_target: bool = False
    created_at: datetime
