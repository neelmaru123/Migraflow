"""
Execution Domain Database Models (Jobs and Diagnostic Errors)
"""

import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Dict, List, Optional
from sqlalchemy import BigInteger, Boolean, DateTime, Float, ForeignKey, Integer, String, JSON
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
    status: Mapped[str] = mapped_column(
        String(50), default="queued", index=True, nullable=False
    )  # queued, preparing, running, paused, completed, failed, cancelled, dry_run_completed
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
    errors: Mapped[List["MigrationError"]] = relationship(
        "MigrationError", back_populates="job", cascade="all, delete-orphan"
    )


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
