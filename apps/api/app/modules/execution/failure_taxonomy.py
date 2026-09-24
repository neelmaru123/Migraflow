"""
Centralized Failure Taxonomy and Classification Engine.
Defines unified error classifications, severity levels, error domains, and deterministic
rule-based failure parsing for LLM infrastructure, LLM outputs, database operations, and agent execution.
"""

from datetime import datetime, timezone
import re
from typing import Any, Dict, List, Optional, Type, Union
from pydantic import BaseModel, ConfigDict, Field

from app.core.state import (
    FailureCategory,
    FailureDomain,
    FailureSeverity,
    RecoveryDecisionType,
)


class ClassifiedFailure(BaseModel):
    """
    Structured, standardized failure representation containing actionable metadata.
    Avoids ad-hoc string matching across control-plane and agent components.
    """
    model_config = ConfigDict(from_attributes=True)

    category: FailureCategory
    code: str
    message: str
    retryable: bool
    recoverable: bool
    requires_replan: bool
    requires_user: bool
    severity: FailureSeverity
    domain: FailureDomain
    context: Dict[str, Any] = Field(default_factory=dict)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "category": self.category.value,
            "code": self.code,
            "message": self.message,
            "retryable": self.retryable,
            "recoverable": self.recoverable,
            "requires_replan": self.requires_replan,
            "requires_user": self.requires_user,
            "severity": self.severity.value,
            "domain": self.domain.value,
            "context": self.context,
            "timestamp": self.timestamp.isoformat(),
        }


class RecoveryDecision(BaseModel):
    """
    Deterministic decision outcome computed by RecoveryRouter.
    """
    model_config = ConfigDict(from_attributes=True)

    action: RecoveryDecisionType
    classified_failure: ClassifiedFailure
    delay_seconds: float = 0.0
    replan_context: Optional[Dict[str, Any]] = None
    user_prompt: Optional[Dict[str, Any]] = None
    reason: str


