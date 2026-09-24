"""
LLM Observability & Provenance Tracker
Records model, provider, prompt_version, latency, tokens, cost, and structured output validation.
Enforces credential sanitization: never stores secrets or unmasked raw database rows.
"""

from datetime import datetime, timezone
import logging
from typing import Any, Dict, Optional
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.credential_sanitizer import CredentialSanitizer
from app.core.state import ExecutionEventType
from app.modules.execution.execution_models import ExecutionEvent
from app.modules.observability.budget_manager import ResourceBudgetManager
from app.modules.observability.observability_models import LLMCallRecord

logger = logging.getLogger(__name__)


# Approximate pricing per 1K tokens in USD for cost estimation
PROVIDER_PRICING: Dict[str, Dict[str, float]] = {
    "openai": {"prompt_per_1k": 0.005, "completion_per_1k": 0.015},
    "google": {"prompt_per_1k": 0.0035, "completion_per_1k": 0.0105},
    "anthropic": {"prompt_per_1k": 0.003, "completion_per_1k": 0.015},
    "default": {"prompt_per_1k": 0.004, "completion_per_1k": 0.012},
}


class LLMTracker:
    """
    Centralized recorder for AI invocations ensuring end-to-end observability and provenance.
    """

    @classmethod
    def estimate_cost(
        cls,
        provider: str,
        prompt_tokens: int,
        completion_tokens: int,
    ) -> float:
        """Estimates cost in USD based on provider pricing tiers."""
        rates = PROVIDER_PRICING.get(provider.lower(), PROVIDER_PRICING["default"])
        cost = (prompt_tokens / 1000.0) * rates["prompt_per_1k"] + (completion_tokens / 1000.0) * rates["completion_per_1k"]
        return round(cost, 6)

    @classmethod
    async def record_llm_call(
        cls,
        session: AsyncSession,
        model: str,
        provider: str,
        prompt_version: str,
        latency_ms: float,
        structured_output_valid: bool = True,
        trace_id: Optional[uuid.UUID] = None,
        execution_trace_id: Optional[uuid.UUID] = None,
        migration_plan_id: Optional[uuid.UUID] = None,
        migration_job_id: Optional[uuid.UUID] = None,
        model_version: Optional[str] = None,
        planner_version: str = "migraflow-planner-v2.0",
        schema_version: int = 1,
        prompt_tokens: Optional[int] = None,
        completion_tokens: Optional[int] = None,
        total_tokens: Optional[int] = None,
        status: str = "success",
        validation_error: Optional[str] = None,
        raw_prompt: Optional[str] = None,
        prompt_preview: Optional[str] = None,
        error: Optional[Exception] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> LLMCallRecord:
        """
        Records an LLM call record with sanitized preview, updates budgets, and emits an audit event.
        """
        effective_trace_id = trace_id or uuid.uuid4()
        now = datetime.now(timezone.utc)
        effective_prompt = prompt_preview or raw_prompt

        # Estimate tokens if not provided (heuristically ~4 chars per token)
        p_tokens = prompt_tokens or (len(effective_prompt) // 4 if effective_prompt else 250)
        c_tokens = completion_tokens or 150
        t_tokens = total_tokens or (p_tokens + c_tokens)

        cost_usd = cls.estimate_cost(provider, p_tokens, c_tokens)

        # Sanitize prompt preview to ensure no passwords or connection strings are stored
        sanitized_preview: Optional[str] = None
        if effective_prompt:
            masked = CredentialSanitizer.mask_credentials(effective_prompt)
            sanitized_preview = masked[:250] + ("..." if len(masked) > 250 else "")

        record = LLMCallRecord(
            id=uuid.uuid4(),
            trace_id=effective_trace_id,
            execution_trace_id=execution_trace_id,
            migration_plan_id=migration_plan_id,
            migration_job_id=migration_job_id,
            model=model,
            provider=provider,
            model_version=model_version,
            prompt_version=prompt_version,
            planner_version=planner_version,
            schema_version=schema_version,
            request_timestamp=now,
            latency_ms=round(latency_ms, 2),
            prompt_tokens=p_tokens,
            completion_tokens=c_tokens,
            total_tokens=t_tokens,
            estimated_cost_usd=cost_usd,
            status=status if not error else "failed",
            structured_output_valid=structured_output_valid,
            validation_error=validation_error,
            sanitized_prompt_preview=sanitized_preview,
            error_message=str(error) if error else None,
            created_at=now,
        )
        session.add(record)
        await session.flush()

        # Update budget if associated with a MigrationJob
        if migration_job_id:
            await ResourceBudgetManager.record_usage(
                session=session,
                job_id=migration_job_id,
                llm_calls=1,
                tokens=t_tokens,
                cost_usd=cost_usd,
            )

        # Emit audit event
        event = ExecutionEvent(
            id=uuid.uuid4(),
            event_id=uuid.uuid4(),
            migration_job_id=migration_job_id,
            migration_plan_id=migration_plan_id,
            event_type=ExecutionEventType.LLM_CALL_RECORDED.value,
            actor_type="system",
            actor_id="LLMTracker",
            timestamp=now,
            payload={
                "model": model,
                "provider": provider,
                "prompt_version": prompt_version,
                "latency_ms": latency_ms,
                "total_tokens": t_tokens,
                "estimated_cost_usd": cost_usd,
                "status": record.status,
            },
            schema_version=1,
        )
        session.add(event)
        await session.flush()

        return record

    record_call = record_llm_call

