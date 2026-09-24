"""
Resource Budget Manager
Deterministically enforces hard operational limits on migration execution:
- Max LLM calls
- Max replans
- Max retries
- Max execution duration
- Max concurrent steps
- Max total tokens
- Max estimated AI cost (USD)
Never relies on LLMs to self-police; limits are evaluated in deterministic Python logic.
"""

from datetime import datetime, timezone
import logging
from typing import Any, Dict, Optional, Tuple
import uuid

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.state import BudgetExceededError, BudgetLimitType, ExecutionEventType
from app.modules.execution.execution_models import ExecutionEvent
from app.modules.observability.observability_models import ResourceBudget

logger = logging.getLogger(__name__)


class ResourceBudgetManager:
    """
    Durable resource budget governance and real-time consumption enforcement.
    """

    DEFAULT_LIMITS: Dict[str, Any] = {
        "max_llm_calls": 10,
        "max_replans": 3,
        "max_retries": 3,
        "max_execution_duration_seconds": 3600,
        "max_concurrent_steps": 4,
        "max_tokens": 100000,
        "max_cost_usd": 5.00,
    }

    @classmethod
    async def get_or_create_budget(
        cls,
        session: AsyncSession,
        job_id: Optional[uuid.UUID] = None,
        migration_job_id: Optional[uuid.UUID] = None,
        user_id: Optional[uuid.UUID] = None,
        custom_limits: Optional[Dict[str, Any]] = None,
        **kwargs,
    ) -> ResourceBudget:
        """Fetches existing budget or creates a new budget with default/custom limits."""
        effective_job_id = migration_job_id or job_id
        if effective_job_id is None:
            raise ValueError("job_id or migration_job_id must be provided")

        stmt = select(ResourceBudget).where(ResourceBudget.migration_job_id == effective_job_id)
        res = await session.execute(stmt)
        budget = res.scalar_one_or_none()
        if budget:
            return budget

        limits = dict(cls.DEFAULT_LIMITS)
        if custom_limits:
            limits.update(custom_limits)
        for k, v in kwargs.items():
            if k in limits:
                limits[k] = v

        now = datetime.now(timezone.utc)
        budget = ResourceBudget(
            id=uuid.uuid4(),
            migration_job_id=effective_job_id,
            user_id=user_id,
            max_llm_calls=limits["max_llm_calls"],
            max_replans=limits["max_replans"],
            max_retries=limits["max_retries"],
            max_execution_duration_seconds=limits["max_execution_duration_seconds"],
            max_concurrent_steps=limits["max_concurrent_steps"],
            max_tokens=limits["max_tokens"],
            max_cost_usd=limits["max_cost_usd"],
            current_llm_calls=0,
            current_replans=0,
            current_retries=0,
            current_tokens=0,
            current_cost_usd=0.0,
            is_exceeded=False,
            created_at=now,
            updated_at=now,
        )
        session.add(budget)
        await session.flush()
        return budget

    @classmethod
    async def record_usage(
        cls,
        session: AsyncSession,
        job_id: Optional[uuid.UUID] = None,
        migration_job_id: Optional[uuid.UUID] = None,
        llm_calls: int = 0,
        tokens: int = 0,
        cost_usd: float = 0.0,
        replans: int = 0,
        retries: int = 0,
        duration_seconds: int = 0,
    ) -> ResourceBudget:
        """
        Records resource usage increments and deterministically validates limits.
        Raises BudgetExceededError if any threshold is breached.
        """
        effective_job_id = migration_job_id or job_id
        if effective_job_id is None:
            raise ValueError("job_id or migration_job_id must be provided")

        budget = await cls.get_or_create_budget(session, job_id=effective_job_id)

        # Increment counters
        budget.current_llm_calls += llm_calls
        budget.current_tokens += tokens
        budget.current_cost_usd = round(budget.current_cost_usd + cost_usd, 4)
        budget.current_replans += replans
        budget.current_retries += retries
        budget.updated_at = datetime.now(timezone.utc)

        # Deterministic checks
        breach_type: Optional[str] = None
        limit_val: Any = None
        curr_val: Any = None

        if budget.current_llm_calls > budget.max_llm_calls:
            breach_type = BudgetLimitType.MAX_LLM_CALLS.value
            limit_val = budget.max_llm_calls
            curr_val = budget.current_llm_calls
        elif budget.current_replans > budget.max_replans:
            breach_type = BudgetLimitType.MAX_REPLANS.value
            limit_val = budget.max_replans
            curr_val = budget.current_replans
        elif budget.current_retries > budget.max_retries:
            breach_type = BudgetLimitType.MAX_RETRIES.value
            limit_val = budget.max_retries
            curr_val = budget.current_retries
        elif budget.current_tokens > budget.max_tokens:
            breach_type = BudgetLimitType.MAX_TOKENS.value
            limit_val = budget.max_tokens
            curr_val = budget.current_tokens
        elif budget.current_cost_usd > budget.max_cost_usd:
            breach_type = BudgetLimitType.MAX_COST_USD.value
            limit_val = budget.max_cost_usd
            curr_val = budget.current_cost_usd
        elif duration_seconds > 0 and duration_seconds > budget.max_execution_duration_seconds:
            breach_type = BudgetLimitType.MAX_EXECUTION_DURATION_SECONDS.value
            limit_val = budget.max_execution_duration_seconds
            curr_val = duration_seconds

        if breach_type:
            budget.is_exceeded = True
            budget.exceeded_limit_type = breach_type

            # Emit audit event
            event = ExecutionEvent(
                id=uuid.uuid4(),
                event_id=uuid.uuid4(),
                migration_job_id=job_id,
                event_type=ExecutionEventType.BUDGET_EXCEEDED.value,
                actor_type="system",
                actor_id="ResourceBudgetManager",
                timestamp=datetime.now(timezone.utc),
                payload={
                    "limit_type": breach_type,
                    "limit_value": limit_val,
                    "current_value": curr_val,
                },
                schema_version=1,
            )
            session.add(event)
            await session.commit()

            logger.error(
                f"[ResourceBudgetManager] Budget exceeded for job '{job_id}': "
                f"{breach_type} (limit={limit_val}, current={curr_val})"
            )
            raise BudgetExceededError(
                limit_type=breach_type,
                limit_value=limit_val,
                current_value=curr_val,
                entity_id=str(job_id),
            )

        await session.flush()
        return budget

    @classmethod
    async def get_budget(cls, session: AsyncSession, job_id: uuid.UUID) -> Optional[ResourceBudget]:
        """Retrieves budget details for a specific migration job."""
        stmt = select(ResourceBudget).where(ResourceBudget.migration_job_id == job_id)
        res = await session.execute(stmt)
        return res.scalar_one_or_none()
