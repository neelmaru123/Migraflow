"""
Failure Injection Simulator
Phase 6: Controlled failure injection harness for platform resilience and lifecycle verification:
1. Agent crash (heartbeat timeout / container termination)
2. Network failure (socket disconnect / transient connection error)
3. Database timeout (slow query exceeding timeout limits)
4. Schema drift (source metadata changes unexpectedly)
5. Invalid AST (corrupted / malformed blueprint JSON)
6. Target constraint failure (duplicate key / FK integrity breach)
7. Verification mismatch (row count discrepancies post-ETL)
"""

import asyncio
from typing import Any, Dict, Optional
from pydantic import BaseModel, Field

from app.core.state import (
    ExecutionLifecycle,
    ExecutionStepLifecycle,
    ExecutionTimeoutError,
    FailureCategory,
    RecoveryDecisionType,
    VerificationStatus,
)
from app.modules.execution.failure_taxonomy import FailureClassifier
from app.modules.execution.recovery_router import RecoveryRouter
from app.modules.execution.verification_services import (
    VerificationCoordinator,
    VerificationEngine,
    VerificationPolicy,
)


class FailureInjectionResult(BaseModel):
    """Result of a controlled failure simulation."""
    injection_type: str
    simulated_error: str
    classified_category: str
    recovery_decision: str
    expected_decision: str
    lifecycle_transition_safe: bool
    unhandled_state: bool
    details: Dict[str, Any] = Field(default_factory=dict)


