"""
Structured Contextual Logger & Credential Guard
Standardizes execution logs correlated with migration_job_id, agent_run_id, execution_step_id, and trace_id.
Strictly redacts database passwords, connection URIs, tokens, auth headers, and raw sensitive rows.
"""

from contextvars import ContextVar
import json
import logging
from typing import Any, Dict, Optional
import uuid

from app.core.credential_sanitizer import CredentialSanitizer

# Context variables for request/task correlation
current_job_id: ContextVar[Optional[str]] = ContextVar("current_job_id", default=None)
current_agent_run_id: ContextVar[Optional[str]] = ContextVar("current_agent_run_id", default=None)
current_step_id: ContextVar[Optional[str]] = ContextVar("current_step_id", default=None)
current_trace_id: ContextVar[Optional[str]] = ContextVar("current_trace_id", default=None)


class StructuredLogger:
    """
    Structured logger wrapper that injects correlation context and redacts sensitive data.
    """

    def __init__(self, name: str):
        self._logger = logging.getLogger(name)

    @staticmethod
    def set_context(
        job_id: Optional[uuid.UUID] = None,
        migration_job_id: Optional[uuid.UUID] = None,
        agent_run_id: Optional[uuid.UUID] = None,
        step_id: Optional[uuid.UUID] = None,
        execution_step_id: Optional[uuid.UUID] = None,
        trace_id: Optional[uuid.UUID] = None,
    ):
        """Sets correlation identifiers in async context."""
        effective_job_id = migration_job_id or job_id
        effective_step_id = execution_step_id or step_id

        if effective_job_id:
            current_job_id.set(str(effective_job_id))
        if agent_run_id:
            current_agent_run_id.set(str(agent_run_id))
        if effective_step_id:
            current_step_id.set(str(effective_step_id))
        if trace_id:
            current_trace_id.set(str(trace_id))

    @staticmethod
    def clear_context():
        """Clears correlation identifiers from context."""
        current_job_id.set(None)
        current_agent_run_id.set(None)
        current_step_id.set(None)
        current_trace_id.set(None)

    @property
    def logger(self) -> logging.Logger:
        return self._logger

    def _format_payload(self, msg: str, extra: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Builds a sanitized structured dictionary containing correlation keys."""
        clean_msg = CredentialSanitizer.mask_credentials(msg)
        clean_extra = CredentialSanitizer.sanitize_structure(extra or {})

        payload: Dict[str, Any] = {
            "message": clean_msg,
            "migration_job_id": current_job_id.get(),
            "agent_run_id": current_agent_run_id.get(),
            "execution_step_id": current_step_id.get(),
            "trace_id": current_trace_id.get(),
        }
        if clean_extra:
            payload["extra"] = clean_extra
            payload["extra_data"] = clean_extra

        return {k: v for k, v in payload.items() if v is not None}

    def info(self, msg: str, extra: Optional[Dict[str, Any]] = None, **kwargs):
        payload = self._format_payload(msg, extra or kwargs.get("extra_data"))
        self._logger.info(json.dumps(payload))

    def warning(self, msg: str, extra: Optional[Dict[str, Any]] = None, **kwargs):
        payload = self._format_payload(msg, extra or kwargs.get("extra_data"))
        self._logger.warning(json.dumps(payload))

    def error(self, msg: str, extra: Optional[Dict[str, Any]] = None, **kwargs):
        payload = self._format_payload(msg, extra or kwargs.get("extra_data"))
        self._logger.error(json.dumps(payload))

    def debug(self, msg: str, extra: Optional[Dict[str, Any]] = None, **kwargs):
        payload = self._format_payload(msg, extra or kwargs.get("extra_data"))
        self._logger.debug(json.dumps(payload))


def get_structured_logger(name: str) -> StructuredLogger:
    """Factory function for StructuredLogger."""
    return StructuredLogger(name)


get_logger = get_structured_logger
set_log_context = StructuredLogger.set_context
clear_log_context = StructuredLogger.clear_context
