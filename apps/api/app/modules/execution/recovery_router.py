"""
Deterministic Recovery Decision Router.
Evaluates classified failures and execution context to produce deterministic recovery actions:
RETRY, RECOVER, REPLAN, ASK_USER, or FAIL.
Enforces hard bounds on agentic loops to prevent infinite replan/retry cycles.
"""

from typing import Any, Dict, Optional
import logging

from app.core.state import (
    FailureCategory,
    FailureDomain,
    FailureSeverity,
    RecoveryDecisionType,
)
from app.modules.execution.failure_taxonomy import (
    ClassifiedFailure,
    RecoveryDecision,
)
from app.modules.execution.retry_policy import DEFAULT_RETRY_POLICY, RetryPolicy

logger = logging.getLogger(__name__)

# Hard Operational Bounds to Prevent Infinite Loops
MAX_RETRIES_PER_STEP = 3
MAX_RECOVERIES_PER_STEP = 2
MAX_REPLANS_PER_JOB = 3
MAX_LLM_CALLS = 5
MAX_TOTAL_RETRY_DURATION_SECONDS = 300.0


class RecoveryRouter:
    """
    Deterministic decision layer for execution errors.
    The LLM never directly controls low-level execution recovery.
    """

    def __init__(self, retry_policy: Optional[RetryPolicy] = None):
        self.retry_policy = retry_policy or DEFAULT_RETRY_POLICY

    def evaluate(
        self,
        failure: ClassifiedFailure,
        step_type: Optional[str] = None,
        step_key: Optional[str] = None,
        attempt_count: int = 1,
        replan_count: int = 0,
        recovery_count: int = 0,
        llm_call_count: int = 0,
        total_retry_duration: float = 0.0,
        is_destructive: bool = False,
        table_name: Optional[str] = None,
    ) -> RecoveryDecision:
        """
        Determines the single authoritative recovery action based on structured failure characteristics
        and operational bounds.
        """
        logger.info(
            f"[RecoveryRouter] Evaluating failure category='{failure.category.value}', code='{failure.code}', "
            f"step='{step_key}', attempt={attempt_count}/{MAX_RETRIES_PER_STEP}, "
            f"replan={replan_count}/{MAX_REPLANS_PER_JOB}, recovery={recovery_count}/{MAX_RECOVERIES_PER_STEP}"
        )

        # ---------------------------------------------------------------------
        # 1. Immediate Cancellation / User Abortion
        # ---------------------------------------------------------------------
        if failure.category == FailureCategory.USER_CANCELLED:
            return RecoveryDecision(
                action=RecoveryDecisionType.FAIL,
                classified_failure=failure,
                reason="Execution cancelled by user request.",
            )

        # ---------------------------------------------------------------------
        # 2. Hard Loop Bounds & Circuit Breakers (Anti-Infinite Loop)
        # ---------------------------------------------------------------------
        if replan_count >= MAX_REPLANS_PER_JOB or llm_call_count >= MAX_LLM_CALLS:
            return RecoveryDecision(
                action=RecoveryDecisionType.ASK_USER if failure.requires_user else RecoveryDecisionType.FAIL,
                classified_failure=failure,
                user_prompt={
                    "title": "Maximum Replans Reached",
                    "question": f"Automated AI replanning failed after {replan_count} attempts. Please review the schema error manually.",
                    "suggested_action": "manual_review",
                    "options": [
                        {"id": "abort", "label": "Abort Migration", "action": "fail"},
                        {"id": "retry_manual", "label": "Edit Plan Manually & Retry", "action": "manual_edit"},
                    ],
                },
                reason=f"Max automated replan limit ({MAX_REPLANS_PER_JOB}) or LLM limit ({MAX_LLM_CALLS}) reached.",
            )

        if recovery_count >= MAX_RECOVERIES_PER_STEP:
            return RecoveryDecision(
                action=RecoveryDecisionType.FAIL,
                classified_failure=failure,
                reason=f"Max agent failover/recovery attempts ({MAX_RECOVERIES_PER_STEP}) exceeded for step '{step_key}'.",
            )

        if total_retry_duration >= MAX_TOTAL_RETRY_DURATION_SECONDS:
            return RecoveryDecision(
                action=RecoveryDecisionType.FAIL,
                classified_failure=failure,
                reason=f"Cumulative retry duration exceeded limit of {MAX_TOTAL_RETRY_DURATION_SECONDS}s.",
            )

        # ---------------------------------------------------------------------
        # 3. Destructive Ambiguity Safety Guard
        # ---------------------------------------------------------------------
        if is_destructive or self.retry_policy.is_destructive_operation(failure.message, step_type or ""):
            return RecoveryDecision(
                action=RecoveryDecisionType.ASK_USER,
                classified_failure=failure,
                user_prompt={
                    "title": "Destructive Action Requires Confirmation",
                    "question": f"A failure occurred during a destructive operation on table '{table_name or 'target'}'. Blind retry is disabled.",
                    "suggested_action": "user_confirmation",
                    "options": [
                        {"id": "truncate_and_proceed", "label": "Clean Target & Retry", "action": "retry"},
                        {"id": "abort", "label": "Abort Execution", "action": "fail"},
                    ],
                },
                reason="Destructive operation safety guard requires explicit user confirmation.",
            )

        # ---------------------------------------------------------------------
        # 4. Explicit ASK_USER Conditions (Unresolvable without Human Input)
        # ---------------------------------------------------------------------
        if failure.requires_user or failure.category in {
            FailureCategory.AUTHENTICATION,
            FailureCategory.AUTHORIZATION,
            FailureCategory.PLAN_INFEASIBLE,
        }:
            prompt_data = {
                "title": f"User Input Required: {failure.category.value.replace('_', ' ').title()}",
                "question": failure.message,
                "suggested_action": "user_intervention",
                "options": [
                    {"id": "replan", "label": "Replan Migration", "action": "replan"},
                    {"id": "retry", "label": "Retry with Updated Config", "action": "retry"},
                    {"id": "abort", "label": "Abort Job", "action": "fail"},
                ],
            }
            return RecoveryDecision(
                action=RecoveryDecisionType.ASK_USER,
                classified_failure=failure,
                user_prompt=prompt_data,
                reason=f"Category '{failure.category.value}' requires user intervention.",
            )

        # ---------------------------------------------------------------------
        # 5. Agent Failover / Crash Recovery (Safe In-Place Checkpoint Resumption)
        # ---------------------------------------------------------------------
        if failure.category in {FailureCategory.AGENT_LOST, FailureCategory.AGENT_CRASH} or failure.recoverable:
            return RecoveryDecision(
                action=RecoveryDecisionType.RECOVER,
                classified_failure=failure,
                reason="Agent disconnect/crash detected; eligible for safe step reassignment and checkpoint resume.",
            )

        # ---------------------------------------------------------------------
        # 6. Retryable Transient Failures (Network, Database Locks, Timeouts, LLM 503)
        # ---------------------------------------------------------------------
        if failure.retryable:
            if attempt_count < MAX_RETRIES_PER_STEP:
                delay = self.retry_policy.compute_backoff_delay(attempt_count)
                return RecoveryDecision(
                    action=RecoveryDecisionType.RETRY,
                    classified_failure=failure,
                    delay_seconds=delay,
                    reason=f"Transient failure '{failure.category.value}' eligible for retry with backoff {delay}s (Attempt {attempt_count + 1}/{MAX_RETRIES_PER_STEP}).",
                )
            else:
                # If retries exhausted but requires replan, fallback to replan
                if failure.requires_replan and replan_count < MAX_REPLANS_PER_JOB:
                    return self._build_replan_decision(failure, step_key, replan_count, table_name)
                return RecoveryDecision(
                    action=RecoveryDecisionType.FAIL,
                    classified_failure=failure,
                    reason=f"Max retry attempts ({MAX_RETRIES_PER_STEP}) exhausted for '{failure.code}'.",
                )

        # ---------------------------------------------------------------------
        # 7. Agentic Replanning (Schema Drift, Invalid AST, Missing Columns, Type Cast Error)
        # ---------------------------------------------------------------------
        if failure.requires_replan or failure.category in {
            FailureCategory.SCHEMA_CHANGED,
            FailureCategory.SOURCE_SCHEMA_MISMATCH,
            FailureCategory.TARGET_SCHEMA_MISMATCH,
            FailureCategory.PLAN_INVALID,
            FailureCategory.TRANSFORMATION_ERROR,
            FailureCategory.CONSTRAINT_VIOLATION,
            FailureCategory.DATA_VALIDATION,
        }:
            if replan_count < MAX_REPLANS_PER_JOB:
                return self._build_replan_decision(failure, step_key, replan_count, table_name)
            else:
                return RecoveryDecision(
                    action=RecoveryDecisionType.FAIL,
                    classified_failure=failure,
                    reason=f"Replan required for '{failure.category.value}', but max replans ({MAX_REPLANS_PER_JOB}) exhausted.",
                )

        # ---------------------------------------------------------------------
        # 8. Unhandled or Fatal Failures
        # ---------------------------------------------------------------------
        return RecoveryDecision(
            action=RecoveryDecisionType.FAIL,
            classified_failure=failure,
            reason=f"Non-retryable, non-recoverable failure '{failure.code}': {failure.message}",
        )

    def _build_replan_decision(
        self,
        failure: ClassifiedFailure,
        step_key: Optional[str],
        replan_count: int,
        table_name: Optional[str],
    ) -> RecoveryDecision:
        """Builds structured sanitized replan context for LangGraph agentic replanning."""
        replan_ctx = {
            "failed_step_key": step_key,
            "failed_table_name": table_name,
            "error_category": failure.category.value,
            "error_code": failure.code,
            "error_message": failure.message,
            "replan_attempt": replan_count + 1,
            "max_replans": MAX_REPLANS_PER_JOB,
            "context_details": failure.context,
        }
        return RecoveryDecision(
            action=RecoveryDecisionType.REPLAN,
            classified_failure=failure,
            replan_context=replan_ctx,
            reason=f"Schema or structural failure '{failure.category.value}' triggers agentic replan {replan_count + 1}/{MAX_REPLANS_PER_JOB}.",
        )


# Singleton instance
recovery_router = RecoveryRouter()