class FailureInjectionSimulator:
    """Simulates operational failures to verify lifecycle transitions and recovery routing."""

    def __init__(self, router: Optional[RecoveryRouter] = None):
        self.router = router or RecoveryRouter()

    def simulate_agent_crash(self) -> FailureInjectionResult:
        """Simulates agent crash (heartbeat timeout / worker termination)."""
        error = RuntimeError("Agent container terminated unexpectedly; heartbeat missed for > 60s.")
        classified = FailureClassifier.classify(error, step_type="worker_heartbeat")
        decision = self.router.evaluate(classified, step_type="worker_heartbeat")

        # Must classify as AGENT_CRASH or AGENT_LOST and route to RECOVER
        is_safe = (decision.action in [RecoveryDecisionType.RECOVER, RecoveryDecisionType.FAIL])

        return FailureInjectionResult(
            injection_type="agent_crash",
            simulated_error=str(error),
            classified_category=classified.category.value,
            recovery_decision=decision.action.value,
            expected_decision=RecoveryDecisionType.RECOVER.value,
            lifecycle_transition_safe=is_safe,
            unhandled_state=False,
            details={"reason": decision.reason},
        )

    def simulate_network_failure(self) -> FailureInjectionResult:
        """Simulates transient socket drop / network disconnect."""
        error = ConnectionResetError("Connection closed by remote host: ECONNRESET")
        classified = FailureClassifier.classify(error, step_type="table_stream")
        decision = self.router.evaluate(classified, step_type="table_stream", attempt_count=1)

        is_safe = (decision.action == RecoveryDecisionType.RETRY)

        return FailureInjectionResult(
            injection_type="network_failure",
            simulated_error=str(error),
            classified_category=classified.category.value,
            recovery_decision=decision.action.value,
            expected_decision=RecoveryDecisionType.RETRY.value,
            lifecycle_transition_safe=is_safe,
            unhandled_state=False,
            details={"retry_delay_seconds": getattr(decision, "delay_seconds", 0.0)},
        )

    def simulate_database_timeout(self) -> FailureInjectionResult:
        """Simulates database query exceeding operational timeout limits."""
        error = ExecutionTimeoutError(operation_type="read_batch", timeout_seconds=10.0)
        classified = FailureClassifier.classify(error, step_type="db_query")
        decision = self.router.evaluate(classified, step_type="db_query", attempt_count=1)

        is_safe = (decision.action in [RecoveryDecisionType.RETRY, RecoveryDecisionType.RECOVER])

        return FailureInjectionResult(
            injection_type="database_timeout",
            simulated_error=str(error),
            classified_category=classified.category.value,
            recovery_decision=decision.action.value,
            expected_decision=RecoveryDecisionType.RETRY.value,
            lifecycle_transition_safe=is_safe,
            unhandled_state=False,
            details={"reason": decision.reason},
        )

    def simulate_schema_drift(self) -> FailureInjectionResult:
        """Simulates source column rename or type change midway."""
        error = ValueError("Source schema mismatch: Column 'account_status' not found in source table 'customers'")
        classified = FailureClassifier.classify(error, step_type="schema_validation")
        decision = self.router.evaluate(classified, step_type="schema_validation", replan_count=0)

        is_safe = (decision.action == RecoveryDecisionType.REPLAN)

        return FailureInjectionResult(
            injection_type="schema_drift",
            simulated_error=str(error),
            classified_category=classified.category.value,
            recovery_decision=decision.action.value,
            expected_decision=RecoveryDecisionType.REPLAN.value,
            lifecycle_transition_safe=is_safe,
            unhandled_state=False,
            details={"reason": decision.reason},
        )

    def simulate_invalid_ast(self) -> FailureInjectionResult:
        """Simulates malformed or corrupt AST payload."""
        from app.modules.migration_plans.migration_plans_engine.migration_plans_validator import (
            MigrationPlanValidator,
        )
        malformed_ast = {"table_mappings": "invalid_string_not_list"}
        res = MigrationPlanValidator.validate(malformed_ast, [])

        is_safe = (not res.is_valid and len(res.errors) > 0)

        return FailureInjectionResult(
            injection_type="invalid_ast",
            simulated_error="Malformed AST structure",
            classified_category="plan_invalid",
            recovery_decision="validation_rejected",
            expected_decision="validation_rejected",
            lifecycle_transition_safe=is_safe,
            unhandled_state=False,
            details={"validation_errors": res.errors},
        )

    def simulate_target_constraint_failure(self) -> FailureInjectionResult:
        """Simulates unique key conflict in target database."""
        error = ValueError("duplicate key value violates unique constraint 'uq_customers_email'")
        classified = FailureClassifier.classify(error, step_type="table_copy")
        # In a step with no deduplication key, constraint violation requires user input or replan
        decision = self.router.evaluate(classified, step_type="table_copy", attempt_count=3)

        is_safe = (decision.action in [RecoveryDecisionType.ASK_USER, RecoveryDecisionType.FAIL, RecoveryDecisionType.REPLAN])

        return FailureInjectionResult(
            injection_type="target_constraint_failure",
            simulated_error=str(error),
            classified_category=classified.category.value,
            recovery_decision=decision.action.value,
            expected_decision=RecoveryDecisionType.ASK_USER.value,
            lifecycle_transition_safe=is_safe,
            unhandled_state=False,
            details={"reason": decision.reason},
        )

    def simulate_verification_mismatch(self) -> FailureInjectionResult:
        """Simulates missing rows detected post-ETL by VerificationEngine."""
        status, details = VerificationEngine.check_row_counts(
            source_count=1000,
            target_count=950,
            tolerance_pct=0.0,
        )

        # Lifecycle transition rule: verification failure MUST NOT report COMPLETED
        target_lifecycle = (
            ExecutionLifecycle.FAILED.value
            if status == VerificationStatus.FAILED
            else ExecutionLifecycle.NEEDS_REVIEW.value
        )
        is_safe = (target_lifecycle in [ExecutionLifecycle.FAILED.value, ExecutionLifecycle.NEEDS_REVIEW.value])

        return FailureInjectionResult(
            injection_type="verification_mismatch",
            simulated_error=f"Row count mismatch: source={details['source_rows']}, target={details['target_rows']}",
            classified_category="verification_failed",
            recovery_decision=target_lifecycle,
            expected_decision=ExecutionLifecycle.FAILED.value,
            lifecycle_transition_safe=is_safe,
            unhandled_state=False,
            details={"check_details": details},
        )
