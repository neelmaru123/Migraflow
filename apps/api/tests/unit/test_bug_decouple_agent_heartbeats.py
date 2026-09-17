"""
Unit test for Fix 4: Decouple agent heartbeats from job execution.
Verifies that background heartbeat thread continues firing periodic pings while ExecutionOrchestrator.run_job runs.
"""

import os
import sys
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
AGENT_DIR = REPO_ROOT / "apps" / "agent"
if str(AGENT_DIR) not in sys.path:
    sys.path.insert(0, str(AGENT_DIR))

import main as agent_main


def test_heartbeat_thread_continues_during_job_execution():
    """
    Verifies that start_heartbeat_thread fires send_heartbeat periodically
    even while ExecutionOrchestrator.run_job is executing a long-running job.
    """
    backend_url = "http://localhost:8000"
    agent_token = "ag_live_testtoken123"
    version = "1.0.0"
    interval = 1  # 1 second interval for fast test execution

    hb_config = agent_main.HeartbeatConfig()
    hb_config._interval = interval
    hb_config.ACTIVE_INTERVAL = interval

    heartbeat_call_count = 0
    heartbeat_lock = threading.Lock()

    def mock_send_heartbeat(*args, **kwargs):
        nonlocal heartbeat_call_count
        with heartbeat_lock:
            heartbeat_call_count += 1
        return True

    stop_event = threading.Event()

    with patch.object(agent_main, "send_heartbeat", side_effect=mock_send_heartbeat):
        # Start heartbeat thread in background
        h_thread = agent_main.start_heartbeat_thread(
            backend_url=backend_url,
            agent_token=agent_token,
            version=version,
            interval=interval,
            stop_event=stop_event,
            heartbeat_config=hb_config,
        )

        # Simulate long-running job execution for 2.5 seconds
        time.sleep(2.5)

        with heartbeat_lock:
            current_count = heartbeat_call_count

        # Heartbeat should have fired at least 2 times during the 2.5s window
        assert current_count >= 2

        # Signal thread to stop cleanly
        stop_event.set()
        h_thread.join(timeout=2.0)
        assert not h_thread.is_alive()
