"""
Planning Evaluator
Phase 6: Deterministically evaluates AI migration plans across 8 engineering dimensions:
1. Schema correctness
2. Table mapping correctness
3. Column mapping correctness
4. Constraint correctness
5. Validation accuracy
6. Unsupported-operation detection
7. Unnecessary transformations detection
8. Confidence calibration
"""

from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field

from app.modules.evaluation.evaluation_dataset import EvaluationScenario
from app.modules.migration_plans.migration_plans_engine.migration_plans_validator import (
    MigrationPlanValidator,
    PlanValidationResult,
)
from app.modules.migration_plans.migration_plans_schemas import TransformationPlanAST


class PlanningMetricsReport(BaseModel):
    """Detailed scores across all planning dimensions."""
    schema_correctness: float = Field(ge=0.0, le=1.0)
    table_mapping_correctness: float = Field(ge=0.0, le=1.0)
    column_mapping_correctness: float = Field(ge=0.0, le=1.0)
    constraint_correctness: float = Field(ge=0.0, le=1.0)
    validation_accuracy: float = Field(ge=0.0, le=1.0)
    unsupported_operation_detection: float = Field(ge=0.0, le=1.0)
    unnecessary_transformations_score: float = Field(ge=0.0, le=1.0)
    confidence_calibration: float = Field(ge=0.0, le=1.0)
    overall_score: float = Field(ge=0.0, le=1.0)

    redundant_transformations_count: int = 0
    validation_errors: List[str] = Field(default_factory=list)
    validation_warnings: List[str] = Field(default_factory=list)
    details: Dict[str, Any] = Field(default_factory=dict)


