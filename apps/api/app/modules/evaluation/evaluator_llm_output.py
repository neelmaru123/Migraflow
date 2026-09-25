"""
LLM Output Evaluator
Phase 6: Validates LLM responses and generated AST against strict schemas.
Tracks:
- Invalid output rate (raw JSON / schema parsing errors)
- Validation failure rate (semantic/feasibility failures)
- Self-correction rate
- Successful correction rate
"""

import json
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field

from app.modules.migration_plans.migration_plans_schemas import TransformationPlanAST


class LLMOutputMetricsReport(BaseModel):
    """Detailed LLM output validation report."""
    is_valid_json: bool
    is_valid_pydantic: bool
    validation_failure_rate: float = Field(ge=0.0, le=1.0)
    self_corrected: bool = False
    correction_successful: bool = False
    parsing_errors: List[str] = Field(default_factory=list)
    schema_violation_count: int = 0
    details: Dict[str, Any] = Field(default_factory=dict)


class LLMOutputEvaluator:
    """Evaluates raw model text responses and validates AST compliance."""

    @classmethod
    def evaluate_response(
        cls,
        raw_output: str,
        expected_ast: Optional[TransformationPlanAST] = None,
        correction_attempt: int = 0,
    ) -> LLMOutputMetricsReport:
        parsing_errors = []
        is_json = False
        is_pydantic = False
        schema_violations = 0
        parsed_dict = None

        # 1. JSON parsing check
        try:
            # Strip markdown code fences if model enclosed in ```json ... ```
            clean_str = raw_output.strip()
            if clean_str.startswith("```"):
                lines = clean_str.split("\n")
                if lines[0].startswith("```"):
                    lines = lines[1:]
                if lines and lines[-1].startswith("```"):
                    lines = lines[:-1]
                clean_str = "\n".join(lines).strip()

            parsed_dict = json.loads(clean_str)
            is_json = True
        except Exception as e:
            parsing_errors.append(f"JSON Decode Error: {str(e)}")

        # 2. Pydantic validation check
        if is_json and isinstance(parsed_dict, dict):
            try:
                TransformationPlanAST.model_validate(parsed_dict)
                is_pydantic = True
            except Exception as e:
                parsing_errors.append(f"Pydantic Validation Error: {str(e)}")
                schema_violations = 1

        val_fail_rate = 0.0 if (is_json and is_pydantic) else 1.0
        self_corrected = (correction_attempt > 0)
        correction_successful = self_corrected and (is_json and is_pydantic)

        return LLMOutputMetricsReport(
            is_valid_json=is_json,
            is_valid_pydantic=is_pydantic,
            validation_failure_rate=val_fail_rate,
            self_corrected=self_corrected,
            correction_successful=correction_successful,
            parsing_errors=parsing_errors,
            schema_violation_count=schema_violations,
            details={
                "raw_length": len(raw_output),
                "correction_attempt": correction_attempt,
            },
        )
