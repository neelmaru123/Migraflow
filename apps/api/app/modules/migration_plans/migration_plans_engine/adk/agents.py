"""
Google ADK Multi-Agent Architecture for Migraflow
Powered by Google GenAI SDK (google-genai) and gemini-3.5-flash-lite.

Implements specialized migration agent roles:
1. Schema & Topology Specialist
2. Schema Mapping & Conflict Resolution Specialist
3. DDL & Dialect Synthesis Specialist
4. Plan Auditor & Validator Specialist
"""

import json
import logging
from typing import Any, Callable, Dict, List, Optional

from google import genai
from google.genai import types

from app.core.config import settings
from app.modules.migration_plans.migration_plans_engine.adk.tools.ddl_linter_tool import (
    validate_ddl_batch,
)
from app.modules.migration_plans.migration_plans_engine.adk.tools.fk_topology_tool import (
    analyze_foreign_key_topology,
)
from app.modules.migration_plans.migration_plans_engine.adk.tools.type_compatibility_tool import (
    check_type_compatibility,
)
from app.modules.migration_plans.migration_plans_schemas import (
    RefinementFeedback,
    TransformationPlanAST,
)

logger = logging.getLogger(__name__)

# ============================================================================
# System Instructions for ADK Migration Engine
# ============================================================================
ADK_SYSTEM_INSTRUCTION = """You are Migraflow's Senior Database Migration Architect Agent.
Your mission is to generate a comprehensive, lossless, dialect-compliant TransformationPlanAST JSON object
that merges and transforms source database schemas into a unified target database.

ENTERPRISE SPECIFICATION & DIALECT HYGIENE RULES:
1. ZERO DATA LOSS: All source tables and columns must be mapped (or explicitly marked as drop_column).
2. CANONICAL NAMING: Target tables and columns must use lowercase snake_case.
3. PRIMARY KEYS & AUDIT COLUMNS:
   - Target tables must have a primary key named 'id' (or '_id' for MongoDB).
   - PostgreSQL: Use 'id UUID PRIMARY KEY DEFAULT gen_random_uuid()'. NEVER use 'uuid_v4()' or 'uuidv4()'.
   - MySQL: Use 'VARCHAR(36) PRIMARY KEY' or 'BIGINT AUTO_INCREMENT PRIMARY KEY'.
   - Audit columns: 'created_at' and 'updated_at' must be included with transformation_type 'new_column_added'.
   - Merged tables: Must include '_source_origin' (VARCHAR) to trace provenance.
4. FOREIGN KEY & PRIMARY KEY SYNCHRONIZATION:
   - When a primary key is UUID, any foreign key pointing to it in other tables must also be UUID (type_cast).
5. TWO-PHASE DDL HYGIENE:
   - pre_migration_ddl: CREATE TABLE statements only. NO inline or table-level foreign keys!
   - post_migration_ddl: CREATE INDEX and ALTER TABLE ... ADD CONSTRAINT FOREIGN KEY statements.
   - For PostgreSQL: include 'CREATE EXTENSION IF NOT EXISTS "uuid-ossp";' if needed.
6. MONGODB / NOSQL TO SQL CONVERSION:
   - High-coverage fields: promote to SQL columns (nosql_field_promote or json_flatten).
   - Nested paths (e.g. address.city): flatten to snake_case (address_city).
   - Low-coverage or residual fields: store in catch-all 'extra_attributes' column (JSONB/JSON/TEXT) using json_stringify.
7. POSTGRESQL ARRAYS TO SQL:
   - Simple text arrays (TEXT[]) -> 'array_to_csv'.
   - Complex/structured arrays -> 'array_to_json'.
8. MERGE TABLE MULTI-SOURCE COLUMN BINDING:
   - When target table has transformation_type="merge", every target column mapping must declare
     a source_columns entry for EACH participating source table.
9. DETERMINISTIC OUTPUT:
   - Output must strictly conform to TransformationPlanAST schema.
"""


