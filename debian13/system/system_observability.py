"""Honest, bounded runtime diagnostics for the Debian 13 API.

Measurements describe the *current runtime/container*, not a verified boot
of a full Debian 13 VM, and do not prove an LLM agent is working.
"""
from __future__ import annotations

import os
import platform
from datetime import datetime, timezone
from pathlib import Path


def _memory() -> dict:
    try:
        pairs = {}
        for line in Path("/proc/meminfo").read_text(encoding="ascii").splitlines():
            key, _, value = line.partition(":")
            if key in ("MemTotal", "MemAvailable"):
                pairs[key] = int(value.strip().split()[0])
        if "MemTotal" in pairs and "MemAvailable" in pairs:
            return {
                "totalMiB": pairs["MemTotal"] // 1024,
                "usedMiB": max(0, (pairs["MemTotal"] - pairs["MemAvailable"]) // 1024),
                "source": "/proc/meminfo",
            }
    except (OSError, ValueError, IndexError):
        pass
    return {"source": "unavailable"}


def _disk(workspace: Path) -> dict:
    try:
        fs = os.statvfs(workspace)
        return {
            "totalGiB": round(fs.f_blocks * fs.f_frsize / (1024 ** 3), 2),
            "usedGiB": round((fs.f_blocks - fs.f_bavail) * fs.f_frsize / (1024 ** 3), 2),
            "source": "statvfs(workspace)",
        }
    except OSError:
        return {"source": "unavailable"}


def snapshot(server) -> dict:
    try:
        load = os.getloadavg()
        cpu = {"logicalCores": os.cpu_count(), "loadAverage1m": round(load[0], 2),
               "source": "os.getloadavg", "cpuPercentMeasured": False}
    except OSError:
        cpu = {"logicalCores": os.cpu_count(), "source": "unavailable",
               "cpuPercentMeasured": False}
    tasks = server.work_store.list_tasks()
    events = server.work_store.list_events()
    monitor = server.agent_monitor.snapshot() if getattr(server, "agent_monitor", None) else {
        "enabled": False, "source": "AI Studio original agents registry",
        "lastPollAt": None, "registryReachable": False,
        "executionVerified": False, "workersVerified": 0,
        "reason": "background_monitor_disabled",
    }
    return {
        "observedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "runtime": {"python": platform.python_version(),
                    "distribution": platform.platform(),
                    "debian13BootVerified": False},
        "cpu": cpu,
        "memory": _memory(),
        "disk": _disk(server.workspace),
        "taskLedger": {
            "queued": sum(t["status"] == "queued" for t in tasks),
            "cancelled": sum(t["status"] == "cancelled" for t in tasks),
            "storedInSQLite": True,
            "automaticallyExecutingWorkers": 0,
        },
        "recentEvents": [
            {"type": str(e["type"])[:64], "agent": str(e["agent"])[:32],
             "taskId": str(e["task_id"])[:64], "at": str(e["at"])[:40]}
            for e in events[:15]
        ],
        "agentMonitor": monitor,
        "executionEnvironment": "debian13-api-runtime",
    }
