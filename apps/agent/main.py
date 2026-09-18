import atexit
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import logging
import os
import re
import signal
import socket
import sys
import time
from typing import Any, Dict, List, Optional
import urllib.error
import urllib.parse
import urllib.request

from metadata_engine import AgentMetadataEngine

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("docker-agent")


def sync_metadata_snapshots(backend_url: str, agent_token: str):
    """
    Introspects local schemas for all configured healthy databases and posts snapshot payloads
    to backend `/api/v1/metadata/sync`.
    """
    url = f"{backend_url.rstrip('/')}/api/v1/metadata/sync"
    headers = {
        "Content-Type": "application/json",
        "X-Agent-Token": agent_token,
    }

    seen_identifiers = set()
    for k, v in os.environ.items():
        if (k.startswith("SRC_") or k.startswith("DEST_") or k in ("SOURCE_DB_URL", "DEST_DB_URL")) and (
            "URL" in k or "URI" in k
        ):
            ident = extract_identifier_from_env_key(k)
            if ident in seen_identifiers:
                continue
            seen_identifiers.add(ident)

            logger.info(f"Executing metadata schema introspection for database '{ident}'...")
            snapshot_data = AgentMetadataEngine.introspect_database(ident, v)
            if not snapshot_data:
                continue

            payload = json.dumps(snapshot_data, default=str).encode("utf-8")
            req = urllib.request.Request(url, data=payload, headers=headers, method="POST")
            try:
                with urllib.request.urlopen(req, timeout=15.0) as resp:
                    if resp.status in (200, 201):
                        res_body = json.loads(resp.read().decode("utf-8"))
                        logger.info(
                            f"Metadata snapshot v{res_body.get('version')} successfully synced for database '{ident}' "
                            f"(Snapshot ID: {res_body.get('id')}, Tables: {res_body.get('total_tables')}, Columns: {res_body.get('total_columns')})."
                        )
            except urllib.error.HTTPError as err:
                logger.error(f"Metadata sync failed for '{ident}' with HTTP status {err.code}: {err.reason}")
            except Exception as exc:
                logger.error(f"Could not transmit metadata snapshot for '{ident}' to {url}: {exc}")


def _mask_url(url_val: str) -> str:
    """Masks password in connection URLs for safe logging, highlighting unfilled placeholders."""
    if "<" in url_val and ">" in url_val:
        return f"{url_val} [WARNING: Unfilled credential placeholder detected]"
    return re.sub(r":([^:@]+)@", r":***@", url_val)


def extract_identifier_from_env_key(env_key: str) -> str:
    """
    Extracts the clean data source identifier from environment variable name.
    e.g. 'SRC_PG_PRIMARY_URL' -> 'pg_primary'
    e.g. 'DEST_MYSQL_WAREHOUSE_URL' -> 'mysql_warehouse'
    e.g. 'SOURCE_DB_URL' -> 'source_db'
    """
    k = env_key.upper()
    if k.endswith("_URL"):
        k = k[:-4]
    elif k.endswith("_URI"):
        k = k[:-4]

    if k in ("SOURCE_DB", "SRC"):
        return "source_db"
    if k in ("DEST_DB", "DEST", "DST"):
        return "dest_db"

    if k.startswith("SRC_"):
        k = k[4:]
    elif k.startswith("DEST_"):
        k = k[5:]
    elif k.startswith("DST_"):
        k = k[4:]
    return k.lower()


