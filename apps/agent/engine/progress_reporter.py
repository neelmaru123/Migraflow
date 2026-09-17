import json
import logging
import time
import urllib.error
import urllib.request
from typing import Optional

logger = logging.getLogger("docker-agent-execution")


class ProgressReporter:
    """Sends HTTP progress reports to Control Plane POST /api/v1/executions/{id}/progress."""

    @staticmethod
    def report(
        backend_url: str,
        agent_token: str,
        job_id: str,
        status: str = "running",
        progress: float = 0.0,
        processed_rows: int = 0,
        successful_rows: int = 0,
        failed_rows: int = 0,
        skipped_rows: int = 0,
        total_rows: int = 0,
        current_table: Optional[str] = None,
        current_stage: Optional[str] = None,
        error_message: Optional[str] = None,
    ):
        if not backend_url or "testserver" in backend_url:
            return

        url = f"{backend_url.rstrip('/')}/api/v1/executions/{job_id}/progress"
        payload = json.dumps({
            "status": status,
            "progress": progress,
            "processed_rows": processed_rows,
            "successful_rows": successful_rows,
            "failed_rows": failed_rows,
            "skipped_rows": skipped_rows,
            "total_rows": total_rows,
            "current_table": current_table,
            "current_stage": current_stage,
            "error_message": error_message,
        }).encode("utf-8")

        req = urllib.request.Request(
            url,
            data=payload,
            headers={
                "Content-Type": "application/json",
                "X-Agent-Token": agent_token,
            },
            method="POST",
        )

        is_terminal = status in ("completed", "dry_run_completed", "failed")
        max_attempts = 4 if is_terminal else 2

        for attempt in range(1, max_attempts + 1):
            try:
                with urllib.request.urlopen(req, timeout=15) as resp:
                    if resp.status in (200, 201):
                        try:
                            data = json.loads(resp.read().decode("utf-8", errors="ignore"))
                            return data
                        except Exception:
                            return {"status": status}
                    logger.warning(
                        f"Progress report attempt {attempt}/{max_attempts} returned HTTP status {resp.status} for job '{job_id}'."
                    )
            except urllib.error.HTTPError as http_err:
                logger.error(
                    f"Progress report attempt {attempt}/{max_attempts} failed for job '{job_id}' with HTTP {http_err.code}: {http_err.reason}"
                )
                if http_err.code in (401, 403, 404):
                    break  # Unrecoverable auth/not found error
            except Exception as exc:
                logger.warning(
                    f"Progress report attempt {attempt}/{max_attempts} failed for job '{job_id}': {exc}"
                )

            if attempt < max_attempts:
                time.sleep(0.5 * (2 ** attempt))
