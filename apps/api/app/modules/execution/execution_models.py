"""
Execution Domain Database Models (Jobs, Diagnostic Errors, Agent Runs, and Execution Events)
"""

import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Dict, List, Optional
from sqlalchemy import BigInteger, Boolean, DateTime, Float, ForeignKey, Integer, String, JSON, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.core.db import Base

JSON_TYPE = JSONB().with_variant(JSON, "sqlite")

if TYPE_CHECKING:
    from app.modules.migration_plans.migration_plans_models import MigrationPlan
    from app.modules.agents.agents_models import Agent


class MigrationJob(Base):
    __tablename__ = "migration_jobs"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    migration_plan_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("migration_plans.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    agent_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("agents.id", ondelete="SET NULL"),
        index=True,
        nullable=True,
    )
    current_run_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
        index=True,
    )
    idempotency_key: Mapped[Optional[str]] = mapped_column(
        String(255),
        unique=True,
        index=True,
        nullable=True,
    )
    status: Mapped[str] = mapped_column(
        String(50), default="queued", index=True, nullable=False
    )  # queued, claimed, preparing, running, paused, recovering, verifying, completed, failed, cancelled
    is_dry_run: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    truncate_target: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    progress: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    total_rows: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    processed_rows: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    successful_rows: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    failed_rows: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    current_table: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    current_stage: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    started_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    completed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    error_message: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    ai_diagnosis: Mapped[Optional[Dict[str, Any]]] = mapped_column(JSON_TYPE, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    # Relationships
    plan: Mapped["MigrationPlan"] = relationship("MigrationPlan", back_populates="jobs")
    agent: Mapped[Optional["Agent"]] = relationship("Agent", back_populates="migration_jobs")
    execution_plan: Mapped[Optional["MigrationExecutionPlan"]] = relationship(
        "MigrationExecutionPlan", back_populates="job", uselist=False, cascade="all, delete-orphan"
    )
    errors: Mapped[List["MigrationError"]] = relationship(
        "MigrationError", back_populates="job", cascade="all, delete-orphan"
    )
    runs: Mapped[List["AgentRun"]] = relationship(
        "AgentRun", back_populates="job", cascade="all, delete-orphan", order_by="AgentRun.created_at"
    )
    events: Mapped[List["ExecutionEvent"]] = relationship(
        "ExecutionEvent", back_populates="job", cascade="all, delete-orphan", order_by="ExecutionEvent.timestamp"
    )


class AgentRun(Base):
    """
    First-class execution/run identity.
    A single MigrationJob may have multiple AgentRuns due to crashes, restarts,
    recovery attempts, or agent reassignments.
    """
    __tablename__ = "agent_runs"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    migration_job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("migration_jobs.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    agent_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("agents.id", ondelete="SET NULL"),
        index=True,
        nullable=True,
    )
    status: Mapped[str] = mapped_column(
        String(50), default="preparing", index=True, nullable=False
    )
    started_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    finished_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    failure_reason: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    agent_version: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    execution_engine_version: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    # Relationships
    job: Mapped["MigrationJob"] = relationship("MigrationJob", back_populates="runs")
    agent: Mapped[Optional["Agent"]] = relationship("Agent", back_populates="agent_runs")
    events: Mapped[List["ExecutionEvent"]] = relationship(
        "ExecutionEvent", back_populates="agent_run"
    )
    steps: Mapped[List["MigrationExecutionStep"]] = relationship(
        "MigrationExecutionStep", back_populates="agent_run"
    )


class MigrationExecutionPlan(Base):
    """
    Durable execution representation derived from an approved MigrationPlan.
    Maintains the state, concurrency limits, and step execution graph for a job.
    """
    __tablename__ = "migration_execution_plans"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    migration_job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("migration_jobs.id", ondelete="CASCADE"),
        unique=True,
        index=True,
        nullable=False,
    )
    migration_plan_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("migration_plans.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    migration_plan_version_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
        index=True,
    )
    status: Mapped[str] = mapped_column(
        String(50), default="pending", index=True, nullable=False
    )  # pending, running, paused, completed, failed, cancelled
    concurrency_limit: Mapped[int] = mapped_column(
        Integer, default=2, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    finalized_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Relationships
    job: Mapped["MigrationJob"] = relationship("MigrationJob", back_populates="execution_plan")
    plan: Mapped["MigrationPlan"] = relationship("MigrationPlan")
    steps: Mapped[List["MigrationExecutionStep"]] = relationship(
        "MigrationExecutionStep",
        back_populates="execution_plan",
        cascade="all, delete-orphan",
        order_by="MigrationExecutionStep.sequence",
    )


class MigrationExecutionStep(Base):
    """
    Granular execution step within a MigrationExecutionPlan.
    Tracks state, dependencies, retry attempts, agent run ownership, and execution results.
    """
    __tablename__ = "migration_execution_steps"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    execution_plan_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("migration_execution_plans.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    step_key: Mapped[str] = mapped_column(
        String(100), index=True, nullable=False
    )
    step_type: Mapped[str] = mapped_column(
        String(50), index=True, nullable=False
    )  # preflight, create_schema, pre_ddl, extract, transform, load, post_ddl, verify
    sequence: Mapped[int] = mapped_column(
        Integer, default=0, nullable=False
    )
    dependencies: Mapped[List[str]] = mapped_column(
        JSON_TYPE, default=list, nullable=False
    )
    status: Mapped[str] = mapped_column(
        String(50), default="pending", index=True, nullable=False
    )  # pending, running, retrying, completed, failed, skipped, cancelled
    attempt_count: Mapped[int] = mapped_column(
        Integer, default=0, nullable=False
    )
    max_attempts: Mapped[int] = mapped_column(
        Integer, default=3, nullable=False
    )
    started_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    finished_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    agent_run_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("agent_runs.id", ondelete="SET NULL"),
        index=True,
        nullable=True,
    )
    input_definition: Mapped[Dict[str, Any]] = mapped_column(
        JSON_TYPE, default=dict, nullable=False
    )
    output_summary: Mapped[Dict[str, Any]] = mapped_column(
        JSON_TYPE, default=dict, nullable=False
    )
    error_type: Mapped[Optional[str]] = mapped_column(
        String(100), nullable=True
    )
    error_message: Mapped[Optional[str]] = mapped_column(
        String, nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    __table_args__ = (
        UniqueConstraint("execution_plan_id", "step_key", name="uq_execution_steps_plan_step_key"),
    )

    # Relationships
    execution_plan: Mapped["MigrationExecutionPlan"] = relationship(
        "MigrationExecutionPlan", back_populates="steps"
    )
    agent_run: Mapped[Optional["AgentRun"]] = relationship(
        "AgentRun", back_populates="steps"
    )
    checkpoints: Mapped[List["ExecutionCheckpoint"]] = relationship(
        "ExecutionCheckpoint", back_populates="step", cascade="all, delete-orphan"
    )


class ExecutionCheckpoint(Base):
    """
    Authoritative durable checkpoint persisted in control plane database.
    Ensures safe, bounded resume across container destruction, agent reassignment, or API restarts.
    """
    __tablename__ = "execution_checkpoints"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    execution_step_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("migration_execution_steps.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    source_identifier: Mapped[str] = mapped_column(
        String(100), default="default", nullable=False
    )
    source_table: Mapped[str] = mapped_column(
        String(255), nullable=False
    )
    target_table: Mapped[str] = mapped_column(
        String(255), nullable=False
    )
    cursor_offset: Mapped[int] = mapped_column(
        BigInteger, default=0, nullable=False
    )
    rows_processed: Mapped[int] = mapped_column(
        BigInteger, default=0, nullable=False
    )
    source_position: Mapped[Optional[Dict[str, Any]]] = mapped_column(
        JSON_TYPE, nullable=True
    )
    checkpoint_version: Mapped[int] = mapped_column(
        Integer, default=1, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    __table_args__ = (
        UniqueConstraint(
            "execution_step_id",
            "source_identifier",
            "source_table",
            "target_table",
            name="uq_checkpoint_step_source_target",
        ),
    )

    # Relationships
    step: Mapped["MigrationExecutionStep"] = relationship(
        "MigrationExecutionStep", back_populates="checkpoints"
    )


class ExecutionEvent(Base):
    """
    Durable append-only audit trail and event stream for migration execution.
    The operational state tables remain authoritative; events provide historical tracing and debugging.
    """
    __tablename__ = "execution_events"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    event_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), default=uuid.uuid4, unique=True, index=True, nullable=False
    )
    agent_run_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("agent_runs.id", ondelete="SET NULL"),
        index=True,
        nullable=True,
    )
    migration_job_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("migration_jobs.id", ondelete="CASCADE"),
        index=True,
        nullable=True,
    )
    migration_plan_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("migration_plans.id", ondelete="CASCADE"),
        index=True,
        nullable=True,
    )
    event_type: Mapped[str] = mapped_column(String(100), index=True, nullable=False)
    actor_type: Mapped[str] = mapped_column(String(50), default="system", nullable=False)
    actor_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        index=True,
        nullable=False,
    )
    payload: Mapped[Dict[str, Any]] = mapped_column(JSON_TYPE, default=dict, nullable=False)
    schema_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)

    # Relationships
    job: Mapped[Optional["MigrationJob"]] = relationship("MigrationJob", back_populates="events")
    agent_run: Mapped[Optional["AgentRun"]] = relationship("AgentRun", back_populates="events")
    plan: Mapped[Optional["MigrationPlan"]] = relationship("MigrationPlan")


class MigrationError(Base):
    __tablename__ = "migration_errors"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    migration_job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("migration_jobs.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    source_table: Mapped[str] = mapped_column(String(255), nullable=False)
    source_row_identifier: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    error_type: Mapped[str] = mapped_column(String(100), nullable=False)
    error_message: Mapped[str] = mapped_column(String, nullable=False)
    raw_data: Mapped[Optional[Dict[str, Any]]] = mapped_column(JSON_TYPE, nullable=True)
    ai_suggestion: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    status: Mapped[str] = mapped_column(
        String(50), default="unresolved", nullable=False
    )  # unresolved, resolved, ignored, retrying
    retry_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    # Relationships
    job: Mapped["MigrationJob"] = relationship("MigrationJob", back_populates="errors")
