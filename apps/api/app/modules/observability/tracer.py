"""
Execution Tracer & OpenTelemetry Bridge
Implements hierarchical execution tracing:
AgentRun -> ExecutionStep -> ToolRun / LLMRun / Verification.
Scrubs all metadata through CredentialSanitizer before persistence.
"""

from contextlib import asynccontextmanager
from datetime import datetime, timezone
import logging
from typing import Any, AsyncGenerator, Dict, List, Optional
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.credential_sanitizer import CredentialSanitizer
from app.core.state import ExecutionEventType, TraceOperationType, TraceStatus
from app.modules.execution.execution_models import ExecutionEvent
from app.modules.observability.observability_models import ExecutionTrace
from app.modules.observability.observability_schemas import TraceTreeResponse

logger = logging.getLogger(__name__)


class ExecutionTracer:
    """
    Manages creation, lifecycle, and hierarchical tree assembly of execution traces.
    """

    @classmethod
    async def start_span(
        cls,
        session: AsyncSession,
        name: str,
        operation_type: str,
        trace_id: Optional[uuid.UUID] = None,
        parent_run_id: Optional[uuid.UUID] = None,
        migration_job_id: Optional[uuid.UUID] = None,
        agent_run_id: Optional[uuid.UUID] = None,
        execution_step_id: Optional[uuid.UUID] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> ExecutionTrace:
        """Starts a new trace span, inheriting or generating a root trace_id."""
        span_id = uuid.uuid4()
        effective_trace_id = trace_id
        if effective_trace_id is None:
            if parent_run_id:
                parent_span = await session.get(ExecutionTrace, parent_run_id)
                if parent_span:
                    effective_trace_id = parent_span.trace_id
            if effective_trace_id is None:
                effective_trace_id = span_id

        clean_metadata = CredentialSanitizer.sanitize_structure(metadata or {})

        now = datetime.now(timezone.utc)
        span = ExecutionTrace(
            id=span_id,
            trace_id=effective_trace_id,
            parent_run_id=parent_run_id,
            migration_job_id=migration_job_id,
            agent_run_id=agent_run_id,
            execution_step_id=execution_step_id,
            operation_type=operation_type,
            name=name,
            status=TraceStatus.RUNNING.value,
            started_at=now,
            metadata_snapshot=clean_metadata,
            created_at=now,
        )
        session.add(span)
        await session.flush()
        return span

    @classmethod
    async def finish_span(
        cls,
        session: AsyncSession,
        span: Optional[ExecutionTrace] = None,
        trace_id: Optional[uuid.UUID] = None,
        status: str = TraceStatus.COMPLETED.value,
        error: Optional[Exception] = None,
        metadata_updates: Optional[Dict[str, Any]] = None,
    ) -> Optional[ExecutionTrace]:
        """Finishes an execution trace span, calculating duration and recording errors."""
        target_span = span
        if target_span is None and trace_id is not None:
            target_span = await session.get(ExecutionTrace, trace_id)
        if target_span is None:
            return None

        now = datetime.now(timezone.utc)
        target_span.finished_at = now
        target_span.status = status

        if target_span.started_at:
            delta = now - target_span.started_at
            target_span.duration_ms = round(delta.total_seconds() * 1000.0, 2)

        if error:
            target_span.error_type = type(error).__name__
            target_span.error_message = CredentialSanitizer.mask_credentials(str(error))
            if target_span.status == TraceStatus.COMPLETED.value:
                target_span.status = TraceStatus.FAILED.value

        if metadata_updates:
            clean_updates = CredentialSanitizer.sanitize_structure(metadata_updates)
            updated_meta = dict(target_span.metadata_snapshot or {})
            updated_meta.update(clean_updates)
            target_span.metadata_snapshot = updated_meta

        await session.flush()
        return target_span

    @classmethod
    @asynccontextmanager
    async def span(
        cls,
        session: AsyncSession,
        name: str,
        operation_type: str,
        trace_id: Optional[uuid.UUID] = None,
        parent_run_id: Optional[uuid.UUID] = None,
        migration_job_id: Optional[uuid.UUID] = None,
        agent_run_id: Optional[uuid.UUID] = None,
        execution_step_id: Optional[uuid.UUID] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> AsyncGenerator[ExecutionTrace, None]:
        """
        Async context manager providing ergonomic span lifecycle wrapping.
        Example:
            async with ExecutionTracer.span(session, "load:customers", TraceOperationType.EXECUTION_STEP) as span:
                await do_work()
        """
        span_obj = await cls.start_span(
            session=session,
            name=name,
            operation_type=operation_type,
            trace_id=trace_id,
            parent_run_id=parent_run_id,
            migration_job_id=migration_job_id,
            agent_run_id=agent_run_id,
            execution_step_id=execution_step_id,
            metadata=metadata,
        )
        try:
            yield span_obj
            await cls.finish_span(session, span_obj, status=TraceStatus.COMPLETED.value)
        except Exception as exc:
            await cls.finish_span(session, span_obj, status=TraceStatus.FAILED.value, error=exc)
            raise

    @classmethod
    async def get_trace_tree(cls, session: AsyncSession, trace_id: uuid.UUID) -> Optional[TraceTreeResponse]:
        """
        Fetches all spans sharing a trace_id and reconstructs the full hierarchy:
        AgentRun -> ExecutionStep -> ToolRun / LLMRun.
        """
        stmt = (
            select(ExecutionTrace)
            .where(ExecutionTrace.trace_id == trace_id)
            .order_by(ExecutionTrace.started_at.asc())
        )
        res = await session.execute(stmt)
        all_spans = list(res.scalars().all())
        if not all_spans:
            return None

        # Map span ID to its node and find roots (spans with no parent_run_id or parent not in this trace)
        span_map: Dict[uuid.UUID, Dict[str, Any]] = {}
        for s in all_spans:
            span_map[s.id] = {
                "span": s,
                "children": [],
                "otel_span": s.to_otel_span(),
            }

        root_nodes = []
        for s in all_spans:
            if s.parent_run_id and s.parent_run_id in span_map:
                span_map[s.parent_run_id]["children"].append(span_map[s.id])
            else:
                root_nodes.append(span_map[s.id])

        if not root_nodes:
            root_nodes = [span_map[all_spans[0].id]]

        def _to_tree(node: Dict[str, Any]) -> TraceTreeResponse:
            return TraceTreeResponse(
                span=node["span"],
                children=[_to_tree(c) for c in node["children"]],
                otel_span=node["otel_span"],
            )

        return _to_tree(root_nodes[0])
