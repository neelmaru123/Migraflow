"""
Timeout Policy & Operational Guard
Defines explicit timeout policies across all platform operations to prevent infinite hangs:
- LLM calls
- DB connections
- Metadata inspection
- Migration execution steps
- Verification suite
- Whole jobs
- Agent communications
"""

import asyncio
from datetime import datetime, timezone
import logging
from typing import Any, Awaitable, Optional, TypeVar
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.state import ExecutionEventType, ExecutionTimeoutError
from app.modules.execution.execution_models import ExecutionEvent

logger = logging.getLogger(__name__)

T = TypeVar("T")


class TimeoutPolicy:
    """Centralized timeout constants in seconds."""
    LLM_CALL: float = 60.0
    DB_CONNECTION: float = 10.0
    METADATA_INSPECTION: float = 120.0
    MIGRATION_STEP: float = 600.0
    VERIFICATION: float = 180.0
    JOB: float = 7200.0
    AGENT_COMMUNICATION: float = 30.0


async def execute_with_timeout(
    coro: Awaitable[T],
    timeout_seconds: float,
    operation_type: str,
    entity_id: Optional[str] = None,
    session: Optional[AsyncSession] = None,
    job_id: Optional[uuid.UUID] = None,
) -> T:
    """
    Executes an async coroutine with a strict timeout.
    If the timeout expires, an audit event is optionally recorded and an ExecutionTimeoutError is raised.
    """
    try:
        return await asyncio.wait_for(coro, timeout=timeout_seconds)
    except asyncio.TimeoutError:
        logger.error(
            f"[TimeoutPolicy] Operation '{operation_type}' timed out after {timeout_seconds}s"
            + (f" on entity '{entity_id}'" if entity_id else "")
        )
        if session and job_id:
            try:
                event = ExecutionEvent(
                    id=uuid.uuid4(),
                    event_id=uuid.uuid4(),
                    migration_job_id=job_id,
                    event_type=ExecutionEventType.TIMEOUT_TRIGGERED.value,
                    actor_type="system",
                    actor_id="TimeoutPolicy",
                    timestamp=datetime.now(timezone.utc),
                    payload={
                        "operation_type": operation_type,
                        "timeout_seconds": timeout_seconds,
                        "entity_id": entity_id,
                    },
                    schema_version=1,
                )
                session.add(event)
                await session.commit()
            except Exception as e:
                logger.warning(f"Could not persist timeout event: {e}")

        raise ExecutionTimeoutError(
            operation_type=operation_type,
            timeout_seconds=timeout_seconds,
            entity_id=entity_id,
        )
