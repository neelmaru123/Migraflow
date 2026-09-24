"""
Retry Policy Foundation
Provides a reusable retry policy abstraction for durable execution steps and jobs.
Supports max attempts, exponential backoff, jitter, duration bounds, and classification of retryable vs non-retryable errors.
"""

import math
import random
from typing import Any, Dict, Optional, Set, Union

from app.core.state import FailureCategory


class RetryPolicy:
    """
    Reusable retry policy for resilient step execution and recovery.
    """

    def __init__(
        self,
        max_attempts: int = 3,
        initial_delay: float = 1.0,
        backoff_factor: float = 2.0,
        max_delay: float = 60.0,
        max_total_retry_duration: float = 300.0,
        jitter: bool = True,
        retryable_error_types: Optional[Set[str]] = None,
        non_retryable_error_types: Optional[Set[str]] = None,
    ):
        self.max_attempts = max_attempts
        self.initial_delay = initial_delay
        self.backoff_factor = backoff_factor
        self.max_delay = max_delay
        self.max_total_retry_duration = max_total_retry_duration
        self.jitter = jitter

        self.retryable_categories: Set[FailureCategory] = {
            FailureCategory.TRANSIENT_NETWORK,
            FailureCategory.TRANSIENT_DATABASE,
            FailureCategory.TIMEOUT,
            FailureCategory.SOURCE_UNAVAILABLE,
            FailureCategory.TARGET_UNAVAILABLE,
        }

        self.retryable_error_types: Set[str] = (
            {t.upper() for t in retryable_error_types}
            if retryable_error_types is not None
            else {
                "NETWORK_TIMEOUT",
                "CONNECTION_RESET",
                "CONNECTION_REFUSED",
                "DEADLOCK_DETECTED",
                "LOCK_TIMEOUT",
                "AGENT_CRASH",
                "AGENT_TIMEOUT",
                "TEMPORARY_UNAVAILABLE",
                "TRANSIENT_IO_ERROR",
                "RATE_LIMITED",
                "ERR_NETWORK_CONNECTION_DROPPED",
                "ERR_DB_LOCK_CONTENTION",
                "ERR_OPERATION_TIMEOUT",
                "ERR_LLM_RATE_LIMIT",
                "ERR_LLM_SERVICE_UNAVAILABLE",
            }
        )

        self.non_retryable_error_types: Set[str] = (
            {t.upper() for t in non_retryable_error_types}
            if non_retryable_error_types is not None
            else {
                "SCHEMA_MISMATCH",
                "SYNTAX_ERROR",
                "AUTHENTICATION_FAILURE",
                "PERMISSION_DENIED",
                "TABLE_NOT_FOUND",
                "COLUMN_NOT_FOUND",
                "DATA_TYPE_MISMATCH",
                "CONSTRAINT_VIOLATION",
                "INVALID_PLAN_SPEC",
                "ERR_DB_AUTHENTICATION_FAILED",
                "ERR_DB_AUTHORIZATION_DENIED",
                "ERR_USER_CANCELLED",
            }
        )

    def is_retryable_error(self, error_type: Optional[str]) -> bool:
        """Determines if a given error category or type is retryable."""
        if not error_type:
            return True
        norm = str(error_type).strip().upper()
        if norm in self.non_retryable_error_types:
            return False
        if norm in self.retryable_error_types:
            return True
        # Check substring matches for common patterns
        if any(non_ret in norm for non_ret in ["SYNTAX", "AUTH", "PERMISSION", "NOT_FOUND", "CONSTRAINT", "CANCEL"]):
            return False
        if any(ret in norm for ret in ["TIMEOUT", "CONN", "DEADLOCK", "TEMPORARY", "TRANSIENT", "RATE_LIMIT"]):
            return True
        # Default policy: allow retry if not explicitly non-retryable
        return True

    def is_destructive_operation(self, operation: str, step_type: str = "") -> bool:
        """Determines if an operation is destructive (e.g. truncate, drop table, purge)."""
        op_norm = operation.strip().lower()
        step_norm = step_type.strip().lower()
        destructive_patterns = ["truncate", "drop", "purge", "delete_all", "cascade_drop"]
        return any(d in op_norm or d in step_norm for d in destructive_patterns)

    def should_retry(
        self,
        current_attempt: int,
        error_type: Optional[str] = None,
        total_retry_duration: float = 0.0,
        is_destructive: bool = False,
    ) -> bool:
        """
        Determines whether another execution attempt should be scheduled.
        Guards against max attempts, max retry duration, non-retryable errors, and destructive operations.
        """
        if current_attempt >= self.max_attempts:
            return False
        if total_retry_duration >= self.max_total_retry_duration:
            return False
        if is_destructive:
            return False
        return self.is_retryable_error(error_type)

    def compute_backoff_delay(self, attempt: int) -> float:
        """
        Calculates exponential backoff delay with optional full jitter.
        """
        base_delay = min(
            self.max_delay,
            self.initial_delay * (self.backoff_factor ** max(0, attempt - 1)),
        )
        if not self.jitter:
            return round(base_delay, 3)

        # Full jitter: uniform random delay between 0.5 * base_delay and base_delay
        jittered = base_delay * (0.5 + random.random() * 0.5)
        return round(jittered, 3)


# Shared default policy instance
DEFAULT_RETRY_POLICY = RetryPolicy()
