"""
Agents Domain Database Models
"""

import hashlib
import secrets
import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING, ClassVar, List, Optional
from sqlalchemy import DateTime, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.core.db import Base

if TYPE_CHECKING:
    from app.modules.users.users_models import User
    from app.modules.sources.sources_models import DataSource
    from app.modules.migration_plans.migration_plans_models import MigrationPlan
    from app.modules.execution.execution_models import MigrationJob, AgentRun


class Agent(Base):
    __tablename__ = "agents"
    __table_args__ = (
        UniqueConstraint("user_id", "agent_identifier", name="uq_agents_user_identifier"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    agent_identifier: Mapped[str] = mapped_column(
        String(255), index=True, nullable=False
    )
    api_token_hash: Mapped[str] = mapped_column(
        String(255),
        unique=True,
        index=True,
        nullable=False,
        default=lambda: hashlib.sha256(secrets.token_urlsafe(32).encode("utf-8")).hexdigest(),
    )
    status: Mapped[str] = mapped_column(
        String(50), default="offline", nullable=False
    )
    version: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    last_seen_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    idle_since: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="Timestamp when agent last became idle (no active jobs). Set on job complete/fail, cleared on new job queued. Used to determine ENTER_IDLE_MODE / SHUTDOWN directives.",
    )
    last_error: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
        comment="Human-readable description of the fatal error that stopped or degraded the agent.",
    )
    error_category: Mapped[Optional[str]] = mapped_column(
        String(100),
        nullable=True,
        comment="Category tag for the fatal error (e.g. CONFIG_ERROR, AUTH_ERROR, RUNTIME_CRASH, DISCONNECTED_UNEXPECTEDLY).",
    )
    last_error_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="Timestamp when the fatal stopping error occurred or was recorded.",
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
    user: Mapped["User"] = relationship("User", back_populates="agents")
    data_sources: Mapped[List["DataSource"]] = relationship(
        "DataSource", back_populates="agent", cascade="all, delete-orphan"
    )
    migration_plans: Mapped[List["MigrationPlan"]] = relationship(
        "MigrationPlan", back_populates="agent"
    )
    migration_jobs: Mapped[List["MigrationJob"]] = relationship(
        "MigrationJob", back_populates="agent"
    )
    agent_runs: Mapped[List["AgentRun"]] = relationship(
        "AgentRun", back_populates="agent"
    )