def test_database_connection(identifier: str, url_val: str) -> Dict[str, Any]:
    """
    Tests network reachability, socket connection, and credential completeness for a database URL.
    Returns structured diagnostic health result with actionable troubleshooting advice.
    """
    if not url_val or not url_val.strip():
        return {
            "identifier": identifier,
            "is_healthy": False,
            "error_type": "MissingConfiguration",
            "error_message": f"Connection URL for '{identifier}' is empty.",
            "latency_ms": 0.0,
        }

    # 1. Detect unfilled placeholders (e.g. <password>, <username>, <database_name>)
    if "<" in url_val and ">" in url_val:
        return {
            "identifier": identifier,
            "is_healthy": False,
            "error_type": "UnfilledPlaceholder",
            "error_message": (
                f"Unfilled credential placeholder detected in database URL for '{identifier}'. "
                "Please replace placeholders (e.g. <password>, <username>) with your actual database credentials."
            ),
            "latency_ms": 0.0,
        }

    # 2. Parse URL components
    try:
        parsed = urllib.parse.urlparse(url_val)
        scheme = parsed.scheme.lower() if parsed.scheme else "unknown"
        hostname = parsed.hostname or "localhost"
        db_name = parsed.path.lstrip("/") if parsed.path else None

        default_ports = {
            "postgresql": 5432,
            "postgres": 5432,
            "mysql": 3306,
            "mariadb": 3306,
            "mongodb": 27017,
            "mongodb+srv": 27017,
        }
        port = parsed.port or default_ports.get(scheme, 5432)
    except Exception as parse_err:
        return {
            "identifier": identifier,
            "is_healthy": False,
            "error_type": "InvalidUrlFormat",
            "error_message": f"Could not parse database URL: {parse_err}",
            "latency_ms": 0.0,
        }

    # 3. Test TCP socket reachability with timeout
    t0 = time.perf_counter()
    try:
        sock = socket.create_connection((hostname, port), timeout=3.5)
        sock.close()
        latency_ms = round((time.perf_counter() - t0) * 1000, 2)
        return {
            "identifier": identifier,
            "is_healthy": True,
            "error_type": None,
            "error_message": None,
            "latency_ms": latency_ms,
            "database_name": db_name,
        }
    except (socket.timeout, TimeoutError):
        return {
            "identifier": identifier,
            "is_healthy": False,
            "error_type": "TimeoutError",
            "error_message": f"Connection timed out reaching database host '{hostname}:{port}'.",
            "latency_ms": 0.0,
        }
    except ConnectionRefusedError:
        msg = f"Connection refused on {hostname}:{port}. Ensure your database server is running."
        if hostname in ("localhost", "127.0.0.1"):
            msg += (
                " Note: Inside a Docker container, 'localhost' refers to the container itself. "
                "Use 'host.docker.internal' instead to connect to a database on your host machine."
            )
        return {
            "identifier": identifier,
            "is_healthy": False,
            "error_type": "ConnectionRefused",
            "error_message": msg,
            "latency_ms": 0.0,
        }
    except socket.gaierror as dns_err:
        return {
            "identifier": identifier,
            "is_healthy": False,
            "error_type": "DnsResolutionFailed",
            "error_message": f"Could not resolve host '{hostname}': {dns_err}.",
            "latency_ms": 0.0,
        }
    except Exception as exc:
        return {
            "identifier": identifier,
            "is_healthy": False,
            "error_type": "NetworkError",
            "error_message": f"Could not connect to {hostname}:{port}: {exc}",
            "latency_ms": 0.0,
        }


def collect_data_sources_health() -> List[Dict[str, Any]]:
    """
    Scans environment for all configured database connection URLs and tests their health concurrently.
    Uses ThreadPoolExecutor to bound total execution time to ≤ 3.5s regardless of database count.
    Returns list of DataSourceHealthReport dicts.
    """
    items_to_test: List[tuple[str, str]] = []
    seen_identifiers = set()
    seen_urls = set()

    # Sort env keys so specific ones (e.g. DEST_DST_DB_2_URL) come before generic ones (DEST_DB_URL)
    env_keys = sorted(
        os.environ.keys(),
        key=lambda k: (k in ("SOURCE_DB_URL", "DEST_DB_URL", "SOURCE_DB_URI", "DEST_DB_URI"), len(k)),
    )

    for k in env_keys:
        v = os.environ[k]
        if (k.startswith("SRC_") or k.startswith("DEST_") or k.startswith("DST_") or k in ("SOURCE_DB_URL", "DEST_DB_URL")) and (
            "URL" in k or "URI" in k
        ):
            raw_id = extract_identifier_from_env_key(k)
            norm_url = v.strip()
            if raw_id in seen_identifiers or (norm_url and norm_url in seen_urls):
                continue
            seen_identifiers.add(raw_id)
            if norm_url:
                seen_urls.add(norm_url)
            items_to_test.append((raw_id, v))

    if not items_to_test:
        return []

    reports: List[Dict[str, Any]] = []
    # Run socket health checks concurrently
    max_workers = min(len(items_to_test), 10)
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_id = {
            executor.submit(test_database_connection, ident, url): ident
            for ident, url in items_to_test
        }
        for future in as_completed(future_to_id):
            try:
                report = future.result()
                reports.append(report)
            except Exception as exc:
                ident = future_to_id[future]
                reports.append({
                    "identifier": ident,
                    "is_healthy": False,
                    "error_type": "HealthCheckFailed",
                    "error_message": f"Unexpected health check error: {exc}",
                    "latency_ms": 0.0,
                })

    return reports


