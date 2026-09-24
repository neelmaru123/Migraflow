"""
Observability Domain Database Models
Provides:
1. Hierarchical Execution Traces (AgentRun -> ExecutionStep -> ToolRun / LLMRun) with OpenTelemetry compatibility.
2. Comprehensive LLM Observability & Call Records (model, provider, prompt_version, token usage, cost, latency).
3. Deterministic Resource Budgets (limits and live usage tracking per migration job).
"""

from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Dict, List, Optional
import uuid

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    JSON,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base

JSON_TYPE = JSONB().with_variant(JSON, "sqlite")

if TYPE_CHECKING:
    from app.modules.execution.execution_models import (
        AgentRun,
        MigrationExecutionStep,
        MigrationJob,
    )
    from app.modules.migration_plans.migration_plans_models import MigrationPlan
    from app.modules.users.users_models import User


class ExecutionTrace(Base):
    """
    Hierarchical execution trace representing spans within the migration lifecycle:
    AgentRun -> ExecutionStep -> ToolRun / LLMRun / Verification.
    Designed for seamless future export to OpenTelemetry Spans.
    """
    __tablename__ = "execution_traces"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    trace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), index=True, nullable=False
    )
    parent_run_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("execution_traces.id", ondelete="CASCADE"),
        index=True,
        nullable=True,
    )
    migration_job_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("migration_jobs.id", ondelete="CASCADE"),
        index=True,
        nullable=True,
    )
    agent_run_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("agent_runs.id", ondelete="SET NULL"),
        index=True,
        nullable=True,
    )
    execution_step_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("migration_execution_steps.id", ondelete="SET NULL"),
        index=True,
        nullable=True,
    )
    operation_type: Mapped[str] = mapped_column(
        String(50), index=True, nullable=False
    )  # agent_run, execution_step, tool_run, llm_run, verification, etl_chunk, ddl_execution
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(
        String(50), default="running", index=True, nullable=False
    )  # running, completed, failed, cancelled, timed_out
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    finished_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    duration_ms: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    error_type: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    metadata_snapshot: Mapped[Dict[str, Any]] = mapped_column(
        JSON_TYPE, default=dict, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    # Hierarchical tree relationships
    parent: Mapped[Optional["ExecutionTrace"]] = relationship(
        "ExecutionTrace", remote_side=[id], back_populates="children"
    )
    children: Mapped[List["ExecutionTrace"]] = relationship(
        "ExecutionTrace", back_populates="parent", cascade="all, delete-orphan"
    )
    llm_calls: Mapped[List["LLMCallRecord"]] = relationship(
        "LLMCallRecord", back_populates="trace", cascade="all, delete-orphan"
    )

    @property
    def error(self) -> Optional[str]:
        return self.error_message

    @property
    def trace_metadata(self) -> Dict[str, Any]:
        return self.metadata_snapshot

    def to_otel_span(self) -> Dict[str, Any]:
        """Converts the internal trace span to an OpenTelemetry-compatible span payload."""
        return {
            "name": self.name,
            "context": {
                "trace_id": str(self.trace_id),
                "span_id": str(self.id),
            },
            "parent_id": str(self.parent_run_id) if self.parent_run_id else None,
            "start_time": self.started_at.isoformat() if self.started_at else None,
            "end_time": self.finished_at.isoformat() if self.finished_at else None,
            "attributes": {
                "operation_type": self.operation_type,
                "status": self.status,
                "duration_ms": self.duration_ms,
                "migration_job_id": str(self.migration_job_id) if self.migration_job_id else None,
                "agent_run_id": str(self.agent_run_id) if self.agent_run_id else None,
                "execution_step_id": str(self.execution_step_id) if self.execution_step_id else None,
                **self.metadata_snapshot,
            },
            "status": {
                "code": "ERROR" if self.status in ("failed", "timed_out") else "OK",
                "message": self.error_message,
            },
        }


class LLMCallRecord(Base):
    """
    Granular record of an LLM invocation capturing provenance, latency, token usage,
    estimated cost, and schema validation outcomes without leaking secrets or raw database rows.
    """
    __tablename__ = "llm_call_records"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    trace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), index=True, nullable=False
    )
    execution_trace_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("execution_traces.id", ondelete="SET NULL"),
        index=True,
        nullable=True,
    )
    migration_plan_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("migration_plans.id", ondelete="SET NULL"),
        index=True,
        nullable=True,
    )
    migration_job_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("migration_jobs.id", ondelete="SET NULL"),
        index=True,
        nullable=True,
    )
    model: Mapped[str] = mapped_column(String(100), nullable=False)
    provider: Mapped[str] = mapped_column(String(50), nullable=False)  # openai, google, anthropic, local
    model_version: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    prompt_version: Mapped[str] = mapped_column(String(50), nullable=False)
    planner_version: Mapped[str] = mapped_column(String(50), default="migraflow-planner-v2.0", nullable=False)
    schema_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    request_timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    latency_ms: Mapped[float] = mapped_column(Float, nullable=False)
    prompt_tokens: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    completion_tokens: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    total_tokens: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    estimated_cost_usd: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    status: Mapped[str] = mapped_column(
        String(50), default="success", index=True, nullable=False
    )  # success, failed, timeout
    structured_output_valid: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    validation_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    sanitized_prompt_preview: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    # Relationships
    trace: Mapped[Optional["ExecutionTrace"]] = relationship(
        "ExecutionTrace", back_populates="llm_calls"
    )

    @property
    def is_success(self) -> bool:
        return self.status == "success"

    @property
    def prompt_preview(self) -> Optional[str]:
        return self.sanitized_prompt_preview



class ResourceBudget(Base):
    """
    Deterministic resource budget governing a MigrationJob.
    Enforces hard limits on LLM calls, replans, retries, duration, tokens, and cost.
    """
    __tablename__ = "resource_budgets"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    migration_job_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("migration_jobs.id", ondelete="CASCADE"),
        unique=True,
        index=True,
        nullable=True,
    )
    user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        index=True,
        nullable=True,
    )
    # Configured limits
    max_llm_calls: Mapped[int] = mapped_column(Integer, default=10, nullable=False)
    max_replans: Mapped[int] = mapped_column(Integer, default=3, nullable=False)
    max_retries: Mapped[int] = mapped_column(Integer, default=3, nullable=False)
    max_execution_duration_seconds: Mapped[int] = mapped_column(Integer, default=3600, nullable=False)
    max_concurrent_steps: Mapped[int] = mapped_column(Integer, default=4, nullable=False)
    max_tokens: Mapped[int] = mapped_column(BigInteger, default=100000, nullable=False)
    max_cost_usd: Mapped[float] = mapped_column(Float, default=5.00, nullable=False)

    # Current consumed usage
    current_llm_calls: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    current_replans: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    current_retries: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    current_tokens: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    current_cost_usd: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)

    # Limit status
    is_exceeded: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    exceeded_limit_type: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)

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