class FailureClassifier:
    """
    Deterministic rule-based failure classifier.
    Separates LLM infrastructure errors != LLM output validation errors != migration execution errors != database engine errors.
    """

    @classmethod
    def classify_exception(
        cls,
        exc: Exception,
        context: Optional[Dict[str, Any]] = None,
    ) -> ClassifiedFailure:
        """Classifies a Python exception instance into a structured ClassifiedFailure."""
        ctx = dict(context or {})
        exc_type = type(exc).__name__
        exc_msg = str(exc)
        ctx["exception_type"] = exc_type

        # 1. LLM Infrastructure Errors
        if cls._is_llm_infra_error(exc, exc_type, exc_msg):
            return cls._classify_llm_infra(exc, exc_type, exc_msg, ctx)

        # 2. LLM Output Validation Errors (e.g. invalid AST, unparseable JSON)
        if cls._is_llm_output_error(exc, exc_type, exc_msg, ctx):
            return cls._classify_llm_output(exc, exc_type, exc_msg, ctx)

        # 3. Security / Authentication / Authorization Errors
        if cls._is_auth_error(exc, exc_type, exc_msg):
            return cls._classify_auth(exc, exc_type, exc_msg, ctx)

        # 4. Schema Mismatch / Structural Drift
        if cls._is_schema_error(exc, exc_type, exc_msg):
            return cls._classify_schema(exc, exc_type, exc_msg, ctx)

        # 5. Database Constraint / Data Type Violations
        if cls._is_constraint_error(exc, exc_type, exc_msg):
            return cls._classify_constraint(exc, exc_type, exc_msg, ctx)

        # 6. Transient Database / Lock / Network / Timeout Errors
        if cls._is_network_or_timeout_error(exc, exc_type, exc_msg):
            return cls._classify_network_or_timeout(exc, exc_type, exc_msg, ctx)

        # 7. Resource Exhaustion
        if cls._is_resource_exhaustion(exc, exc_type, exc_msg):
            return cls._classify_resource_exhaustion(exc, exc_type, exc_msg, ctx)

        # 8. Agent Lost / Crash
        if cls._is_agent_crash(exc, exc_type, exc_msg):
            return cls._classify_agent_crash(exc, exc_type, exc_msg, ctx)

        # 9. User Cancelled
        if "cancel" in exc_type.lower() or "cancelled" in exc_msg.lower():
            return ClassifiedFailure(
                category=FailureCategory.USER_CANCELLED,
                code="ERR_USER_CANCELLED",
                message=exc_msg or "Execution was cancelled by user.",
                retryable=False,
                recoverable=False,
                requires_replan=False,
                requires_user=False,
                severity=FailureSeverity.LOW,
                domain=FailureDomain.SYSTEM_ORCHESTRATION,
                context=ctx,
            )

        # Fallback to UNKNOWN
        return ClassifiedFailure(
            category=FailureCategory.UNKNOWN,
            code="ERR_UNKNOWN_EXECUTION_FAILURE",
            message=exc_msg or f"Unhandled {exc_type} error.",
            retryable=False,
            recoverable=False,
            requires_replan=False,
            requires_user=False,
            severity=FailureSeverity.HIGH,
            domain=FailureDomain.MIGRATION_EXECUTION,
            context=ctx,
        )

    @classmethod
    def classify_error_payload(
        cls,
        error_type: Optional[str],
        error_message: str,
        context: Optional[Dict[str, Any]] = None,
    ) -> ClassifiedFailure:
        """Classifies an error reported as error_type and error_message strings."""
        ctx = dict(context or {})
        err_type_str = str(error_type or "UnknownError").strip()
        err_msg = str(error_message or "").strip()
        ctx["reported_error_type"] = err_type_str

        # Synthetic exception for unified routing
        class SyntheticError(Exception):
            pass
        SyntheticError.__name__ = err_type_str

        return cls.classify_exception(SyntheticError(err_msg), context=ctx)

    # =========================================================================
    # Heuristic Discriminators
    # =========================================================================

    @classmethod
    def _is_llm_infra_error(cls, exc: Exception, exc_type: str, msg: str) -> bool:
        t_lower = exc_type.lower()
        m_lower = msg.lower()
        llm_infra_types = ["ratelimiterror", "apiconnectionerror", "apitimeouterror", "serviceunavailableerror", "resourceexhausted"]
        if any(k in t_lower for k in llm_infra_types):
            return True
        if "openai" in t_lower or "anthropic" in t_lower or "google.api" in t_lower:
            if "rate limit" in m_lower or "503" in m_lower or "504" in m_lower or "quota exceeded" in m_lower:
                return True
        return False

    @classmethod
    def _classify_llm_infra(cls, exc: Exception, exc_type: str, msg: str, ctx: Dict[str, Any]) -> ClassifiedFailure:
        m_lower = msg.lower()
        if "rate limit" in m_lower or "quota" in m_lower or "429" in m_lower:
            return ClassifiedFailure(
                category=FailureCategory.RESOURCE_EXHAUSTION,
                code="ERR_LLM_RATE_LIMIT",
                message="LLM API rate limit or quota exceeded. Retry with backoff.",
                retryable=True,
                recoverable=False,
                requires_replan=False,
                requires_user=False,
                severity=FailureSeverity.MEDIUM,
                domain=FailureDomain.LLM_INFRASTRUCTURE,
                context=ctx,
            )
        return ClassifiedFailure(
            category=FailureCategory.TRANSIENT_NETWORK,
            code="ERR_LLM_SERVICE_UNAVAILABLE",
            message=f"LLM infrastructure connection failure: {msg}",
            retryable=True,
            recoverable=False,
            requires_replan=False,
            requires_user=False,
            severity=FailureSeverity.HIGH,
            domain=FailureDomain.LLM_INFRASTRUCTURE,
            context=ctx,
        )

    @classmethod
    def _is_llm_output_error(cls, exc: Exception, exc_type: str, msg: str, ctx: Dict[str, Any]) -> bool:
        t_lower = exc_type.lower()
        m_lower = msg.lower()
        if ctx.get("domain") == "llm_output":
            return True
        output_err_types = ["outputparserexception", "jsondecodeerror", "validationerror"]
        if any(k in t_lower for k in output_err_types) and ("ast" in m_lower or "plan" in m_lower or "transformation" in m_lower or "schema" in m_lower):
            return True
        if "invalid plan ast" in m_lower or "missing required field in plan" in m_lower or "plan infeasible" in m_lower:
            return True
        return False

    @classmethod
    def _classify_llm_output(cls, exc: Exception, exc_type: str, msg: str, ctx: Dict[str, Any]) -> ClassifiedFailure:
        if "infeasible" in msg.lower():
            return ClassifiedFailure(
                category=FailureCategory.PLAN_INFEASIBLE,
                code="ERR_LLM_PLAN_INFEASIBLE",
                message=f"Plan generated by LLM was determined to be structurally infeasible: {msg}",
                retryable=False,
                recoverable=False,
                requires_replan=True,
                requires_user=True,
                severity=FailureSeverity.HIGH,
                domain=FailureDomain.LLM_OUTPUT_VALIDATION,
                context=ctx,
            )
        return ClassifiedFailure(
            category=FailureCategory.PLAN_INVALID,
            code="ERR_LLM_INVALID_AST",
            message=f"LLM generated invalid AST blueprint: {msg}",
            retryable=False,
            recoverable=False,
            requires_replan=True,
            requires_user=False,
            severity=FailureSeverity.MEDIUM,
            domain=FailureDomain.LLM_OUTPUT_VALIDATION,
            context=ctx,
        )

    @classmethod
    def _is_auth_error(cls, exc: Exception, exc_type: str, msg: str) -> bool:
        t_lower = exc_type.lower()
        m_lower = msg.lower()
        auth_keywords = ["authenticationerror", "permissionerror", "invalidpassword", "accessdenied", "access denied", "password authentication failed", "auth_failed", "unauthorized", "401", "403"]
        return any(k in t_lower or k in m_lower for k in auth_keywords)

    @classmethod
    def _classify_auth(cls, exc: Exception, exc_type: str, msg: str, ctx: Dict[str, Any]) -> ClassifiedFailure:
        is_authz = "permission" in msg.lower() or "access denied" in msg.lower() or "403" in msg.lower()
        category = FailureCategory.AUTHORIZATION if is_authz else FailureCategory.AUTHENTICATION
        code = "ERR_DB_AUTHORIZATION_DENIED" if is_authz else "ERR_DB_AUTHENTICATION_FAILED"
        return ClassifiedFailure(
            category=category,
            code=code,
            message=f"Database authentication or permission denied: {msg}",
            retryable=False,
            recoverable=False,
            requires_replan=False,
            requires_user=True,
            severity=FailureSeverity.CRITICAL,
            domain=FailureDomain.SECURITY_AUTH,
            context=ctx,
        )

    @classmethod
    def _is_schema_error(cls, exc: Exception, exc_type: str, msg: str) -> bool:
        m_lower = msg.lower()
        t_lower = exc_type.lower()
        schema_patterns = [
            "42p01", "42703", "undefinedtable", "undefinedcolumn",
            "table does not exist", "column does not exist", "unknown table", "unknown column",
            "schema_mismatch", "schema drift", "source column dropped", "no such table", "no such column",
            "invalid column name", "has no column", "does not have a column", "undefined table", "undefined column"
        ]
        if any(p in m_lower or p in t_lower for p in schema_patterns):
            return True
        if "does not exist" in m_lower and any(k in m_lower for k in ("column", "table", "relation", "schema")):
            return True
        if "not found" in m_lower and any(k in m_lower for k in ("column", "table", "schema", "relation")):
            return True
        return False

    @classmethod
    def _classify_schema(cls, exc: Exception, exc_type: str, msg: str, ctx: Dict[str, Any]) -> ClassifiedFailure:
        m_lower = msg.lower()
        if "source" in m_lower:
            category = FailureCategory.SOURCE_SCHEMA_MISMATCH
            code = "ERR_SOURCE_SCHEMA_MISMATCH"
        elif "target" in m_lower:
            category = FailureCategory.TARGET_SCHEMA_MISMATCH
            code = "ERR_TARGET_SCHEMA_MISMATCH"
        else:
            category = FailureCategory.SCHEMA_CHANGED
            code = "ERR_SCHEMA_DRIFT_DETECTED"

        return ClassifiedFailure(
            category=category,
            code=code,
            message=f"Database schema discrepancy detected: {msg}",
            retryable=False,
            recoverable=False,
            requires_replan=True,
            requires_user=False,
            severity=FailureSeverity.HIGH,
            domain=FailureDomain.MIGRATION_EXECUTION,
            context=ctx,
        )

    @classmethod
    def _is_constraint_error(cls, exc: Exception, exc_type: str, msg: str) -> bool:
        m_lower = msg.lower()
        t_lower = exc_type.lower()
        constraint_patterns = [
            "23505", "23503", "23502", "22001", "22p02",
            "uniqueviolation", "foreignkeyviolation", "notnullviolation", "integrityerror",
            "duplicate key", "violates foreign key", "null value in column", "string data right truncation",
            "invalid input syntax for type", "conversion failed", "type mismatch"
        ]
        return any(p in m_lower or p in t_lower for p in constraint_patterns)

    @classmethod
    def _classify_constraint(cls, exc: Exception, exc_type: str, msg: str, ctx: Dict[str, Any]) -> ClassifiedFailure:
        m_lower = msg.lower()
        if "syntax for type" in m_lower or "conversion failed" in m_lower or "type mismatch" in m_lower:
            category = FailureCategory.TRANSFORMATION_ERROR
            code = "ERR_DATA_TRANSFORMATION_TYPE_CAST"
            requires_replan = True
        elif "null value" in m_lower or "notnullviolation" in m_lower or "23502" in m_lower:
            category = FailureCategory.DATA_VALIDATION
            code = "ERR_NOT_NULL_CONSTRAINT"
            requires_replan = True
        else:
            category = FailureCategory.CONSTRAINT_VIOLATION
            code = "ERR_TARGET_CONSTRAINT_VIOLATION"
            requires_replan = True

        return ClassifiedFailure(
            category=category,
            code=code,
            message=f"Target constraint or data validation failed: {msg}",
            retryable=False,
            recoverable=False,
            requires_replan=requires_replan,
            requires_user=False,
            severity=FailureSeverity.HIGH,
            domain=FailureDomain.DATABASE_ENGINE,
            context=ctx,
        )

    @classmethod
    def _is_network_or_timeout_error(cls, exc: Exception, exc_type: str, msg: str) -> bool:
        m_lower = msg.lower()
        t_lower = exc_type.lower()
        net_patterns = [
            "timeouterror", "connectionerror", "connectionrefused", "connecttimeout", "readtimeout",
            "could not connect", "connection timed out", "connection reset", "network is unreachable",
            "name or service not known", "getaddrinfofailed", "failed to connect", "08006", "08001",
            "57014", "statement timeout", "lock timeout", "deadlock detected", "operationalerror"
        ]
        return any(p in m_lower or p in t_lower for p in net_patterns)

    @classmethod
    def _classify_network_or_timeout(cls, exc: Exception, exc_type: str, msg: str, ctx: Dict[str, Any]) -> ClassifiedFailure:
        m_lower = msg.lower()
        t_lower = exc_type.lower()
        if "timeout" in m_lower or "timeout" in t_lower or "57014" in m_lower:
            category = FailureCategory.TIMEOUT
            code = "ERR_OPERATION_TIMEOUT"
        elif "deadlock" in m_lower or "lock" in m_lower:
            category = FailureCategory.TRANSIENT_DATABASE
            code = "ERR_DB_LOCK_CONTENTION"
        elif "source" in m_lower and ("connect" in m_lower or "unreachable" in m_lower or "refused" in m_lower or "unavailable" in m_lower):
            category = FailureCategory.SOURCE_UNAVAILABLE
            code = "ERR_SOURCE_DB_UNAVAILABLE"
        elif "target" in m_lower and ("connect" in m_lower or "unreachable" in m_lower or "refused" in m_lower or "unavailable" in m_lower):
            category = FailureCategory.TARGET_UNAVAILABLE
            code = "ERR_TARGET_DB_UNAVAILABLE"
        else:
            category = FailureCategory.TRANSIENT_NETWORK
            code = "ERR_NETWORK_CONNECTION_DROPPED"

        return ClassifiedFailure(
            category=category,
            code=code,
            message=f"Transient connection or timeout failure: {msg}",
            retryable=True,
            recoverable=False,
            requires_replan=False,
            requires_user=False,
            severity=FailureSeverity.MEDIUM,
            domain=FailureDomain.MIGRATION_EXECUTION,
            context=ctx,
        )

    @classmethod
    def _is_resource_exhaustion(cls, exc: Exception, exc_type: str, msg: str) -> bool:
        m_lower = msg.lower()
        t_lower = exc_type.lower()
        res_patterns = ["outofmemory", "memoryerror", "disk full", "no space left on device", "resourceexhausted"]
        return any(p in m_lower or p in t_lower for p in res_patterns)

    @classmethod
    def _classify_resource_exhaustion(cls, exc: Exception, exc_type: str, msg: str, ctx: Dict[str, Any]) -> ClassifiedFailure:
        return ClassifiedFailure(
            category=FailureCategory.RESOURCE_EXHAUSTION,
            code="ERR_RESOURCE_EXHAUSTION",
            message=f"System resource exhaustion: {msg}",
            retryable=False,
            recoverable=False,
            requires_replan=False,
            requires_user=True,
            severity=FailureSeverity.CRITICAL,
            domain=FailureDomain.DATABASE_ENGINE,
            context=ctx,
        )

    @classmethod
    def _is_agent_crash(cls, exc: Exception, exc_type: str, msg: str) -> bool:
        m_lower = msg.lower()
        t_lower = exc_type.lower()
        crash_patterns = ["agent lost", "agent crash", "heartbeat ceased", "agent disconnected", "container terminated", "agentheartbeattimeout"]
        return any(p in m_lower or p in t_lower for p in crash_patterns)

    @classmethod
    def _classify_agent_crash(cls, exc: Exception, exc_type: str, msg: str, ctx: Dict[str, Any]) -> ClassifiedFailure:
        is_lost = "lost" in msg.lower() or "heartbeat" in msg.lower()
        category = FailureCategory.AGENT_LOST if is_lost else FailureCategory.AGENT_CRASH
        code = "ERR_AGENT_HEARTBEAT_LOST" if is_lost else "ERR_AGENT_PROCESS_CRASHED"
        return ClassifiedFailure(
            category=category,
            code=code,
            message=f"Docker execution agent failure: {msg}",
            retryable=False,
            recoverable=True,
            requires_replan=False,
            requires_user=False,
            severity=FailureSeverity.HIGH,
            domain=FailureDomain.SYSTEM_ORCHESTRATION,
            context=ctx,
        )
