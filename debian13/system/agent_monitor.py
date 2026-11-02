"""Optional backend-side monitor for the ORIGINAL four AI Studio agents.

The thread polls the existing task registry only. It never starts surrogate
agents, invents worker execution, purchases resources or runs shell commands.
Monitor status is observable even when the browser is closed, while the API
service stays alive.
"""
from __future__ import annotations

import threading
from datetime import datetime, timezone

from original_agents import report as source_report


class AgentMonitor:
    def __init__(self, interval_seconds: int = 30, poll=source_report):
        self.interval_seconds = max(15, min(300, interval_seconds))
        self._poll = poll
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._status = {
            "enabled": True,
            "source": "AI Studio original agents registry",
            "lastPollAt": None,
            "registryReachable": False,
            "executionVerified": False,
            "workersVerified": 0,
            "reason": "awaiting_first_poll",
        }
        self._thread = threading.Thread(target=self._run, daemon=True, name="original-agents-monitor")
        self._thread.start()

    def _run(self):
        while not self._stop.is_set():
            try:
                report = self._poll()
                result = {
                    "enabled": True,
                    "source": "AI Studio original agents registry",
                    "lastPollAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    "registryReachable": report.get("registryReachable") is True,
                    # The registry is NOT a running model worker proof.
                    "executionVerified": False,
                    "workersVerified": 0,
                    "reason": str(report.get("reason", "unverified_source"))[:64],
                    "agents": [{
                        "name": str(agent.get("name", ""))[:32],
                        "registryTaskCount": int(agent.get("tasksInRegistry") or 0),
                        "reportedRunningEntries": int(agent.get("runningEntriesInRegistry") or 0),
                        "workerVerified": False,
                    } for agent in report.get("agents", [])[:4]],
                }
            except Exception:
                result = {
                    "enabled": True, "source": "AI Studio original agents registry",
                    "lastPollAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    "registryReachable": False, "executionVerified": False,
                    "workersVerified": 0, "reason": "source_unavailable",
                }
            with self._lock:
                self._status = result
            if self._stop.wait(self.interval_seconds):
                break

    def snapshot(self) -> dict:
        with self._lock:
            data = self._status.copy()
            if "agents" in data:
                data["agents"] = [a.copy() for a in data["agents"]]
            return data

    def close(self) -> None:
        self._stop.set()
        self._thread.join(timeout=9)