def send_heartbeat(
    backend_url: str,
    agent_token: str,
    version: str,
    override_status: Optional[str] = None,
    error_message: Optional[str] = None,
    error_category: Optional[str] = None,
) -> Optional[str]:
    """
    Sends periodic heartbeat ping, diagnostics, or fatal stopping error to backend API.
    Computes status ('online', 'degraded', 'error', or 'offline') based on health checks or override.
    Returns the backend control directive string if one was issued (e.g. 'ENTER_IDLE_MODE',
    'RESUME_ACTIVE_MODE', 'SHUTDOWN'), or None if no directive was given.
    """
    url = f"{backend_url.rstrip('/')}/api/v1/agents/heartbeat"
    headers = {
        "Content-Type": "application/json",
        "X-Agent-Token": agent_token,
    }

    if override_status in ("offline", "error"):
        ds_reports = None
        computed_status = override_status
    else:
        # Collect latest connection diagnostics for all configured local databases concurrently
        ds_reports = collect_data_sources_health()
        if override_status:
            computed_status = override_status
        elif ds_reports:
            # If any configured DB is failing, mark agent status as 'degraded'
            all_healthy = all(r.get("is_healthy", False) for r in ds_reports)
            computed_status = "online" if all_healthy else "degraded"
        else:
            computed_status = "online"

    payload_data: Dict[str, Any] = {
        "status": computed_status,
        "version": version,
        "data_sources": ds_reports if ds_reports else None,
        "error_message": error_message,
        "error_category": error_category,
    }
    payload = json.dumps(payload_data).encode("utf-8")

    req = urllib.request.Request(url, data=payload, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=5.0) as resp:
            if resp.status == 200:
                body = json.loads(resp.read().decode("utf-8"))
                action = body.get("action")  # Optional backend control directive
                if computed_status == "offline":
                    logger.info(f"Offline status synced with backend (Agent ID: {body.get('id')}).")
                    return action  # None for offline

                healthy_count = sum(1 for r in (ds_reports or []) if r.get("is_healthy"))
                failing_count = len(ds_reports or []) - healthy_count
                logger.info(
                    f"Heartbeat synced [{computed_status.upper()}] (Agent ID: {body.get('id')}). "
                    f"Databases: {healthy_count} healthy, {failing_count} failing."
                )
                for r in (ds_reports or []):
                    if not r.get("is_healthy"):
                        logger.warning(f"  [!] DataSource '{r.get('identifier')}' unreachable: {r.get('error_message')}")

                if action:
                    logger.info(f"Backend directive received: [{action}] — {body.get('action_reason', '')}")
                return action
    except urllib.error.HTTPError as err:
        logger.error(f"Agent heartbeat failed with HTTP status {err.code}: {err.reason}")
        raise
    except Exception as exc:
        logger.error(f"Could not connect to backend at {url}: {exc}")
        raise

    return None


