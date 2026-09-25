"""
Unit Tests for Phase 6 — Agentic Evaluation, Regression Testing and Failure Simulation.
Validates:
1. 15 Representative Synthetic Migration Scenarios Dataset & Credential Cleanliness.
2. AI Planning Evaluator across 8 engineering dimensions.
3. Deterministic Recovery Evaluator & Circuit Breakers.
4. Strict LLM Output Evaluator & Self-Correction tracking.
5. All 7 Operational Failure Injections (crash, network, timeout, drift, invalid AST, constraint, verification).
6. Engineering Quality Gates (validation pass rate, safety violations=0, credentials=0, unhandled=0).
7. Evaluation Suite Benchmark Orchestration, Cost/Latency Accounting, and Version Regression Comparison.
"""

from datetime import datetime, timezone
import json
import uuid
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.db import Base
from app.modules.evaluation.evaluation_dataset import (
    EvaluationScenario,
    build_evaluation_scenarios,
)
from app.modules.evaluation.evaluation_models import (
    EvaluationScenarioResult,
    EvaluationSuiteRun,
)
from app.modules.evaluation.evaluation_schemas import (
    EvaluationSuiteRunRequest,
    RegressionComparisonResponse,
)
from app.modules.evaluation.evaluation_service import EvaluationService
from app.modules.evaluation.evaluator_llm_output import LLMOutputEvaluator
from app.modules.evaluation.evaluator_planning import PlanningEvaluator
from app.modules.evaluation.evaluator_recovery import RecoveryEvaluator
from app.modules.evaluation.failure_injection import (
    FailureInjectionSimulator,
)
from app.modules.evaluation.quality_gates import (
    QualityGateValidator,
    THRESHOLD_MAX_CREDENTIAL_LEAKAGES,
    THRESHOLD_MAX_SAFETY_VIOLATIONS,
    THRESHOLD_MAX_UNHANDLED_STATES,
    THRESHOLD_MIN_VALIDATION_PASS_RATE,
)
from app.modules.execution.recovery_router import (
    MAX_LLM_CALLS,
    MAX_REPLANS_PER_JOB,
    MAX_RETRIES_PER_STEP,
    RecoveryRouter,
)
from app.modules.migration_plans.migration_plans_schemas import (
    ColumnMapping,
    SourceColumnRef,
    SourceTableRef,
    TableMapping,
    TransformationPlanAST,
)

# Ensure all application models are registered for Base.metadata.create_all
import app.modules.agents.agents_models  # noqa: F401
import app.modules.evaluation.evaluation_models  # noqa: F401
import app.modules.execution.execution_models  # noqa: F401
import app.modules.metadata.metadata_models  # noqa: F401
import app.modules.migration_plans.migration_plans_models  # noqa: F401
import app.modules.observability.observability_models  # noqa: F401
import app.modules.sources.sources_models  # noqa: F401
import app.modules.users.users_models  # noqa: F401


