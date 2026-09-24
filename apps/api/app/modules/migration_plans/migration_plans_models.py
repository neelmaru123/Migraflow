"""
Migration Plans Domain Database Models
"""

import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, List, Optional
from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, JSON, String, desc
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.core.db import Base

JSON_TYPE = JSONB().with_variant(JSON, "sqlite")

if TYPE_CHECKING:
    from app.modules.users.users_models import User
    from app.modules.agents.agents_models import Agent
    from app.modules.metadata.metadata_models import MetadataSnapshot
    from app.modules.execution.execution_models import MigrationJob


class MigrationPlanSnapshot(Base):
    """Join table linking MigrationPlan to MetadataSnapshot (N:M relationship)."""
    __tablename__ = "migration_plan_snapshots"

    migration_plan_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("migration_plans.id", ondelete="CASCADE"),
        primary_key=True,
    )
    metadata_snapshot_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("metadata_snapshots.id", ondelete="CASCADE"),
        primary_key=True,
        index=True,
    )


class MigrationPlan(Base):
    __tablename__ = "migration_plans"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    agent_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("agents.id", ondelete="SET NULL"),
        index=True,
        nullable=True,
    )
    status: Mapped[str] = mapped_column(String(50), default="draft", nullable=False)
    plan_data: Mapped[Any] = mapped_column(JSON_TYPE, nullable=False)
    target_config: Mapped[Optional[Any]] = mapped_column(JSON_TYPE, nullable=True)
    ai_model: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    prompt_version: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    confidence_score: Mapped[float] = mapped_column(Float, default=1.0, nullable=False)
    is_valid: Mapped[Optional[bool]] = mapped_column(nullable=True, default=True)
    validation_errors: Mapped[Optional[Any]] = mapped_column(JSON_TYPE, nullable=True)
    langgraph_thread_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    approved_version_number: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    approved_by_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    approved_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
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

    # Relationships
    user: Mapped["User"] = relationship("User", back_populates="migration_plans", foreign_keys="[MigrationPlan.user_id]")
    approved_by: Mapped[Optional["User"]] = relationship("User", foreign_keys="[MigrationPlan.approved_by_user_id]")
    agent: Mapped[Optional["Agent"]] = relationship("Agent", back_populates="migration_plans")
    snapshots: Mapped[List["MetadataSnapshot"]] = relationship(
        "MetadataSnapshot", secondary="migration_plan_snapshots", back_populates="migration_plans"
    )
    jobs: Mapped[List["MigrationJob"]] = relationship(
        "MigrationJob", back_populates="plan", cascade="all, delete-orphan"
    )
    versions: Mapped[List["MigrationPlanVersion"]] = relationship(
        "MigrationPlanVersion",
        back_populates="plan",
        cascade="all, delete-orphan",
        order_by=lambda: desc(MigrationPlanVersion.version_number),
    )


class MigrationPlanVersion(Base):
    """Historical snapshot of a MigrationPlan's AST state at a specific version point."""
    __tablename__ = "migration_plan_versions"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    migration_plan_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("migration_plans.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    version_number: Mapped[int] = mapped_column(nullable=False)
    edit_type: Mapped[str] = mapped_column(String(50), nullable=False)
    user_feedback: Mapped[Optional[str]] = mapped_column(nullable=True)
    plan_data: Mapped[Any] = mapped_column(JSON_TYPE, nullable=False)
    is_valid: Mapped[Optional[bool]] = mapped_column(nullable=True, default=True)
    confidence_score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    validation_errors: Mapped[Optional[Any]] = mapped_column(JSON_TYPE, nullable=True)
    is_approved: Mapped[Optional[bool]] = mapped_column(Boolean, default=False, nullable=True)
    approved_by_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    approved_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    # Relationships
    plan: Mapped["MigrationPlan"] = relationship("MigrationPlan", back_populates="versions")