def report_fatal_error_and_exit(
    backend_url: str,
    agent_token: str,
    version: str,
    error_message: str,
    error_category: str = "FATAL_ERROR",
    exit_code: int = 1,
):
    """
    Transmits an emergency fatal error notification to the backend so the user
    can see the exact failure reason in the Web UI, then terminates the container process.
    """
    logger.error(f"[FATAL AGENT ERROR - {error_category}]: {error_message}")
    agent_id = os.getenv("AGENT_ID")

    # 1. Try authenticated heartbeat with error payload first
    reported = False
    if agent_token and backend_url:
        try:
            send_heartbeat(
                backend_url=backend_url,
                agent_token=agent_token,
                version=version,
                override_status="error",
                error_message=error_message,
                error_category=error_category,
            )
            reported = True
            logger.info("Fatal stopping error successfully synced to backend for UI presentation.")
        except Exception as sync_err:
            logger.debug(f"Authenticated heartbeat error reporting failed: {sync_err}")

    # 2. Fallback to emergency /fatal-error endpoint if unauthenticated (e.g. token rejected)
    if not reported and agent_id and backend_url:
        try:
            fatal_url = f"{backend_url.rstrip('/')}/api/v1/agents/fatal-error"
            fatal_payload = json.dumps({
                "error_message": error_message,
                "error_category": error_category,
            }).encode("utf-8")
            fatal_req = urllib.request.Request(
                fatal_url,
                data=fatal_payload,
                headers={"Content-Type": "application/json", "X-Agent-ID": agent_id},
                method="POST",
            )
            with urllib.request.urlopen(fatal_req, timeout=5.0) as f_resp:
                if f_resp.status in (200, 201):
                    reported = True
                    logger.info("Emergency fatal stopping error successfully synced to backend for UI presentation.")
        except Exception as f_err:
            logger.warning(f"Could not transmit emergency fatal error to backend: {f_err}")

    logger.info(f"Docker Agent terminating with exit code {exit_code}.")
    os._exit(exit_code)


def log_configured_databases():
    """Scans and logs all configured source and destination database environments."""
    src_configs = {}
    dest_configs = {}

    for k, v in os.environ.items():
        if k.startswith("SRC_") or k == "SOURCE_DB_URL":
            src_configs[k] = _mask_url(v) if "URL" in k or "PASSWORD" in k else v
        elif k.startswith("DEST_") or k == "DEST_DB_URL":
            dest_configs[k] = _mask_url(v) if "URL" in k or "PASSWORD" in k else v

    if src_configs:
        logger.info("Configured Source Databases detected:")
        for k, v in src_configs.items():
            logger.info(f"  - {k}: {v}")
    else:
        logger.warning("No Source Database environment variables detected.")

    if dest_configs:
        logger.info("Configured Destination Databases detected:")
        for k, v in dest_configs.items():
            logger.info(f"  - {k}: {v}")
    else:
        logger.warning("No Destination Database environment variables detected.")


def graceful_shutdown(backend_url: str, agent_token: str, version: str):
    """Notifies the backend that the agent process is stopping."""
    logger.info("Sending offline notification to backend before shutdown...")
    try:
        send_heartbeat(backend_url, agent_token, version, override_status="offline")
    except Exception as exc:
        logger.warning(f"Could not send final offline heartbeat: {exc}")


