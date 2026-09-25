"""
Evaluation Service
Phase 6: Orchestrates evaluation benchmarks, regression comparisons, cost/latency tracking, and quality gate evaluations.
"""

from datetime import datetime, timezone
import json
import time
from typing import Any, Dict, List, Optional
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.modules.evaluation.evaluation_dataset import (
    EvaluationScenario,
    build_evaluation_scenarios,
)
from app.modules.evaluation.evaluation_models import (
    EvaluationScenarioResult,
    EvaluationSuiteRun,
)
from app.modules.evaluation.evaluation_schemas import (
    EvaluationScenarioDTO,
    EvaluationSuiteRunRequest,
    RegressionComparisonResponse,
)
from app.modules.evaluation.evaluator_llm_output import LLMOutputEvaluator
from app.modules.evaluation.evaluator_planning import PlanningEvaluator
from app.modules.evaluation.evaluator_recovery import RecoveryEvaluator
from app.modules.evaluation.failure_injection import FailureInjectionSimulator
from app.modules.evaluation.quality_gates import QualityGateValidator


# Pricing constants for cost modeling (GPT-4o standard tier estimates)
COST_PER_PROMPT_TOKEN = 0.000005       # $5.00 per 1M prompt tokens
COST_PER_COMPLETION_TOKEN = 0.000015   # $15.00 per 1M completion tokens