class PlanningEvaluator:
    """Evaluates TransformationPlanAST plans against ground-truth scenarios."""

    @classmethod
    def evaluate(
        cls,
        scenario: EvaluationScenario,
        ast: TransformationPlanAST,
        validation_result: Optional[PlanValidationResult] = None,
    ) -> PlanningMetricsReport:
        gt = scenario.ground_truth

        # 1. Validation Accuracy Check
        if validation_result is None:
            # Create synthetic MetadataSnapshot structure for validation check
            validation_result = cls._run_synthetic_validation(scenario, ast)

        val_match = (validation_result.is_valid == gt.expected_is_valid)
        error_matches = True
        for expected_err in gt.expected_error_substrings:
            if not any(expected_err.lower() in err.lower() for err in validation_result.errors):
                error_matches = False
                break

        validation_accuracy = 1.0 if (val_match and error_matches) else (0.5 if val_match else 0.0)

        # 2. Table Mapping Correctness
        actual_table_count = len(ast.table_mappings)
        table_diff = abs(actual_table_count - gt.expected_table_count)
        table_score = max(0.0, 1.0 - (table_diff * 0.5))

        if gt.expected_mappings:
            matched_mappings = 0
            for tm in ast.table_mappings:
                for src in tm.source_tables:
                    if src.table_name in gt.expected_mappings and gt.expected_mappings[src.table_name] == tm.target_table_name:
                        matched_mappings += 1
            mapping_ratio = matched_mappings / max(1, len(gt.expected_mappings))
            table_score = (table_score + mapping_ratio) / 2.0

        # 3. Column Mapping & Schema Correctness
        total_columns = 0
        correct_columns = 0
        correct_types = 0
        redundant_transforms = 0
        pk_correct = 0

        target_cols_seen = set()
        duplicate_col_penalties = 0

        for tm in ast.table_mappings:
            for cm in tm.column_mappings:
                total_columns += 1
                col_name = (cm.target_column_name or "").lower()

                # Check uniqueness of target columns within table
                if col_name in target_cols_seen:
                    duplicate_col_penalties += 1
                target_cols_seen.add(col_name)

                # Check type cast expectations
                if col_name in gt.expected_type_casts:
                    expected_type = gt.expected_type_casts[col_name].lower()
                    actual_type = (cm.target_data_type or "").lower()
                    if expected_type in actual_type or actual_type in expected_type:
                        correct_types += 1
                else:
                    correct_types += 1

                # Check unnecessary transformations (e.g. direct copy marked as complex cast with identical types)
                if cm.transformation_type in ["type_cast", "expression", "expression_sql"] and not cm.expression_template:
                    if cm.source_columns and len(cm.source_columns) == 1:
                        # Redundant if no actual change
                        redundant_transforms += 1

                # Check PK
                if cm.is_primary_key:
                    pk_correct += 1
                correct_columns += 1

        col_score = max(0.0, (correct_columns - duplicate_col_penalties) / max(1, total_columns))
        schema_score = correct_types / max(1, total_columns)
        unnecessary_score = max(0.0, 1.0 - (redundant_transforms * 0.15))

        # 4. Constraint Correctness
        # Check two-phase FK hygiene and PK existence
        has_pre_fk = any("FOREIGN KEY" in ddl.upper() for ddl in ast.pre_migration_ddl)
        has_pk = any(any(cm.is_primary_key for cm in tm.column_mappings) for tm in ast.table_mappings)
        constraint_score = 1.0
        if has_pre_fk:
            constraint_score -= 0.5
        if not has_pk and gt.expected_is_valid:
            constraint_score -= 0.3
        constraint_score = max(0.0, constraint_score)

        # 5. Unsupported Operation Detection
        unsupported_detected = 1.0
        if scenario.scenario_id == "scenario_12_unsupported_data_type":
            # Must have converted to text or json or flagged in warnings
            has_fallback = any(cm.target_data_type in ["text", "jsonb", "json"] for tm in ast.table_mappings for cm in tm.column_mappings)
            unsupported_detected = 1.0 if has_fallback else 0.3

        # 6. Confidence Calibration
        conf = ast.confidence_score
        if gt.min_confidence <= conf <= gt.max_confidence:
            calibration_score = 1.0
        elif conf < gt.min_confidence:
            calibration_score = max(0.0, 1.0 - (gt.min_confidence - conf) * 2.0)
        else:
            # Overconfident penalty
            calibration_score = max(0.0, 1.0 - (conf - gt.max_confidence) * 3.0)

        # Weighted Overall Score
        overall = (
            0.20 * schema_score
            + 0.15 * table_score
            + 0.20 * col_score
            + 0.15 * constraint_score
            + 0.15 * validation_accuracy
            + 0.05 * unsupported_detected
            + 0.05 * unnecessary_score
            + 0.05 * calibration_score
        )

        return PlanningMetricsReport(
            schema_correctness=round(schema_score, 4),
            table_mapping_correctness=round(table_score, 4),
            column_mapping_correctness=round(col_score, 4),
            constraint_correctness=round(constraint_score, 4),
            validation_accuracy=round(validation_accuracy, 4),
            unsupported_operation_detection=round(unsupported_detected, 4),
            unnecessary_transformations_score=round(unnecessary_score, 4),
            confidence_calibration=round(calibration_score, 4),
            overall_score=round(overall, 4),
            redundant_transformations_count=redundant_transforms,
            validation_errors=validation_result.errors,
            validation_warnings=validation_result.warnings,
            details={
                "scenario_id": scenario.scenario_id,
                "confidence_score": conf,
                "expected_is_valid": gt.expected_is_valid,
                "actual_is_valid": validation_result.is_valid,
                "unsupported_op_notes": "Unsupported data type mapped to safe fallback" if (unsupported_detected >= 0.9 and scenario.scenario_id == "scenario_12_unsupported_data_type") else "",
            },
        )

    @classmethod
    def _run_synthetic_validation(
        cls,
        scenario: EvaluationScenario,
        ast: TransformationPlanAST,
    ) -> PlanValidationResult:
        """Constructs lightweight mock snapshot structure to validate AST deterministically."""
        from unittest.mock import MagicMock
        from app.modules.metadata.metadata_models import MetadataColumn, MetadataSchema, MetadataSnapshot, MetadataTable

        snap = MagicMock(spec=MetadataSnapshot)
        snap.id = "synthetic_snap_id"
        snap.data_source_id = "synthetic_ds"
        snap.data_source = MagicMock()
        snap.data_source.identifier = scenario.source_metadata.get("alias", "src_pg")

        schema_obj = MagicMock(spec=MetadataSchema)
        schema_obj.schema_name = "public"
        table_objs = []

        tables_dict = scenario.source_metadata.get("tables", {})
        for tname, cols in tables_dict.items():
            tbl = MagicMock(spec=MetadataTable)
            tbl.table_name = tname
            col_objs = []
            for cname, cinfo in cols.items():
                c = MagicMock(spec=MetadataColumn)
                c.column_name = cname
                c.data_type = cinfo.get("type", "varchar")
                col_objs.append(c)
            tbl.columns = col_objs
            table_objs.append(tbl)

        schema_obj.tables = table_objs
        snap.schemas = [schema_obj]

        alias_map = {str(snap.data_source_id): snap.data_source.identifier}
        return MigrationPlanValidator.validate(ast, [snap], alias_map=alias_map)