def auto_register_agent(backend_url: str) -> Optional[str]:
    """
    Auto-registers an Agent with the backend if USER_EMAIL and USER_PASSWORD (or default test credentials)
    are available, auto-detecting data sources from environment variables.
    """
    user_email = os.getenv("USER_EMAIL")
    user_pass = os.getenv("USER_PASSWORD")
    if not user_email or not user_pass:
        logger.error("Auto-registration failed: USER_EMAIL and USER_PASSWORD environment variables must be provided when AGENT_TOKEN is not set.")
        return None

    agent_name = os.getenv("AGENT_NAME", "Docker Agent")
    agent_ident = os.getenv("AGENT_IDENTIFIER", f"docker_agent_{int(time.time())}")

    # 1. Login user to get JWT token
    login_url = f"{backend_url.rstrip('/')}/api/v1/auth/login"
    login_data = json.dumps({"email": user_email, "password": user_pass}).encode("utf-8")
    req = urllib.request.Request(login_url, data=login_data, headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=10.0) as resp:
            cookies = resp.headers.get_all("Set-Cookie") or []
            jwt_token = ""
            for c in cookies:
                if "access_token=" in c:
                    jwt_token = c.split("access_token=")[1].split(";")[0]

            if not jwt_token:
                res_json = json.loads(resp.read().decode("utf-8"))
                jwt_token = res_json.get("access_token", "")

            if not jwt_token:
                logger.error("Could not obtain access token during auto-registration login.")
                return None
    except Exception as exc:
        logger.error(f"Auto-registration login failed for '{user_email}': {exc}")
        return None

    # 2. Extract DataSources from ENV
    ds_list = []
    seen = set()
    for k, v in os.environ.items():
        if ("SRC_" in k or "DEST_" in k or "SOURCE_DB" in k or "DEST_DB" in k) and ("URL" in k or "URI" in k):
            ident = extract_identifier_from_env_key(k)
            if ident in seen:
                continue
            seen.add(ident)
            role = "target" if ("DEST" in k) else "source"
            db_type = os.getenv(f"{k.replace('_URL', '_TYPE').replace('_URI', '_TYPE')}", "postgresql")
            ds_list.append({"name": f"{ident.title()} DB", "type": db_type.lower(), "role": role, "identifier": ident})

    if not ds_list:
        ds_list = [{"name": "Default DB", "type": "postgresql", "role": "source", "identifier": "src_db_1"}]

    # 3. Create Agent
    create_url = f"{backend_url.rstrip('/')}/api/v1/agents"
    create_payload = json.dumps({
        "name": agent_name,
        "agent_identifier": agent_ident,
        "version": os.getenv("AGENT_VERSION", "1.0.0"),
        "data_sources": ds_list,
    }).encode("utf-8")

    req_create = urllib.request.Request(
        create_url,
        data=create_payload,
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {jwt_token}"},
        method="POST"
    )
    try:
        with urllib.request.urlopen(req_create, timeout=10.0) as resp:
            body = json.loads(resp.read().decode("utf-8"))
            token = body.get("api_token")
            logger.info(f"Agent '{agent_name}' auto-registered successfully! Agent ID: {body.get('id')}")
            return token
    except Exception as exc:
        logger.error(f"Failed to auto-register Agent '{agent_name}': {exc}")
        return None