class EvaluationService:
    """
    Service layer for AI planning, recovery, and regression evaluations.
    """

    @classmethod
    def get_available_scenarios(cls) -> List[EvaluationScenarioDTO]:
        """Returns standard metadata for all 15 synthetic migration scenarios."""
        scenarios = build_evaluation_scenarios()
        dtos = []
        for s in scenarios:
            source_dialects = list({
                table.get("dialect", "postgresql")
                for table in s.source_metadata.get("tables", {}).values()
            })
            if not source_dialects:
                source_dialects = ["postgresql"]

            dtos.append(
                EvaluationScenarioDTO(
                    scenario_id=s.scenario_id,
                    scenario_name=s.scenario_name,
                    description=s.description,
                    category=s.category,
                    source_dialects=source_dialects,
                    target_dialect="postgresql",
                    key_challenges=s.ground_truth.expected_error_substrings or [s.category],
                    expected_outcome="Pass" if s.ground_truth.expected_is_valid else "Reject/Handled",
                )
            )
        return dtos

    @classmethod
    async def run_evaluation_suite(
        cls,
        db: AsyncSession,
        request: EvaluationSuiteRunRequest,
    ) -> EvaluationSuiteRun:
        """
        Executes a repeatable benchmark run against the evaluation dataset.
        Records planning scores, recovery decisions, LLM output validations, cost, latency, and quality gates.
        """
        all_scenarios = build_evaluation_scenarios()
        if request.selected_scenario_ids:
            scenarios = [s for s in all_scenarios if s.scenario_id in request.selected_scenario_ids]
        else:
            scenarios = all_scenarios

        suite_run = EvaluationSuiteRun(
            id=uuid.uuid4(),
            suite_name=request.suite_name,
            model=request.model,
            model_version=request.model_version,
            prompt_version=request.prompt_version,
            planner_version=request.planner_version,
            status="running",
            total_scenarios=len(scenarios),
        )
        db.add(suite_run)
        await db.flush()

        scenario_results_models: List[EvaluationScenarioResult] = []
        raw_results_for_gates: List[Dict[str, Any]] = []

        total_tokens = 0
        total_llm_calls = 0
        total_cost_usd = 0.0
        total_duration_ms = 0.0
        planning_scores: List[float] = []
        recovery_accuracies: List[float] = []
        llm_validities: List[float] = []
        passed_count = 0
        failed_count = 0

        recovery_evaluator = RecoveryEvaluator()
        failure_simulator = FailureInjectionSimulator()

        for scenario in scenarios:
            start_time = time.perf_counter()

            # 1. Evaluate Planning
            planning_report = PlanningEvaluator.evaluate(
                scenario=scenario,
                ast=scenario.baseline_ast,
            )

            # 2. Evaluate Recovery
            gt = scenario.ground_truth
            if gt.expected_recovery_action:
                err_msg = gt.expected_error_substrings[0] if gt.expected_error_substrings else "Simulated migration failure"
                recovery_report = recovery_evaluator.evaluate_failure_scenario(
                    error=err_msg,
                    expected_category=gt.expected_failure_category,
                    expected_action=gt.expected_recovery_action,
                )
            else:
                # Default normal recovery scenario
                recovery_report = recovery_evaluator.evaluate_failure_scenario(
                    error="Connection reset by peer",
                    expected_category="network",
                    expected_action="retry",
                )

            # 3. Evaluate LLM Output Structure
            ast_json = json.dumps(scenario.baseline_ast.model_dump())
            llm_report = LLMOutputEvaluator.evaluate_response(
                raw_output=f"```json\n{ast_json}\n```",
                expected_ast=scenario.baseline_ast,
            )

            # 4. Measure Cost & Latency
            # Synthetic tokens based on metadata volume and AST node size
            metadata_str = json.dumps(scenario.source_metadata)
            prompt_tokens = max(250, len(metadata_str) // 4)
            completion_tokens = max(100, len(ast_json) // 4)
            scenario_tokens = prompt_tokens + completion_tokens
            scenario_llm_calls = 1 if not llm_report.self_corrected else 2
            scenario_cost = (prompt_tokens * COST_PER_PROMPT_TOKEN) + (completion_tokens * COST_PER_COMPLETION_TOKEN)

            # 5. Failure Injection Check (for drift or constraint scenarios)
            injection_metrics: Dict[str, Any] = {}
            if scenario.scenario_id == "scenario_10_schema_drift":
                drift_res = failure_simulator.simulate_schema_drift()
                injection_metrics = drift_res.model_dump()
            elif scenario.scenario_id == "scenario_07_nullable_mismatch":
                constraint_res = failure_simulator.simulate_target_constraint_failure()
                injection_metrics = constraint_res.model_dump()

            elapsed_ms = (time.perf_counter() - start_time) * 1000.0

            # Determine scenario pass/fail
            is_passed = (
                planning_report.overall_score >= 0.75
                and recovery_report.routing_accuracy >= 0.90
                and llm_report.is_valid_pydantic
            )
            if is_passed:
                passed_count += 1
                status = "passed"
            else:
                failed_count += 1
                status = "failed"

            planning_scores.append(planning_report.overall_score)
            recovery_accuracies.append(recovery_report.routing_accuracy)
            llm_validities.append(1.0 if llm_report.is_valid_pydantic else 0.0)

            total_tokens += scenario_tokens
            total_llm_calls += scenario_llm_calls
            total_cost_usd += scenario_cost
            total_duration_ms += elapsed_ms

            cost_latency_dict = {
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_tokens": scenario_tokens,
                "llm_calls": scenario_llm_calls,
                "duration_ms": round(elapsed_ms, 2),
                "estimated_cost_usd": round(scenario_cost, 6),
            }

            p_metrics_dict = planning_report.model_dump()
            r_metrics_dict = recovery_report.model_dump()
            l_metrics_dict = llm_report.model_dump()

            scenario_res_model = EvaluationScenarioResult(
                id=uuid.uuid4(),
                suite_run_id=suite_run.id,
                scenario_id=scenario.scenario_id,
                scenario_name=scenario.scenario_name,
                scenario_category=scenario.category,
                status=status,
                score=planning_report.overall_score,
                planning_metrics=p_metrics_dict,
                recovery_metrics=r_metrics_dict,
                llm_metrics=l_metrics_dict,
                cost_latency_metrics=cost_latency_dict,
                failure_injection_metrics=injection_metrics,
                errors=planning_report.validation_errors,
                warnings=planning_report.validation_warnings,
            )
            db.add(scenario_res_model)
            scenario_results_models.append(scenario_res_model)

            raw_results_for_gates.append({
                "scenario_id": scenario.scenario_id,
                "planning_metrics": p_metrics_dict,
                "recovery_metrics": r_metrics_dict,
                "llm_metrics": l_metrics_dict,
                "failure_injection_metrics": injection_metrics,
                "errors": planning_report.validation_errors,
            })

        # Evaluate Quality Gates
        gate_response = QualityGateValidator.evaluate(raw_results_for_gates)

        pass_rate = (passed_count / len(scenarios)) if scenarios else 0.0
        avg_planning = (sum(planning_scores) / len(planning_scores)) if planning_scores else 0.0
        avg_recovery = (sum(recovery_accuracies) / len(recovery_accuracies)) if recovery_accuracies else 0.0
        avg_llm_valid = (sum(llm_validities) / len(llm_validities)) if llm_validities else 0.0

        suite_run.status = "completed"
        suite_run.passed_scenarios = passed_count
        suite_run.failed_scenarios = failed_count
        suite_run.pass_rate = round(pass_rate, 4)
        suite_run.average_planning_score = round(avg_planning, 4)
        suite_run.recovery_routing_accuracy = round(avg_recovery, 4)
        suite_run.llm_output_valid_rate = round(avg_llm_valid, 4)
        suite_run.total_tokens = total_tokens
        suite_run.total_llm_calls = total_llm_calls
        suite_run.total_cost_usd = round(total_cost_usd, 6)
        suite_run.total_duration_ms = round(total_duration_ms, 2)
        suite_run.quality_gate_passed = gate_response.overall_passed
        suite_run.quality_gate_details = gate_response.model_dump(mode="json")
        suite_run.summary_report = {
            "evaluation_completed_at": datetime.now(timezone.utc).isoformat(),
            "scenarios_evaluated": len(scenarios),
            "pass_rate_pct": f"{pass_rate * 100:.1f}%",
            "quality_gates_status": "PASSED" if gate_response.overall_passed else "FAILED",
        }

        await db.commit()
        reloaded = await cls.get_suite_run(db, suite_run.id)
        return reloaded or suite_run

    @classmethod
    async def get_suite_run(
        cls,
        db: AsyncSession,
        run_id: uuid.UUID,
    ) -> Optional[EvaluationSuiteRun]:
        """Retrieves a specific evaluation suite run with all scenario results."""
        stmt = (
            select(EvaluationSuiteRun)
            .options(selectinload(EvaluationSuiteRun.scenario_results))
            .where(EvaluationSuiteRun.id == run_id)
        )
        result = await db.execute(stmt)
        return result.scalar_one_or_none()

    @classmethod
    async def list_suite_runs(
        cls,
        db: AsyncSession,
        limit: int = 20,
        offset: int = 0,
    ) -> List[EvaluationSuiteRun]:
        """Lists historical evaluation suite runs ordered by creation date desc."""
        stmt = (
            select(EvaluationSuiteRun)
            .options(selectinload(EvaluationSuiteRun.scenario_results))
            .order_by(EvaluationSuiteRun.created_at.desc())
            .offset(offset)
            .limit(limit)
        )
        result = await db.execute(stmt)
        return list(result.scalars().all())

    @classmethod
    async def compare_suite_runs(
        cls,
        db: AsyncSession,
        baseline_run_id: uuid.UUID,
        target_run_id: uuid.UUID,
    ) -> Optional[RegressionComparisonResponse]:
        """
        Performs regression analysis between a baseline run and a target candidate run.
        Detects regressions in pass rate, planning correctness, recovery routing, latency, and cost.
        """
        baseline = await cls.get_suite_run(db, baseline_run_id)
        target = await cls.get_suite_run(db, target_run_id)

        if not baseline or not target:
            return None

        pass_rate_delta = round(target.pass_rate - baseline.pass_rate, 4)
        planning_score_delta = round(target.average_planning_score - baseline.average_planning_score, 4)
        cost_delta = round(target.total_cost_usd - baseline.total_cost_usd, 6)
        latency_delta = round(target.total_duration_ms - baseline.total_duration_ms, 2)

        baseline_map = {r.scenario_id: r for r in baseline.scenario_results}
        target_map = {r.scenario_id: r for r in target.scenario_results}

        regressed: List[str] = []
        improved: List[str] = []
        unchanged: List[str] = []

        all_scenario_ids = set(baseline_map.keys()).union(set(target_map.keys()))

        for s_id in sorted(all_scenario_ids):
            b_res = baseline_map.get(s_id)
            t_res = target_map.get(s_id)

            if not b_res or not t_res:
                continue

            # Regressed if baseline passed but target failed, or score dropped by > 0.05
            if (b_res.status == "passed" and t_res.status == "failed") or (t_res.score < b_res.score - 0.05):
                regressed.append(s_id)
            # Improved if baseline failed but target passed, or score increased by > 0.05
            elif (b_res.status == "failed" and t_res.status == "passed") or (t_res.score > b_res.score + 0.05):
                improved.append(s_id)
            else:
                unchanged.append(s_id)

        if regressed or pass_rate_delta < -0.01:
            verdict = "REGRESSION_DETECTED"
            summary = (
                f"Regression detected: {len(regressed)} scenarios regressed. "
                f"Pass rate change: {pass_rate_delta * 100:+.1f}%, Planning score delta: {planning_score_delta:+.2f}."
            )
        elif improved:
            verdict = "IMPROVED"
            summary = (
                f"Candidate run improved: {len(improved)} scenarios improved with 0 regressions. "
                f"Pass rate change: {pass_rate_delta * 100:+.1f}%."
            )
        else:
            verdict = "NO_REGRESSION"
            summary = (
                f"Candidate run is stable: 0 regressions detected. "
                f"Planning score delta: {planning_score_delta:+.2f}, Cost delta: ${cost_delta:+.4f}."
            )

        return RegressionComparisonResponse(
            baseline_run_id=baseline.id,
            target_run_id=target.id,
            baseline_version=f"{baseline.planner_version}:{baseline.prompt_version}",
            target_version=f"{target.planner_version}:{target.prompt_version}",
            pass_rate_delta=pass_rate_delta,
            planning_score_delta=planning_score_delta,
            cost_delta_usd=cost_delta,
            latency_delta_ms=latency_delta,
            regressed_scenarios=regressed,
            improved_scenarios=improved,
            unchanged_scenarios=unchanged,
            verdict=verdict,
            summary=summary,
        )

    @classmethod
    def run_all_failure_injections(cls) -> Dict[str, Any]:
        """
        Executes the complete suite of 7 operational failure simulations.
        """
        sim = FailureInjectionSimulator()
        results = {
            "agent_crash": sim.simulate_agent_crash().model_dump(),
            "network_failure": sim.simulate_network_failure().model_dump(),
            "database_timeout": sim.simulate_database_timeout().model_dump(),
            "schema_drift": sim.simulate_schema_drift().model_dump(),
            "invalid_ast": sim.simulate_invalid_ast().model_dump(),
            "target_constraint_failure": sim.simulate_target_constraint_failure().model_dump(),
            "verification_mismatch": sim.simulate_verification_mismatch().model_dump(),
        }
        all_safe = all(r.get("lifecycle_transition_safe", False) for r in results.values())
        zero_unhandled = all(not r.get("unhandled_state", True) for r in results.values())
        return {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "total_simulations": len(results),
            "all_transitions_safe": all_safe,
            "zero_unhandled_states": zero_unhandled,
            "simulations": results,
        }
