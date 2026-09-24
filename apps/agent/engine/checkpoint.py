"""
Resumable checkpointing module for agent execution engine.
Maintains local disk checkpoints under CHECKPOINT_DIR (/tmp) and supports
authoritative control-plane database checkpoint synchronization.
"""

import json
import logging
import os
import urllib.request
from datetime import datetime, timezone
from typing import Any, Dict, Optional

logger = logging.getLogger("docker-agent-execution")

CHECKPOINT_DIR = os.getenv("CHECKPOINT_DIR", "/tmp")


class CheckpointManager:
    """Manages resumable checkpointing per table and per source to prevent restarting from zero on agent crash."""

    @staticmethod
    def get_checkpoint_path(
        job_id: str,
        table_name: str,
        source_identifier: Optional[str] = None,
        source_table: Optional[str] = None,
    ) -> str:
        tmp_dir = os.getenv("CHECKPOINT_DIR", "/tmp")
        os.makedirs(tmp_dir, exist_ok=True)
        if source_identifier or source_table:
            src_id = (source_identifier or "default").replace("/", "_").replace("\\", "_")
            src_tbl = (source_table or "default").replace("/", "_").replace("\\", "_")
            return os.path.join(tmp_dir, f"checkpoint_{job_id}_{table_name}_{src_id}_{src_tbl}.json")
        return os.path.join(tmp_dir, f"checkpoint_{job_id}_{table_name}.json")

    @classmethod
    def get_last_offset(
        cls,
        job_id: str,
        table_name: str,
        source_identifier: Optional[str] = None,
        source_table: Optional[str] = None,
        backend_url: Optional[str] = None,
        agent_token: Optional[str] = None,
        step_id: Optional[str] = None,
    ) -> int:
        """
        Retrieves the last processed row offset.
        Queries the authoritative control plane if backend_url and step_id are provided,
        falling back to local /tmp checkpoint file.
        """
        # 1. Authoritative Control Plane Lookup (if step_id provided)
        if backend_url and agent_token and step_id:
            try:
                url = f"{backend_url.rstrip('/')}/api/v1/execution/steps/{step_id}/checkpoints"
                req = urllib.request.Request(
                    url,
                    headers={"X-Agent-Token": agent_token.strip()},
                    method="GET",
                )
                with urllib.request.urlopen(req, timeout=5.0) as resp:
                    checkpoints = json.loads(resp.read().decode("utf-8"))
                    if isinstance(checkpoints, list):
                        for cp in checkpoints:
                            if (
                                cp.get("target_table") == table_name
                                and (not source_table or cp.get("source_table") == source_table)
                                and (not source_identifier or cp.get("source_identifier") == source_identifier)
                            ):
                                return int(cp.get("cursor_offset", 0))
            except Exception as net_exc:
                logger.debug(f"Could not fetch remote checkpoint for step '{step_id}': {net_exc}")

        # 2. Local File System Fallback
        path = cls.get_checkpoint_path(job_id, table_name, source_identifier, source_table)
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    return data.get("last_offset", 0)
            except Exception as exc:
                logger.warning(f"Could not read checkpoint file '{path}': {exc}")
                return 0

        # Backward compatibility for legacy checkpoint naming without source suffix
        legacy_path = os.path.join(os.getenv("CHECKPOINT_DIR", "/tmp"), f"checkpoint_{job_id}_{table_name}.json")
        if os.path.exists(legacy_path):
            try:
                with open(legacy_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    return data.get("last_offset", 0)
            except Exception as exc:
                logger.warning(f"Could not read legacy checkpoint file '{legacy_path}': {exc}")
                return 0

        return 0

    @classmethod
    def save_checkpoint(
        cls,
        job_id: str,
        table_name: str,
        offset: int,
        rows_processed: int,
        source_identifier: Optional[str] = None,
        source_table: Optional[str] = None,
        backend_url: Optional[str] = None,
        agent_token: Optional[str] = None,
        step_id: Optional[str] = None,
        source_position: Optional[Dict[str, Any]] = None,
    ):
        """
        Saves checkpoint locally to disk and syncs with authoritative control-plane database.
        """
        # 1. Save Local Checkpoint file
        path = cls.get_checkpoint_path(job_id, table_name, source_identifier, source_table)
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump({
                    "job_id": job_id,
                    "table_name": table_name,
                    "source_identifier": source_identifier,
                    "source_table": source_table,
                    "last_offset": offset,
                    "rows_processed": rows_processed,
                    "updated_at": datetime.now(timezone.utc).isoformat()
                }, f, indent=2)
        except Exception as exc:
            logger.warning(f"Could not write local checkpoint for table '{table_name}': {exc}")

        # 2. Sync with Control Plane (if step_id provided)
        if backend_url and agent_token and step_id:
            try:
                url = f"{backend_url.rstrip('/')}/api/v1/execution/steps/{step_id}/checkpoint"
                payload = json.dumps({
                    "source_identifier": source_identifier or "default",
                    "source_table": source_table or table_name,
                    "target_table": table_name,
                    "cursor_offset": offset,
                    "rows_processed": rows_processed,
                    "source_position": source_position,
                }).encode("utf-8")
                req = urllib.request.Request(
                    url,
                    data=payload,
                    headers={
                        "Content-Type": "application/json",
                        "X-Agent-Token": agent_token.strip(),
                    },
                    method="POST",
                )
                with urllib.request.urlopen(req, timeout=5.0) as _:
                    pass
            except Exception as remote_exc:
                logger.debug(f"Could not sync remote checkpoint for step '{step_id}': {remote_exc}")

    @classmethod
    def clear_job_checkpoints(cls, job_id: str):
        """Removes all checkpoint JSON files for a completed job."""
        tmp_dir = os.getenv("CHECKPOINT_DIR", "/tmp")
        if not os.path.exists(tmp_dir):
            return
        prefix = f"checkpoint_{job_id}_"
        try:
            for fname in os.listdir(tmp_dir):
                if fname.startswith(prefix) and fname.endswith(".json"):
                    fpath = os.path.join(tmp_dir, fname)
                    try:
                        os.remove(fpath)
                        logger.info(f"Cleaned up checkpoint file: {fname}")
                    except Exception as err:
                        logger.warning(f"Could not remove checkpoint file '{fpath}': {err}")
        except Exception as exc:
            logger.warning(f"Error scanning checkpoint directory '{tmp_dir}': {exc}")

    @classmethod
    def clear_table_checkpoints(cls, job_id: str, table_name: str):
        """
        Removes all per-source checkpoint files for a single target table.
        Used when a multi-source staging file was found missing/stale on resume.
        """
        tmp_dir = os.getenv("CHECKPOINT_DIR", "/tmp")
        if not os.path.exists(tmp_dir):
            return
        prefix = f"checkpoint_{job_id}_{table_name}_"
        try:
            for fname in os.listdir(tmp_dir):
                if fname.startswith(prefix) and fname.endswith(".json"):
                    fpath = os.path.join(tmp_dir, fname)
                    try:
                        os.remove(fpath)
                        logger.info(f"Reset stale checkpoint file for re-staging: {fname}")
                    except Exception as err:
                        logger.warning(f"Could not remove checkpoint file '{fpath}': {err}")
        except Exception as exc:
            logger.warning(f"Error scanning checkpoint directory '{tmp_dir}': {exc}")