def poll_and_execute_tasks(
    backend_url: str,
    agent_token: str,
    heartbeat_config: Optional["HeartbeatConfig"] = None,
):
    """Polls backend GET /api/v1/agents/tasks for pending migration execution jobs."""
    clean_token = agent_token.strip()
    url = f"{backend_url.rstrip('/')}/api/v1/agents/tasks"
    req = urllib.request.Request(url, headers={"X-Agent-Token": clean_token}, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=10.0) as resp:
            tasks = json.loads(resp.read().decode("utf-8"))
            if not isinstance(tasks, list) or not tasks:
                return

            logger.info(f"Discovered {len(tasks)} pending execution job(s) for this agent!")

            # Snap back to fast 20-sec heartbeat immediately when a job is found
            # (don't wait for next backend directive — this is the direct trigger path)
            if heartbeat_config is not None:
                heartbeat_config.enter_active_mode()

            # Fetch source & target database URLs from environment (EC-13)
            src_urls = {}
            dest_urls = {}
            for k, v in os.environ.items():
                if ("SRC_" in k or "SOURCE_DB" in k) and ("URL" in k or "URI" in k):
                    ident = extract_identifier_from_env_key(k)
                    src_urls[ident] = v
                elif ("DEST_" in k or "DEST_DB" in k or "TARGET_" in k) and ("URL" in k or "URI" in k):
                    dest_urls[k] = v

            dest_url = ""
            if dest_urls:
                sorted_keys = sorted(dest_urls.keys())
                primary_key = sorted_keys[0]
                dest_url = dest_urls[primary_key]
                if len(sorted_keys) > 1:
                    logger.warning(
                        f"Multiple destination database URLs detected ({sorted_keys}). "
                        f"Selected primary target '{primary_key}'."
                    )

            if not dest_url:
                dest_url = os.getenv("DEST_DB_4_URL", os.getenv("DEST_DB_1_URL", ""))

            if not dest_url:
                report_fatal_error_and_exit(
                    backend_url=backend_url,
                    agent_token=clean_token,
                    version=os.getenv("AGENT_VERSION", "1.0.0"),
                    error_message="No destination database environment variables (DEST_*_URL or DEST_DB_URL) detected. Migration cannot run without a target database.",
                    error_category="CONFIG_ERROR",
                    exit_code=1,
                )

            from engine.progress_reporter import ProgressReporter
            from engine.orchestrator import ExecutionOrchestrator

            for task in tasks:
                job_id = task.get("job_id")
                plan_id = task.get("migration_plan_id")
                is_dry_run = bool(task.get("is_dry_run", False))
                truncate_target = bool(task.get("truncate_target", False))

                try:
                    logger.info(f"Fetching AST plan '{plan_id}' for job '{job_id}' (Dry Run: {is_dry_run}, Clean Wipe: {truncate_target}) via X-Agent-Token...")

                    # Fetch full plan AST from API using Agent token auth
                    plan_url = f"{backend_url.rstrip('/')}/api/v1/plans/{plan_id}"
                    req_plan = urllib.request.Request(
                        plan_url,
                        headers={"X-Agent-Token": clean_token},
                        method="GET"
                    )
                    with urllib.request.urlopen(req_plan, timeout=15.0) as r_plan:
                        plan_ast = json.loads(r_plan.read().decode("utf-8"))
                    
                    target_engine_type = "postgresql"
                    if dest_url.startswith("mongodb://") or dest_url.startswith("mongodb+srv://") or "mongo" in dest_url.lower():
                        target_engine_type = "mongodb"
                    elif "mysql" in dest_url.lower():
                        target_engine_type = "mysql"
                    elif "sqlite" in dest_url.lower():
                        target_engine_type = "sqlite"

                    ExecutionOrchestrator.run_job(
                        backend_url=backend_url,
                        agent_token=clean_token,
                        job_id=job_id,
                        plan_ast=plan_ast,
                        source_db_urls=src_urls,
                        target_db_url=dest_url,
                        target_engine_type=target_engine_type,
                        is_dry_run=is_dry_run,
                        truncate_target=truncate_target,
                    )

                    # Trigger metadata snapshot re-sync after migration completes so control plane has fresh row counts
                    try:
                        sync_metadata_snapshots(backend_url, clean_token)
                    except Exception as sync_exc:
                        logger.warning(f"Notice: Could not re-sync metadata snapshot after migration: {sync_exc}")
                except Exception as run_err:
                    logger.error(f"Execution error for job '{job_id}': {run_err}")
    except Exception as exc:
        logger.warning(f"Task polling check exception: {exc}")


import threading


class HeartbeatConfig:
    """
    Thread-safe mutable heartbeat interval configuration.
    Shared between the background HeartbeatThread and the main task polling loop.

    Active mode  (20s) : used during job execution or while idle < 5 minutes.
    Idle mode   (300s) : Option C standby — container stays alive, heartbeat every 5 minutes.
    """
    ACTIVE_INTERVAL: int = 20    # seconds during active job / initial grace period
    IDLE_INTERVAL: int = 300     # seconds in Option C standby (5 minutes)

    def __init__(self) -> None:
        self._interval = self.ACTIVE_INTERVAL
        self._lock = threading.Lock()
        self._wake_event = threading.Event()

    def enter_idle_mode(self) -> None:
        """Switch to 5-minute standby heartbeat (Option C). Container stays alive."""
        with self._lock:
            if self._interval != self.IDLE_INTERVAL:
                self._interval = self.IDLE_INTERVAL
                logger.info(
                    "[OPTION C] Heartbeat switched to STANDBY mode (5-min interval). "
                    "Container remains active and ready for new migration jobs."
                )

    def enter_active_mode(self) -> None:
        """Restore 20-second fast heartbeat (used during job execution)."""
        with self._lock:
            if self._interval != self.ACTIVE_INTERVAL:
                self._interval = self.ACTIVE_INTERVAL
                self._wake_event.set()
                logger.info(
                    "[OPTION C] Heartbeat restored to ACTIVE mode (20-sec interval)."
                )

    @property
    def current(self) -> int:
        with self._lock:
            return self._interval


