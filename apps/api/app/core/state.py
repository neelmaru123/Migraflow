"""
Centralized State Machine Definitions and Event Constants
Defines explicit lifecycles for Agents, Migration Plans, Execution Jobs, and Execution Steps.
"""

from enum import Enum
from typing import Any, Dict, Optional, Set, Union


class NormalizedStrEnum(str, Enum):
    """
    String Enum that supports case-insensitive lookup, comparison, and coercion.
    Ensures state strings are normalized and unified across all subsystems.
    """

    @classmethod
    def from_str(cls, value: Union[str, "NormalizedStrEnum"]) -> "NormalizedStrEnum":
        if isinstance(value, cls):
            return value
        val_str = str(value).strip().lower()
        for member in cls:
            if member.value.lower() == val_str or member.name.lower() == val_str:
                return member
        valid_values = [m.value for m in cls]
        raise ValueError(
            f"'{value}' is not a valid {cls.__name__}. Valid options: {valid_values}"
        )

    @classmethod
    def has_value(cls, value: Any) -> bool:
        try:
            cls.from_str(value)
            return True
        except (ValueError, TypeError, AttributeError):
            return False

    def __eq__(self, other: Any) -> bool:
        if isinstance(other, str):
            other_lower = other.strip().lower()
            if self.value.lower() == other_lower or self.name.lower() == other_lower:
                return True
            if self.value == "completed" and other_lower == "dry_run_completed":
                return True
            return False
        if isinstance(other, Enum):
            return self.value.lower() == str(other.value).strip().lower()
        return super().__eq__(other)

    def __hash__(self) -> int:
        return hash(self.value.lower())

    def __str__(self) -> str:
        return self.value


class AgentLifecycle(NormalizedStrEnum):
    """
    Explicit lifecycle states for Docker Execution Agents.
    """
    REGISTERING = "registering"
    ONLINE = "online"
    BUSY = "busy"
    DEGRADED = "degraded"
    ERROR = "error"
    OFFLINE = "offline"


class MigrationPlanLifecycle(NormalizedStrEnum):
    """
    Explicit lifecycle states for Migration Plans.
    """
    DRAFT = "draft"
    GENERATING = "generating"
    VALIDATING = "validating"
    AWAITING_APPROVAL = "awaiting_approval"
    REFINING = "refining"
    APPROVED = "approved"
    RUNNING = "running"
    COMPLETED = "completed"
    SUPERSEDED = "superseded"
    INVALID = "invalid"


class ExecutionLifecycle(NormalizedStrEnum):
    """
    Explicit lifecycle states for Migration Execution Jobs.
    """
    QUEUED = "queued"
    CLAIMED = "claimed"
    PREPARING = "preparing"
    RUNNING = "running"
    PAUSED = "paused"
    RECOVERING = "recovering"
    VERIFYING = "verifying"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ExecutionStepLifecycle(NormalizedStrEnum):
    """
    Explicit lifecycle states for individual Execution Steps.
    """
    PENDING = "pending"
    RUNNING = "running"
    RETRYING = "retrying"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"
    CANCELLED = "cancelled"


class ExecutionEventType(str, Enum):
    """
    Durable append-only execution and system event types.
    """
    # Migration Plan Lifecycle Events
    PLAN_CREATED = "PLAN_CREATED"
    PLAN_VALIDATED = "PLAN_VALIDATED"
    PLAN_REFINED = "PLAN_REFINED"
    PLAN_APPROVED = "PLAN_APPROVED"
    PLAN_SUPERSEDED = "PLAN_SUPERSEDED"

    # Execution Job Lifecycle Events
    JOB_CREATED = "JOB_CREATED"
    JOB_CLAIMED = "JOB_CLAIMED"
    JOB_STARTED = "JOB_STARTED"
    JOB_PAUSED = "JOB_PAUSED"
    JOB_RESUMED = "JOB_RESUMED"
    JOB_CANCEL_REQUESTED = "JOB_CANCEL_REQUESTED"
    JOB_CANCELLED = "JOB_CANCELLED"
    JOB_COMPLETED = "JOB_COMPLETED"
    JOB_FAILED = "JOB_FAILED"

    # Step Execution Events
    STEP_STARTED = "STEP_STARTED"
    STEP_COMPLETED = "STEP_COMPLETED"
    STEP_FAILED = "STEP_FAILED"

    # Retry and Recovery Events
    RETRY_STARTED = "RETRY_STARTED"
    RECOVERY_STARTED = "RECOVERY_STARTED"
    REPLAN_REQUESTED = "REPLAN_REQUESTED"

    # Verification Events
    VERIFICATION_STARTED = "VERIFICATION_STARTED"
    VERIFICATION_FAILED = "VERIFICATION_FAILED"
    VERIFICATION_COMPLETED = "VERIFICATION_COMPLETED"

    # Agent Lifecycle Events
    AGENT_REGISTERED = "AGENT_REGISTERED"
    AGENT_CONNECTED = "AGENT_CONNECTED"
    AGENT_DISCONNECTED = "AGENT_DISCONNECTED"
    AGENT_STATUS_CHANGED = "AGENT_STATUS_CHANGED"
    AGENT_HEARTBEAT = "AGENT_HEARTBEAT"


class InvalidStateTransitionError(ValueError):
    """
    Domain exception raised when an invalid or illegal state transition is attempted.
    """
    def __init__(
        self,
        from_state: Any,
        to_state: Any,
        entity_type: str = "ExecutionJob",
        reason: Optional[str] = None,
    ):
        self.from_state = from_state
        self.to_state = to_state
        self.entity_type = entity_type
        self.reason = reason
        message = (
            f"Invalid state transition for {entity_type}: "
            f"cannot transition from '{from_state}' to '{to_state}'."
        )
        if reason:
            message += f" Reason: {reason}"
        super().__init__(message)
