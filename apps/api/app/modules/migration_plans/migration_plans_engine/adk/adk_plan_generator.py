"""
Google ADK Plan Generator Service
Bridge and interface compatible with LLMPlanGeneratorService.
"""

from typing import Callable, List, Optional
from app.modules.migration_plans.migration_plans_engine.adk.agents import (
    GoogleADKMigrationEngine,
)
from app.modules.migration_plans.migration_plans_schemas import (
    TransformationPlanAST,
)

# Shared singleton instance
adk_migration_engine = GoogleADKMigrationEngine()


class ADKPlanGeneratorService:
    """Service adapter exposing generate() and refine() using Google ADK."""

    def __init__(self, engine: Optional[GoogleADKMigrationEngine] = None):
        self.engine = engine or adk_migration_engine

    def generate(
        self,
        context_str: str,
        target_db_type: str,
        progress_callback: Optional[Callable[[str, str], None]] = None,
    ) -> TransformationPlanAST:
        return self.engine.generate_plan(
            context_str=context_str,
            target_db_type=target_db_type,
            progress_callback=progress_callback,
        )

    def refine(
        self,
        context_str: str,
        current_ast_dict: dict,
        user_feedback: Optional[str] = None,
        validation_errors: Optional[List[str]] = None,
        max_retries: int = 3,
        progress_callback: Optional[Callable[[str, str], None]] = None,
    ) -> TransformationPlanAST:
        return self.engine.refine_plan(
            context_str=context_str,
            current_ast_dict=current_ast_dict,
            user_feedback=user_feedback,
            validation_errors=validation_errors,
            max_retries=max_retries,
            progress_callback=progress_callback,
        )


adk_plan_generator = ADKPlanGeneratorService()


def generate(
    context_str: str,
    target_db_type: str,
    progress_callback: Optional[Callable[[str, str], None]] = None,
) -> TransformationPlanAST:
    return adk_plan_generator.generate(
        context_str=context_str,
        target_db_type=target_db_type,
        progress_callback=progress_callback,
    )


def refine(
    context_str: str,
    current_ast_dict: dict,
    user_feedback: Optional[str] = None,
    validation_errors: Optional[List[str]] = None,
    max_retries: int = 3,
    progress_callback: Optional[Callable[[str, str], None]] = None,
) -> TransformationPlanAST:
    return adk_plan_generator.refine(
        context_str=context_str,
        current_ast_dict=current_ast_dict,
        user_feedback=user_feedback,
        validation_errors=validation_errors,
        max_retries=max_retries,
        progress_callback=progress_callback,
    )