def start_heartbeat_thread(
    backend_url: str,
    agent_token: str,
    version: str,
    interval: int,
    stop_event: threading.Event,
    heartbeat_config: Optional["HeartbeatConfig"] = None,
) -> threading.Thread:
    """
    Launches a dedicated daemon background thread that sends periodic heartbeats
    at a rate determined by heartbeat_config.current (20s active / 300s idle standby).
    Handles backend control directives:
      - ENTER_IDLE_MODE   : switches to 5-min interval (Option C) — container stays alive
      - RESUME_ACTIVE_MODE: restores 20-sec interval when a new job is detected
      - SHUTDOWN          : sets stop_event to trigger graceful container exit (Option A)
    """
    if heartbeat_config is None:
        heartbeat_config = HeartbeatConfig()
        heartbeat_config._interval = interval
        heartbeat_config.ACTIVE_INTERVAL = interval

    def _run() -> None:
        logger.info(f"Background heartbeat loop started (initial interval: {heartbeat_config.current}s).")
        while not stop_event.is_set():
            try:
                action = send_heartbeat(backend_url, agent_token, version)
            except Exception as exc:
                logger.warning(f"Background heartbeat exception: {exc}")
                action = None

            if action == "SHUTDOWN":
                # Option A: Backend commanded graceful container shutdown
                logger.info(
                    "[OPTION A] Backend issued SHUTDOWN directive. "
                    "Initiating graceful container exit..."
                )
                stop_event.set()  # Signals both heartbeat thread and main polling loop to stop
                break

            elif action == "ENTER_IDLE_MODE":
                # Option C: No active jobs for 5+ min — slow down, container stays alive
                heartbeat_config.enter_idle_mode()

            elif action == "RESUME_ACTIVE_MODE":
                # Backend confirmed an active job — snap back to fast mode
                heartbeat_config.enter_active_mode()

            # Sliced wait loop: wake up immediately if mode switches to active or stop_event is set
            sleep_elapsed = 0
            while not stop_event.is_set():
                if stop_event.wait(timeout=1.0):
                    break
                sleep_elapsed += 1
                if sleep_elapsed >= heartbeat_config.current or heartbeat_config._wake_event.is_set():
                    heartbeat_config._wake_event.clear()
                    break

        logger.info("Background heartbeat loop terminated cleanly.")

    t = threading.Thread(target=_run, daemon=True, name="HeartbeatThread")
    t.start()
    return t


