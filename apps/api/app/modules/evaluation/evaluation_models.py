"""
Evaluation Domain Database Models
Phase 6: Durable evaluation suite runs and scenario result records.
Stores repeatable benchmark results, version regressions, latency/cost stats, and quality gate outcomes.
"""

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
import uuid

from sqlalchemy import (
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


class EvaluationSuiteRun(Base):
    """
    Represents an entire execution of the evaluation benchmark across all test scenarios.
    Allows comparing planner/prompt/model versions over time.
    """
    __tablename__ = "evaluation_suite_runs"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    suite_name: Mapped[str] = mapped_column(String(100), default="phase6_representative_suite", nullable=False)
    model: Mapped[str] = mapped_column(String(100), default="gpt-4o", nullable=False)
    model_version: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    prompt_version: Mapped[str] = mapped_column(String(50), default="p-migration-2026.04", nullable=False)
    planner_version: Mapped[str] = mapped_column(String(50), default="migraflow-planner-v2.0", nullable=False)
    status: Mapped[str] = mapped_column(String(50), default="pending", index=True, nullable=False)

    total_scenarios: Mapped[int] = mapped_column(Integer, default=15, nullable=False)
    passed_scenarios: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    failed_scenarios: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    pass_rate: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)

    average_planning_score: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    recovery_routing_accuracy: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    llm_output_valid_rate: Mapped[float] = mapped_column(Float, default=1.0, nullable=False)

    total_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    total_llm_calls: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    total_cost_usd: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    total_duration_ms: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)

    quality_gate_passed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    quality_gate_details: Mapped[Dict[str, Any]] = mapped_column(JSON_TYPE, default=dict, nullable=False)
    summary_report: Mapped[Dict[str, Any]] = mapped_column(JSON_TYPE, default=dict, nullable=False)

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
    scenario_results: Mapped[List["EvaluationScenarioResult"]] = relationship(
        "EvaluationScenarioResult",
        back_populates="suite_run",
        cascade="all, delete-orphan",
        order_by="EvaluationScenarioResult.scenario_id",
    )


class EvaluationScenarioResult(Base):
    """
    Granular evaluation result for a single scenario within an EvaluationSuiteRun.
    """
    __tablename__ = "evaluation_scenario_results"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    suite_run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("evaluation_suite_runs.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    scenario_id: Mapped[str] = mapped_column(String(100), index=True, nullable=False)
    scenario_name: Mapped[str] = mapped_column(String(255), nullable=False)
    scenario_category: Mapped[str] = mapped_column(String(50), default="planning", nullable=False)
    status: Mapped[str] = mapped_column(String(50), default="pending", nullable=False)
    score: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)

    planning_metrics: Mapped[Dict[str, Any]] = mapped_column(JSON_TYPE, default=dict, nullable=False)
    recovery_metrics: Mapped[Dict[str, Any]] = mapped_column(JSON_TYPE, default=dict, nullable=False)
    llm_metrics: Mapped[Dict[str, Any]] = mapped_column(JSON_TYPE, default=dict, nullable=False)
    cost_latency_metrics: Mapped[Dict[str, Any]] = mapped_column(JSON_TYPE, default=dict, nullable=False)
    failure_injection_metrics: Mapped[Dict[str, Any]] = mapped_column(JSON_TYPE, default=dict, nullable=False)

    errors: Mapped[List[str]] = mapped_column(JSON_TYPE, default=list, nullable=False)
    warnings: Mapped[List[str]] = mapped_column(JSON_TYPE, default=list, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    # Relationships
    suite_run: Mapped["EvaluationSuiteRun"] = relationship(
        "EvaluationSuiteRun", back_populates="scenario_results"
    )
