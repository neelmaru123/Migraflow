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
    ASK_USER = "ask_user"
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
    ASK_USER = "ask_user"
    VERIFYING = "verifying"
    NEEDS_REVIEW = "needs_review"
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
    ASK_USER = "ask_user"
    NEEDS_REVIEW = "needs_review"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"
    CANCELLED = "cancelled"


class ExecutionPlanLifecycle(NormalizedStrEnum):
    """
    Explicit lifecycle states for derived Migration Execution Plans.
    """
    PENDING = "pending"
    RUNNING = "running"
    PAUSED = "paused"
    ASK_USER = "ask_user"
    NEEDS_REVIEW = "needs_review"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ExecutionStepType(NormalizedStrEnum):
    """
    Extensible step types for granular migration execution.
    """
    PREFLIGHT = "preflight"
    CREATE_SCHEMA = "create_schema"
    PRE_DDL = "pre_ddl"
    EXTRACT = "extract"
    TRANSFORM = "transform"
    LOAD = "load"
    POST_DDL = "post_ddl"
    VERIFY = "verify"


class VerificationStatus(NormalizedStrEnum):
    """
    Status of an automated post-migration verification check or full suite.
    """
    PASSED = "passed"
    FAILED = "failed"
    WARNING = "warning"
    SKIPPED = "skipped"


class VerificationCheckType(NormalizedStrEnum):
    """
    Standardized verification check categories.
    """
    ROW_COUNT = "row_count"
    ROWS_PROCESSED = "rows_processed"
    FAILED_ROWS = "failed_rows"
    DUPLICATE_DETECTION = "duplicate_detection"
    PRIMARY_KEY_INTEGRITY = "primary_key_integrity"
    FOREIGN_KEY_INTEGRITY = "foreign_key_integrity"
    NULLABILITY = "nullability"
    TABLE_EXISTENCE = "table_existence"
    SCHEMA_COMPATIBILITY = "schema_compatibility"
    TRANSFORMATION_SANITY = "transformation_sanity"
    SAMPLE_DATA_COMPARISON = "sample_data_comparison"


class OperationRiskLevel(NormalizedStrEnum):
    """
    Operation risk classification levels for safety governance.
    """
    READ_ONLY = "read_only"
    WRITE = "write"
    DESTRUCTIVE = "destructive"


class FailureCategory(NormalizedStrEnum):
    """
    Centralized failure categories across the entire platform.
    """
    TRANSIENT_NETWORK = "transient_network"
    TRANSIENT_DATABASE = "transient_database"
    SOURCE_UNAVAILABLE = "source_unavailable"
    TARGET_UNAVAILABLE = "target_unavailable"
    AUTHENTICATION = "authentication"
    AUTHORIZATION = "authorization"
    SCHEMA_CHANGED = "schema_changed"
    SOURCE_SCHEMA_MISMATCH = "source_schema_mismatch"
    TARGET_SCHEMA_MISMATCH = "target_schema_mismatch"
    DATA_VALIDATION = "data_validation"
    CONSTRAINT_VIOLATION = "constraint_violation"
    TRANSFORMATION_ERROR = "transformation_error"
    RESOURCE_EXHAUSTION = "resource_exhaustion"
    TIMEOUT = "timeout"
    AGENT_CRASH = "agent_crash"
    AGENT_LOST = "agent_lost"
    PLAN_INVALID = "plan_invalid"
    PLAN_INFEASIBLE = "plan_infeasible"
    USER_CANCELLED = "user_cancelled"
    UNKNOWN = "unknown"


class FailureSeverity(NormalizedStrEnum):
    """
    Failure severity levels.
    """
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class FailureDomain(NormalizedStrEnum):
    """
    Domain boundaries separating LLM infrastructure, LLM output, DB, and execution.
    """
    LLM_INFRASTRUCTURE = "llm_infrastructure"
    LLM_OUTPUT_VALIDATION = "llm_output_validation"
    MIGRATION_EXECUTION = "migration_execution"
    DATABASE_ENGINE = "database_engine"
    SECURITY_AUTH = "security_auth"
    SYSTEM_ORCHESTRATION = "system_orchestration"


class RecoveryDecisionType(NormalizedStrEnum):
    """
    Deterministic recovery outcomes computed by RecoveryRouter.
    """
    RETRY = "retry"
    RECOVER = "recover"
    REPLAN = "replan"
    ASK_USER = "ask_user"
    FAIL = "fail"


class ExecutionEventType(str, Enum):
    """
    Durable append-only execution and system event types.
    """
    # Migration Plan Lifecycle Events
    PLAN_CREATED = "PLAN_CREATED"
    PLAN_VALIDATED = "PLAN_VALIDATED"
    PLAN_REFINED = "PLAN_REFINED"
    PLAN_APPROVED = "PLAN_APPROVED"
    PLAN_APPROVAL_REVOKED = "PLAN_APPROVAL_REVOKED"
    PLAN_SUPERSEDED = "PLAN_SUPERSEDED"

    # Execution Plan Events
    EXECUTION_PLAN_CREATED = "EXECUTION_PLAN_CREATED"
    EXECUTION_PLAN_COMPLETED = "EXECUTION_PLAN_COMPLETED"
    EXECUTION_PLAN_FAILED = "EXECUTION_PLAN_FAILED"

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
    STEP_CLAIMED = "STEP_CLAIMED"
    STEP_STARTED = "STEP_STARTED"
    STEP_COMPLETED = "STEP_COMPLETED"
    STEP_FAILED = "STEP_FAILED"
    STEP_RETRYING = "STEP_RETRYING"
    STEP_SKIPPED = "STEP_SKIPPED"
    CHECKPOINT_SAVED = "CHECKPOINT_SAVED"

    # Retry and Recovery Events
    RETRY_STARTED = "RETRY_STARTED"
    RECOVERY_STARTED = "RECOVERY_STARTED"
    REPLAN_REQUESTED = "REPLAN_REQUESTED"
    REPLAN_COMPLETED = "REPLAN_COMPLETED"
    REPLAN_FAILED = "REPLAN_FAILED"

    # User Intervention Events
    USER_INTERVENTION_REQUESTED = "USER_INTERVENTION_REQUESTED"
    USER_INTERVENTION_RESOLVED = "USER_INTERVENTION_RESOLVED"

    # Verification Events
    VERIFICATION_STARTED = "VERIFICATION_STARTED"
    VERIFICATION_CHECK_PASSED = "VERIFICATION_CHECK_PASSED"
    VERIFICATION_CHECK_FAILED = "VERIFICATION_CHECK_FAILED"
    VERIFICATION_CHECK_WARNING = "VERIFICATION_CHECK_WARNING"
    VERIFICATION_COMPLETED = "VERIFICATION_COMPLETED"

    # Safety and Destructive Approval Events
    DESTRUCTIVE_APPROVAL_REQUESTED = "DESTRUCTIVE_APPROVAL_REQUESTED"
    DESTRUCTIVE_APPROVAL_GRANTED = "DESTRUCTIVE_APPROVAL_GRANTED"
    DESTRUCTIVE_APPROVAL_REVOKED = "DESTRUCTIVE_APPROVAL_REVOKED"
    DESTRUCTIVE_APPROVAL_REJECTED = "DESTRUCTIVE_APPROVAL_REJECTED"

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