def main():
    logger.info("Initializing Docker Agent process...")
    backend_url = os.getenv("BACKEND_URL", os.getenv("API_BASE_URL", "http://localhost:8000")).replace("/api/v1", "")
    agent_token = os.getenv("AGENT_TOKEN", "")
    version = os.getenv("AGENT_VERSION", "1.0.0")
    run_once = os.getenv("AGENT_RUN_ONCE", "false").lower() == "true"
    interval = int(os.getenv("HEARTBEAT_INTERVAL", "20"))
    poll_interval = int(os.getenv("TASK_POLL_INTERVAL", "10"))

    log_configured_databases()

    if not agent_token:
        logger.info("AGENT_TOKEN not set. Attempting auto-registration with Control Plane...")
        agent_token = auto_register_agent(backend_url)

    if not agent_token:
        report_fatal_error_and_exit(
            backend_url=backend_url,
            agent_token="",
            version=version,
            error_message="AGENT_TOKEN environment variable is missing. The Docker agent cannot run unauthenticated.",
            error_category="MISSING_TOKEN",
            exit_code=1,
        )

    stop_event = threading.Event()
    is_shutting_down = False

    def signal_handler(signum, frame):
        nonlocal is_shutting_down
        if is_shutting_down:
            return
        is_shutting_down = True
        logger.info(f"Received termination signal ({signum}). Initiating graceful shutdown...")
        stop_event.set()
        graceful_shutdown(backend_url, agent_token, version)
        sys.exit(0)

    try:
        signal.signal(signal.SIGINT, signal_handler)
        signal.signal(signal.SIGTERM, signal_handler)
    except (ValueError, AttributeError):
        pass

    # Initial startup connection handshake with retry loop
    max_retries = 5
    backoff_seconds = 3
    connected = False
    last_handshake_err = None

    for attempt in range(1, max_retries + 1):
        logger.info(f"Executing startup connection handshake with backend (attempt {attempt}/{max_retries})...")
        try:
            send_heartbeat(backend_url, agent_token, version)
            connected = True
            break
        except urllib.error.HTTPError as http_err:
            last_handshake_err = http_err
            if http_err.code in (401, 403):
                report_fatal_error_and_exit(
                    backend_url=backend_url,
                    agent_token=agent_token,
                    version=version,
                    error_message=f"Agent token was rejected by backend (HTTP {http_err.code}: Unauthorized). Please verify the AGENT_TOKEN in your docker run command.",
                    error_category="AUTH_ERROR",
                    exit_code=1,
                )
        except Exception as exc:
            last_handshake_err = exc
        if attempt < max_retries:
            logger.warning(f"Handshake failed. Retrying in {backoff_seconds} seconds...")
            time.sleep(backoff_seconds)

    if not connected:
        report_fatal_error_and_exit(
            backend_url=backend_url,
            agent_token=agent_token,
            version=version,
            error_message=f"Agent startup handshake failed after {max_retries} attempts: {last_handshake_err}. Verify BACKEND_URL and host network connectivity.",
            error_category="CONNECTION_ERROR",
            exit_code=1,
        )

    logger.info("Agent process is online and securely connected to backend.")

    # Execute schema metadata introspection for all configured healthy databases
    try:
        sync_metadata_snapshots(backend_url, agent_token)
    except Exception as meta_err:
        logger.warning(f"Metadata auto-introspection encountered an error: {meta_err}")

    if run_once:
        logger.info("AGENT_RUN_ONCE is enabled. Running single task poll routine.")
        poll_and_execute_tasks(backend_url, agent_token)
        return

    # Create shared HeartbeatConfig (Option C adaptive interval)
    # Starts in ACTIVE mode (20s). Switches to IDLE mode (300s) on backend directive.
    heartbeat_config = HeartbeatConfig()

    # Start dedicated background heartbeat thread so heartbeats continue during long ETL jobs
    start_heartbeat_thread(backend_url, agent_token, version, interval, stop_event, heartbeat_config)

    # Continuous task polling loop in main thread
    logger.info(f"Starting continuous task polling loop (interval: {poll_interval}s)...")
    try:
        while not stop_event.is_set():
            try:
                poll_and_execute_tasks(backend_url, agent_token, heartbeat_config)
            except Exception as exc:
                logger.warning(f"Task polling exception: {exc}")
            stop_event.wait(timeout=poll_interval)
    except KeyboardInterrupt:
        logger.info("Docker Agent process stopping on user signal.")
    finally:
        stop_event.set()
        graceful_shutdown(backend_url, agent_token, version)
        logger.info("Docker Agent process exiting cleanly.")
        os._exit(0)


if __name__ == "__main__":
    try:
        main()
    except Exception as unhandled_exc:
        _backend_url = os.getenv("BACKEND_URL", os.getenv("API_BASE_URL", "http://localhost:8000")).replace("/api/v1", "")
        _agent_token = os.getenv("AGENT_TOKEN", "")
        _version = os.getenv("AGENT_VERSION", "1.0.0")
        report_fatal_error_and_exit(
            backend_url=_backend_url,
            agent_token=_agent_token,
            version=_version,
            error_message=f"Unhandled agent runtime crash: {str(unhandled_exc)}",
            error_category="RUNTIME_CRASH",
            exit_code=1,
        )