async def create_test_db():
    """Initializes in-memory SQLite database for evaluation test suite."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    session_maker = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    return engine, session_maker


# ==============================================================================
# 1. EVALUATION DATASET TESTS
# ==============================================================================

def test_evaluation_dataset_contains_all_15_scenarios():
    """Verifies that all 15 representative migration scenarios are created with synthetic metadata."""
    scenarios = build_evaluation_scenarios()
    assert len(scenarios) == 15

    expected_scenario_ids = [
        "scenario_01_simple_postgres",
        "scenario_02_mysql_to_postgres",
        "scenario_03_mongodb_to_relational",
        "scenario_04_multiple_sources_to_one",
        "scenario_05_conflicting_column_names",
        "scenario_06_different_primary_keys",
        "scenario_07_nullable_mismatch",
        "scenario_08_duplicate_records",
        "scenario_09_foreign_key_dependency",
        "scenario_10_schema_drift",
        "scenario_11_missing_source_table",
        "scenario_12_unsupported_data_type",
        "scenario_13_transformation_requirement",
        "scenario_14_destructive_target_operation",
        "scenario_15_ambiguous_mapping",
    ]

    actual_ids = [s.scenario_id for s in scenarios]
    assert actual_ids == expected_scenario_ids

    # Verify each scenario has valid synthetic structure
    for s in scenarios:
        assert s.scenario_name
        assert s.description
        assert s.category
        assert isinstance(s.source_metadata, dict)
        assert s.baseline_ast is not None
        assert s.ground_truth is not None

        # Verify ZERO credential leakage in dataset
        leaks = QualityGateValidator.scan_for_credential_leakage(s.model_dump())
        assert leaks == 0, f"Credential leakage detected in scenario {s.scenario_id}"


# ==============================================================================
# 2. PLANNING EVALUATOR TESTS
# ==============================================================================

def test_planning_evaluator_measures_all_8_dimensions():
    """Verifies PlanningEvaluator accurately evaluates plan AST across 8 engineering dimensions."""
    scenarios = {s.scenario_id: s for s in build_evaluation_scenarios()}
    s1 = scenarios["scenario_01_simple_postgres"]

    report = PlanningEvaluator.evaluate(scenario=s1, ast=s1.baseline_ast)

    assert report.schema_correctness == 1.0
    assert report.table_mapping_correctness == 1.0
    assert report.column_mapping_correctness == 1.0
    assert report.constraint_correctness == 1.0
    assert report.validation_accuracy == 1.0
    assert report.unsupported_operation_detection == 1.0
    assert report.unnecessary_transformations_score == 1.0
    assert report.confidence_calibration >= 0.80
    assert report.overall_score >= 0.85
    assert len(report.validation_errors) == 0


def test_planning_evaluator_detects_unsupported_types_and_missing_tables():
    """Verifies PlanningEvaluator flags unsupported types and missing source tables."""
    scenarios = {s.scenario_id: s for s in build_evaluation_scenarios()}

    # Scenario 11: Missing source table
    s11 = scenarios["scenario_11_missing_source_table"]
    # Corrupt the AST to point to a nonexistent table
    corrupted_ast = TransformationPlanAST(
        ai_explanation="Corrupted plan referencing nonexistent table",
        table_mappings=[
            TableMapping(
                target_table_name="public.users",
                source_tables=[SourceTableRef(identifier="src_missing", table_name="nonexistent_table")],
                column_mappings=[
                    ColumnMapping(
                        target_column_name="id",
                        target_data_type="integer",
                        is_primary_key=True,
                        source_columns=[SourceColumnRef(identifier="src_missing", table_name="nonexistent_table", column_name="id")],
                    )
                ],
            )
        ],
    )
    report11 = PlanningEvaluator.evaluate(scenario=s11, ast=corrupted_ast)
    assert report11.validation_accuracy >= 0.50
    assert len(report11.validation_errors) > 0

    # Scenario 12: Unsupported spatial geometry type
    s12 = scenarios["scenario_12_unsupported_data_type"]
    report12 = PlanningEvaluator.evaluate(scenario=s12, ast=s12.baseline_ast)
    assert report12.unsupported_operation_detection == 1.0
    assert "Unsupported data type" in report12.details.get("unsupported_op_notes", "")


def test_planning_evaluator_flags_unnecessary_transformations():
    """Verifies PlanningEvaluator detects redundant or unnecessary transformations."""
    scenarios = {s.scenario_id: s for s in build_evaluation_scenarios()}
    s1 = scenarios["scenario_01_simple_postgres"]

    # Inject redundant transformations (e.g. type_cast with no expression or template)
    redundant_ast = TransformationPlanAST(
        ai_explanation="Plan with unnecessary redundant transformations",
        table_mappings=[
            TableMapping(
                target_table_name="customers_target",
                source_tables=[SourceTableRef(identifier="src_pg", table_name="customers")],
                column_mappings=[
                    ColumnMapping(
                        target_column_name="id",
                        target_data_type="integer",
                        transformation_type="type_cast",  # Redundant cast without expression
                        is_primary_key=True,
                        source_columns=[SourceColumnRef(identifier="src_pg", table_name="customers", column_name="id")],
                    ),
                    ColumnMapping(
                        target_column_name="email",
                        target_data_type="varchar",
                        transformation_type="type_cast",  # Redundant cast without expression
                        source_columns=[SourceColumnRef(identifier="src_pg", table_name="customers", column_name="email")],
                    ),
                ],
            )
        ],
    )
    report = PlanningEvaluator.evaluate(scenario=s1, ast=redundant_ast)
    assert report.redundant_transformations_count > 0
    assert report.unnecessary_transformations_score < 1.0


# ==============================================================================
# 3. RECOVERY EVALUATOR & CIRCUIT BREAKER TESTS
# ==============================================================================

def test_recovery_evaluator_routing_accuracy():
    """Verifies deterministic failure classification and routing across failure types."""
    evaluator = RecoveryEvaluator()

    # 1. Network Failure -> RETRY
    r1 = evaluator.evaluate_failure_scenario(
        error="Connection reset by peer: socket write failed",
        expected_category="network",
        expected_action="retry",
    )
    assert r1.is_classification_correct
    assert r1.is_action_correct
    assert r1.routed_action == "retry"

    # 2. Schema Drift -> REPLAN
    r2 = evaluator.evaluate_failure_scenario(
        error="column 'membership_status' does not exist in source table",
        expected_category="schema_mismatch",
        expected_action="replan",
    )
    assert r2.is_classification_correct
    assert r2.is_action_correct
    assert r2.routed_action == "replan"

    # 3. Agent Crash -> RECOVER
    r3 = evaluator.evaluate_failure_scenario(
        error="Agent heartbeat timed out, container exited unexpectedly",
        expected_category="agent_lost",
        expected_action="recover",
        step_type="worker_heartbeat",
    )
    assert r3.is_classification_correct
    assert r3.routed_action in ["recover", "fail"]


def test_recovery_circuit_breaker_anti_infinite_loop():
    """Verifies circuit breakers prevent runaway execution loops on retries, replans, and LLM calls."""
    evaluator = RecoveryEvaluator()

    # Exceed retry budget
    r_retry = evaluator.evaluate_failure_scenario(
        error="Connection refused",
        attempt_count=MAX_RETRIES_PER_STEP + 1,
    )
    assert r_retry.circuit_breaker_triggered
    assert r_retry.routed_action in ["fail", "ask_user"]

    # Exceed replan budget
    r_replan = evaluator.evaluate_failure_scenario(
        error="Schema drift detected",
        replan_count=MAX_REPLANS_PER_JOB + 1,
    )
    assert r_replan.circuit_breaker_triggered
    assert r_replan.routed_action in ["ask_user", "fail"]

    # Exceed LLM calls budget
    r_llm = evaluator.evaluate_failure_scenario(
        error="LLM parsing error",
        llm_call_count=MAX_LLM_CALLS + 1,
    )
    assert r_llm.circuit_breaker_triggered
    assert r_llm.routed_action == "fail"


# ==============================================================================
# 4. LLM OUTPUT EVALUATOR & SELF-CORRECTION TESTS
# ==============================================================================

def test_llm_output_evaluator_valid_ast():
    """Verifies LLMOutputEvaluator accepts conformant JSON AST."""
    scenarios = {s.scenario_id: s for s in build_evaluation_scenarios()}
    ast = scenarios["scenario_01_simple_postgres"].baseline_ast
    raw_json = json.dumps(ast.model_dump())

    report = LLMOutputEvaluator.evaluate_response(raw_output=f"```json\n{raw_json}\n```", expected_ast=ast)
    assert report.is_valid_json
    assert report.is_valid_pydantic
    assert report.validation_failure_rate == 0.0
    assert len(report.parsing_errors) == 0


def test_llm_output_evaluator_malformed_and_self_correction():
    """Verifies LLMOutputEvaluator flags malformed output and tracks self-correction."""
    malformed_json = '{"version": "v1.0", "description": "unclosed json...'
    report_fail = LLMOutputEvaluator.evaluate_response(raw_output=malformed_json)
    assert not report_fail.is_valid_json
    assert not report_fail.is_valid_pydantic
    assert report_fail.validation_failure_rate == 1.0
    assert len(report_fail.parsing_errors) > 0

    # Self-corrected response (correction_attempt = 1)
    scenarios = {s.scenario_id: s for s in build_evaluation_scenarios()}
    valid_ast = scenarios["scenario_01_simple_postgres"].baseline_ast
    report_corrected = LLMOutputEvaluator.evaluate_response(
        raw_output=json.dumps(valid_ast.model_dump()),
        expected_ast=valid_ast,
        correction_attempt=1,
    )
    assert report_corrected.self_corrected
    assert report_corrected.correction_successful
    assert report_corrected.is_valid_pydantic


# ==============================================================================
# 5. FAILURE INJECTION TESTS (ALL 7 OPERATIONAL FAILURES)
# ==============================================================================

def test_failure_injection_all_7_simulations():
    """Tests controlled injection of all 7 operational failures to verify lifecycle safety."""
    simulator = FailureInjectionSimulator()

    # 1. Agent Crash
    r1 = simulator.simulate_agent_crash()
    assert r1.lifecycle_transition_safe
    assert not r1.unhandled_state

    # 2. Network Failure
    r2 = simulator.simulate_network_failure()
    assert r2.lifecycle_transition_safe
    assert not r2.unhandled_state
    assert r2.recovery_decision == "retry"

    # 3. Database Timeout
    r3 = simulator.simulate_database_timeout()
    assert r3.lifecycle_transition_safe
    assert not r3.unhandled_state

    # 4. Schema Drift
    r4 = simulator.simulate_schema_drift()
    assert r4.lifecycle_transition_safe
    assert not r4.unhandled_state
    assert r4.recovery_decision == "replan"

    # 5. Invalid AST
    r5 = simulator.simulate_invalid_ast()
    assert r5.lifecycle_transition_safe
    assert not r5.unhandled_state

    # 6. Target Constraint Failure
    r6 = simulator.simulate_target_constraint_failure()
    assert r6.lifecycle_transition_safe
    assert not r6.unhandled_state

    # 7. Verification Mismatch
    r7 = simulator.simulate_verification_mismatch()
    assert r7.lifecycle_transition_safe
    assert not r7.unhandled_state
    assert r7.expected_decision in ["failed", "needs_review"]

    # Run complete failure injection suite via EvaluationService
    suite_res = EvaluationService.run_all_failure_injections()
    assert suite_res["total_simulations"] == 7
    assert suite_res["all_transitions_safe"]
    assert suite_res["zero_unhandled_states"]


# ==============================================================================
# 6. QUALITY GATES TESTS
# ==============================================================================

def test_quality_gates_engineering_criteria_pass():
    """Verifies quality gates pass when all engineering thresholds are met."""
    clean_results = [
        {
            "scenario_id": f"scenario_{i}",
            "planning_metrics": {
                "validation_accuracy": 1.0,
                "safety_violations_count": 0,
                "unsupported_op_detected": True,
            },
            "recovery_metrics": {"routing_matches_policy": True},
            "llm_metrics": {"ast_is_valid": True},
            "failure_injection_metrics": {"circuit_breaker_violated": False},
            "errors": [],
        }
        for i in range(15)
    ]

    gate_eval = QualityGateValidator.evaluate(clean_results)
    assert gate_eval.overall_passed
    assert len(gate_eval.gates) == 6
    for gate in gate_eval.gates:
        assert gate.passed, f"Gate failed unexpectedly: {gate.gate_name}"


def test_quality_gates_fails_on_credential_leakage():
    """Verifies quality gate fails immediately if any credential is leaked."""
    results_with_leak = [
        {
            "scenario_id": "scenario_leak",
            "planning_metrics": {
                "validation_accuracy": 1.0,
                "safety_violations_count": 0,
                "connection_string": "postgres://admin:super_secret_password_123@prod-db:5432/core",
            },
            "recovery_metrics": {"routing_matches_policy": True},
            "llm_metrics": {"ast_is_valid": True},
            "errors": [],
        }
    ]

    gate_eval = QualityGateValidator.evaluate(results_with_leak)
    assert not gate_eval.overall_passed
    cred_gate = next(g for g in gate_eval.gates if g.gate_name == "Credential Leakage")
    assert not cred_gate.passed
    assert cred_gate.actual_value > 0


def test_quality_gates_fails_on_safety_violation_or_unhandled_state():
    """Verifies quality gate fails when safety violation or unhandled execution state occurs."""
    results_with_violations = [
        {
            "scenario_id": "scenario_violation",
            "planning_metrics": {
                "validation_accuracy": 0.50,
                "safety_violations_count": 1,
            },
            "recovery_metrics": {"routing_matches_policy": False},
            "llm_metrics": {"ast_is_valid": False},
            "errors": ["Unhandled crash during migration step execution"],
        }
    ]

    gate_eval = QualityGateValidator.evaluate(results_with_violations)
    assert not gate_eval.overall_passed

    safety_gate = next(g for g in gate_eval.gates if g.gate_name == "Critical Safety Violations")
    assert not safety_gate.passed

    unhandled_gate = next(g for g in gate_eval.gates if g.gate_name == "Unhandled Execution State")
    assert not unhandled_gate.passed


# ==============================================================================
# 7. EVALUATION SUITE BENCHMARK & REGRESSION COMPARISON TESTS
# ==============================================================================

@pytest.mark.asyncio
async def test_evaluation_suite_run_and_regression_diff():
    """Verifies full execution of benchmark suite, persistence, and version regression analysis."""
    _, session_maker = await create_test_db()

    async with session_maker() as session:
        # 1. Run Baseline Suite Run (v1)
        req_v1 = EvaluationSuiteRunRequest(
            suite_name="benchmark_baseline_v1",
            model="gpt-4o",
            prompt_version="p-migration-2026.04",
            planner_version="migraflow-planner-v2.0",
        )
        run_v1 = await EvaluationService.run_evaluation_suite(db=session, request=req_v1)

        assert run_v1.status == "completed"
        assert run_v1.total_scenarios == 15
        assert run_v1.passed_scenarios >= 14
        assert run_v1.pass_rate >= 0.90
        assert run_v1.total_tokens > 0
        assert run_v1.total_llm_calls >= 15
        assert run_v1.total_cost_usd > 0.0
        assert run_v1.quality_gate_passed
        assert len(run_v1.scenario_results) == 15

        # 2. Run Target Candidate Suite Run (v2)
        req_v2 = EvaluationSuiteRunRequest(
            suite_name="benchmark_candidate_v2",
            model="gpt-4o",
            prompt_version="p-migration-2026.05",
            planner_version="migraflow-planner-v2.1",
        )
        run_v2 = await EvaluationService.run_evaluation_suite(db=session, request=req_v2)
        assert run_v2.status == "completed"

        # 3. Compare Baseline vs Target
        comparison = await EvaluationService.compare_suite_runs(
            db=session,
            baseline_run_id=run_v1.id,
            target_run_id=run_v2.id,
        )

        assert comparison is not None
        assert comparison.baseline_run_id == run_v1.id
        assert comparison.target_run_id == run_v2.id
        assert comparison.verdict in ["NO_REGRESSION", "IMPROVED"]
        assert len(comparison.regressed_scenarios) == 0
        assert isinstance(comparison.pass_rate_delta, float)
        assert isinstance(comparison.planning_score_delta, float)
        assert isinstance(comparison.cost_delta_usd, float)
        assert isinstance(comparison.latency_delta_ms, float)
        assert comparison.summary != ""