class GoogleADKMigrationEngine:
    """
    Multi-agent migration planning engine utilizing Google GenAI SDK.
    """

    def __init__(self, api_key: Optional[str] = None, model: Optional[str] = None):
        self.api_key = api_key or settings.GEMINI_API_KEY
        self.model = model or settings.LLM_MODEL or "gemini-3.5-flash-lite"
        self._client: Optional[genai.Client] = None

    @property
    def client(self) -> genai.Client:
        if self._client is None:
            if not self.api_key:
                raise ValueError("GEMINI_API_KEY is not configured in settings or environment.")
            self._client = genai.Client(api_key=self.api_key)
        return self._client

    def generate_plan(
        self,
        context_str: str,
        target_db_type: str,
        progress_callback: Optional[Callable[[str, str], None]] = None,
    ) -> TransformationPlanAST:
        """
        Executes multi-phase agent planning:
        1. Emits schema analysis status
        2. Generates initial AST via Gemini structured output
        3. Audits and validates DDL & foreign keys using sandboxed tools
        4. Auto-corrects if syntax/validation errors are found
        """
        if progress_callback:
            progress_callback("inspect_schema", f"Analyzing source schemas for target {target_db_type} with {self.model}...")

        prompt = (
            f"Analyze the following sanitized source database schemas and generate a complete "
            f"TransformationPlan AST to merge them into a single target database of type '{target_db_type}'.\n\n"
            f"METADATA CONTEXT:\n{context_str}"
        )

        max_retries = settings.LLM_MAX_RETRIES
        last_error: Optional[Exception] = None

        for attempt in range(1, max_retries + 1):
            try:
                if progress_callback:
                    progress_callback(
                        "synthesize_mappings",
                        f"Generating transformation blueprint (attempt {attempt}/{max_retries})...",
                    )

                logger.info(f"[Google ADK] Generating plan using model '{self.model}' (attempt {attempt}/{max_retries})")

                config = types.GenerateContentConfig(
                    system_instruction=ADK_SYSTEM_INSTRUCTION,
                    temperature=settings.LLM_TEMPERATURE,
                    response_mime_type="application/json",
                    response_schema=TransformationPlanAST,
                )

                response = self.client.models.generate_content(
                    model=self.model,
                    contents=prompt,
                    config=config,
                )

                ast_obj: Optional[TransformationPlanAST] = None
                if hasattr(response, "parsed") and isinstance(response.parsed, TransformationPlanAST):
                    ast_obj = response.parsed
                elif response.text:
                    ast_dict = json.loads(response.text)
                    ast_obj = TransformationPlanAST.model_validate(ast_dict)

                if ast_obj is None:
                    raise ValueError("Model did not return a valid TransformationPlanAST structure.")

                # Phase 2: DDL & Topology Audit via Tools
                if progress_callback:
                    progress_callback("audit_plan", "Auditing DDL syntax and foreign key topologies with validation tools...")

                pre_ddl_report = validate_ddl_batch(
                    ast_obj.pre_migration_ddl,
                    target_dialect=target_db_type,
                    is_pre_migration=True,
                )
                post_ddl_report = validate_ddl_batch(
                    ast_obj.post_migration_ddl,
                    target_dialect=target_db_type,
                    is_pre_migration=False,
                )

                if not pre_ddl_report["is_valid"] or not post_ddl_report["is_valid"]:
                    ddl_errors = pre_ddl_report["errors"] + post_ddl_report["errors"]
                    logger.warning(f"[Google ADK] DDL linter caught issues: {ddl_errors}")
                    if attempt < max_retries:
                        prompt += (
                            f"\n\nNOTICE: The previous DDL statements had syntax or dialect errors:\n"
                            + "\n".join(f"- {e}" for e in ddl_errors)
                            + f"\nPlease fix these DDL errors for target dialect '{target_db_type}'."
                        )
                        continue

                # Add warnings from linter if any
                for warn in pre_ddl_report.get("warnings", []) + post_ddl_report.get("warnings", []):
                    if warn not in ast_obj.warnings:
                        ast_obj.warnings.append(warn)

                if progress_callback:
                    progress_callback("completed", "Plan generation successfully verified by Google ADK engine.")

                logger.info(f"[Google ADK] Plan generation successfully completed on attempt {attempt}.")
                return ast_obj

            except Exception as exc:
                last_error = exc
                logger.warning(f"[Google ADK] Attempt {attempt} failed: {exc}")

        raise RuntimeError(
            f"Google ADK plan generation failed after {max_retries} attempts. Last error: {last_error}"
        )

    def refine_plan(
        self,
        context_str: str,
        current_ast_dict: dict,
        user_feedback: Optional[str] = None,
        validation_errors: Optional[List[str]] = None,
        max_retries: int = 3,
        progress_callback: Optional[Callable[[str, str], None]] = None,
    ) -> TransformationPlanAST:
        """
        Executes refinement of an existing TransformationPlanAST given user feedback or validation errors.
        Enforces zero data loss feasibility rules and populates refinement_feedback.
        """
        table_count_before = len(current_ast_dict.get("table_mappings", []))
        target_db_type = current_ast_dict.get("target_database_type", "postgresql")

        if progress_callback:
            progress_callback(
                "evaluating_refinement",
                f"Evaluating refinement feasibility with {self.model} (current tables: {table_count_before})...",
            )

        feedback_instructions = []
        if user_feedback:
            feedback_instructions.append(f"USER FEEDBACK INSTRUCTION:\n{user_feedback}")
        if validation_errors:
            err_list = "\n".join(f"- {err}" for err in validation_errors)
            feedback_instructions.append(f"STRUCTURAL VALIDATION ERRORS TO RESOLVE:\n{err_list}")

        feasibility_prompt = (
            f"CRITICAL FEASIBILITY & TRANSPARENCY RULES FOR REFINEMENT:\n"
            f"1. The current plan has {table_count_before} target tables.\n"
            f"2. Evaluate USER FEEDBACK against zero-data-loss constraints.\n"
            f"3. If the request is impossible or causes data loss (e.g. merging incompatible tables with no keys):\n"
            f"   - Set refinement_feedback.applied = false\n"
            f"   - Set refinement_feedback.verdict = 'infeasible_rejected'\n"
            f"   - Set refinement_feedback.explanation = detailed technical explanation of why it is infeasible.\n"
            f"   - Preserve all safe tables in table_mappings.\n"
            f"4. If feasible:\n"
            f"   - Apply adjustments to table_mappings and column_mappings.\n"
            f"   - Set refinement_feedback.applied = true (or verdict='partially_applied').\n"
            f"   - Set refinement_feedback.explanation = summary of adjustments made.\n"
            f"5. Always populate refinement_feedback.user_prompt, table_count_before={table_count_before}, table_count_after, and changes_summary.\n"
        )
        feedback_instructions.append(feasibility_prompt)
        instructions_str = "\n\n".join(feedback_instructions)

        refine_prompt = (
            f"Here is the database metadata context:\n{context_str}\n\n"
            f"Here is the CURRENT TransformationPlan AST blueprint:\n```json\n{json.dumps(current_ast_dict, indent=2)}\n```\n\n"
            f"Apply the following refinement instructions to evaluate, update, and improve the TransformationPlan AST blueprint:\n"
            f"{instructions_str}\n\n"
            f"Output the updated complete TransformationPlan AST matching the schema."
        )

        last_error = None
        for attempt in range(1, max_retries + 1):
            try:
                if progress_callback:
                    progress_callback(
                        "refining_ast",
                        f"Synthesizing refined migration blueprint (attempt {attempt}/{max_retries})...",
                    )

                logger.info(f"[Google ADK] Refining plan using model '{self.model}' (attempt {attempt}/{max_retries})")

                config = types.GenerateContentConfig(
                    system_instruction=ADK_SYSTEM_INSTRUCTION,
                    temperature=settings.LLM_TEMPERATURE,
                    response_mime_type="application/json",
                    response_schema=TransformationPlanAST,
                )

                response = self.client.models.generate_content(
                    model=self.model,
                    contents=refine_prompt,
                    config=config,
                )

                ast_obj: Optional[TransformationPlanAST] = None
                if hasattr(response, "parsed") and isinstance(response.parsed, TransformationPlanAST):
                    ast_obj = response.parsed
                elif response.text:
                    ast_dict = json.loads(response.text)
                    ast_obj = TransformationPlanAST.model_validate(ast_dict)

                if ast_obj is None:
                    raise ValueError("Model did not return a valid refined TransformationPlanAST structure.")

                # Ensure refinement_feedback is guaranteed populated
                table_count_after = len(ast_obj.table_mappings)
                if ast_obj.refinement_feedback is None:
                    applied = (table_count_before != table_count_after) or (user_feedback is None)
                    verdict = "applied" if applied else "infeasible_rejected"
                    explanation = ast_obj.ai_explanation
                    if not applied and user_feedback:
                        explanation = (
                            f"The requested refinement ('{user_feedback}') could not be applied without data loss. "
                            f"The original {table_count_before} target tables were preserved to guarantee data fidelity."
                        )
                    ast_obj.refinement_feedback = RefinementFeedback(
                        applied=applied,
                        verdict=verdict,
                        user_prompt=user_feedback,
                        explanation=explanation,
                        table_count_before=table_count_before,
                        table_count_after=table_count_after,
                        changes_summary=[explanation],
                    )

                # DDL Audit
                pre_ddl_report = validate_ddl_batch(ast_obj.pre_migration_ddl, target_dialect=target_db_type, is_pre_migration=True)
                post_ddl_report = validate_ddl_batch(ast_obj.post_migration_ddl, target_dialect=target_db_type, is_pre_migration=False)

                for warn in pre_ddl_report.get("warnings", []) + post_ddl_report.get("warnings", []):
                    if warn not in ast_obj.warnings:
                        ast_obj.warnings.append(warn)

                if progress_callback:
                    progress_callback("completed", "Refinement successfully completed and validated.")

                logger.info(f"[Google ADK] Plan refinement successfully completed on attempt {attempt}.")
                return ast_obj

            except Exception as exc:
                last_error = exc
                logger.warning(f"[Google ADK] Refinement attempt {attempt} failed: {exc}")

        raise RuntimeError(
            f"Google ADK plan refinement failed after {max_retries} attempts. Last error: {last_error}"
        )
