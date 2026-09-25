"""
Recovery Evaluator
Phase 6: Evaluates the deterministic failure classification and recovery routing loop:
Failure -> Classification -> Retry / Recover / Replan / ASK_USER.
Validates that the RecoveryRouter selects the optimal action and adheres to anti-infinite-loop circuit breakers.
"""

from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field

from app.core.state import (
    FailureCategory,
    FailureDomain,
    FailureSeverity,
    RecoveryDecisionType,
)
from app.modules.execution.failure_taxonomy import (
    ClassifiedFailure,
    FailureClassifier,
    RecoveryDecision,
)
from app.modules.execution.recovery_router import (
    MAX_LLM_CALLS,
    MAX_REPLANS_PER_JOB,
    MAX_RETRIES_PER_STEP,
    RecoveryRouter,
)


class RecoveryMetricsReport(BaseModel):
    """Evaluation report for error recovery decisions."""
    is_classification_correct: bool
    is_action_correct: bool
    classified_category: str
    expected_category: Optional[str] = None
    routed_action: str
    expected_action: Optional[str] = None
    circuit_breaker_triggered: bool = False
    routing_accuracy: float = Field(ge=0.0, le=1.0)
    details: Dict[str, Any] = Field(default_factory=dict)


class RecoveryEvaluator:
    """Evaluates FailureClassifier and RecoveryRouter behavior across failure scenarios."""

    def __init__(self, router: Optional[RecoveryRouter] = None):
        self.router = router or RecoveryRouter()

    def evaluate_failure_scenario(
        self,
        error: Exception | str,
        expected_category: Optional[str] = None,
        expected_action: Optional[str] = None,
        step_type: Optional[str] = None,
        step_key: Optional[str] = None,
        attempt_count: int = 1,
        replan_count: int = 0,
        recovery_count: int = 0,
        llm_call_count: int = 0,
        is_destructive: bool = False,
    ) -> RecoveryMetricsReport:
        """Classifies a failure and runs it through the deterministic recovery router."""
        # 1. Classification
        classified = FailureClassifier.classify(
            error=error,
            step_type=step_type,
            step_key=step_key,
        )

        cat_correct = True
        if expected_category:
            c_val = classified.category.value.lower()
            exp_val = expected_category.lower()
            cat_correct = (c_val == exp_val) or (exp_val in c_val) or (c_val in exp_val)

        # 2. Decision Routing
        decision = self.router.evaluate(
            failure=classified,
            step_type=step_type,
            step_key=step_key,
            attempt_count=attempt_count,
            replan_count=replan_count,
            recovery_count=recovery_count,
            llm_call_count=llm_call_count,
            is_destructive=is_destructive,
        )

        action_correct = True
        if expected_action:
            action_correct = (decision.action.value.lower() == expected_action.lower())

        # Check circuit breaker triggering (anti-infinite loop)
        circuit_breaker = (
            replan_count >= MAX_REPLANS_PER_JOB
            or attempt_count >= MAX_RETRIES_PER_STEP
            or llm_call_count >= MAX_LLM_CALLS
        )

        accuracy = 1.0 if (cat_correct and action_correct) else (0.5 if (cat_correct or action_correct) else 0.0)

        return RecoveryMetricsReport(
            is_classification_correct=cat_correct,
            is_action_correct=action_correct,
            classified_category=classified.category.value,
            expected_category=expected_category,
            routed_action=decision.action.value,
            expected_action=expected_action,
            circuit_breaker_triggered=circuit_breaker,
            routing_accuracy=accuracy,
            details={
                "attempt_count": attempt_count,
                "replan_count": replan_count,
                "llm_call_count": llm_call_count,
                "decision_reason": decision.reason,
            },
        )
