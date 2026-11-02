"""Audited, human-approved *container-root* actions for the Debian 13 workspace.

A language model may only PROPOSE a fixed action. A separate operator credential
must APPROVE the exact proposal. This module never accepts a shell string,
arbitrary argv, host path, sudo, Docker socket, or a command supplied by a model.
Execution requires the isolated root-runner container to be explicitly enabled.
"""
from __future__ import annotations

import os
from contextlib import contextmanager
import sqlite3
import stat
import subprocess
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

COMMANDS = {
    "identity": ("id",),
    "kernel": ("uname", "-a"),
    "package_audit": ("dpkg", "--audit"),
    "workspace_disk": ("df", "-h", "/workspace"),
    "workspace_permissions": ("stat", "-c", "%u:%g %a %n", "/workspace"),
    "repair_workspace_owner": None,  # Fixed Python operation, not a shell.
}
AGENTS = frozenset(("Alex", "Aria", "Astyria", "Aurelius"))
MAX_ITEMS = 250
APPROVAL_TTL = 900
LOCK = threading.RLock()


class AdminError(Exception):
    def __init__(self, code, status=400):
        super().__init__(code)
        self.code = code
        self.status = status


def _utc():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class AdminStore:
    def __init__(self, workspace: Path, create=True):
        self.workspace = Path(workspace).resolve()
        self.db_path = self.workspace / ".debian13-admin.sqlite3"
        if create:
            self.workspace.mkdir(parents=True, exist_ok=True)
            with self._db() as db:
                db.execute("""CREATE TABLE IF NOT EXISTS operations(
                    id TEXT PRIMARY KEY, agent TEXT NOT NULL,
                    command_id TEXT NOT NULL, reason TEXT NOT NULL,
                    status TEXT NOT NULL, created_ts INTEGER NOT NULL,
                    created_at TEXT NOT NULL, approved_at TEXT,
                    started_at TEXT, completed_at TEXT,
                    exit_code INTEGER, result TEXT NOT NULL DEFAULT ''
                )""")
            os.chmod(self.db_path, 0o600)

    @contextmanager
    def _db(self):
        db = sqlite3.connect(self.db_path, timeout=8)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA busy_timeout=8000")
        try:
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def propose(self, agent, command_id, reason):
        if not isinstance(agent, str) or agent not in AGENTS:
            raise AdminError("unknown_original_agent")
        if not isinstance(command_id, str) or command_id not in COMMANDS:
            raise AdminError("root_command_not_allowlisted", 403)
        if not isinstance(reason, str) or not 1 <= len(reason.strip()) <= 350:
            raise AdminError("invalid_reason")
        with LOCK, self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute("SELECT COUNT(*) FROM operations").fetchone()[0] >= MAX_ITEMS:
                raise AdminError("admin_proposal_limit", 429)
            oid = str(uuid.uuid4())
            db.execute("""INSERT INTO operations
                (id, agent, command_id, reason, status, created_ts, created_at)
                VALUES (?, ?, ?, ?, 'pending_approval', ?, ?)""",
                (oid, agent, command_id, reason.strip(), int(time.time()), _utc()))
            db.commit()
        return {"id": oid, "agent": agent, "commandId": command_id,
                "status": "pending_approval", "executionStarted": False,
                "requiresOperatorApproval": True, "runsAs": "root_in_isolated_container_only"}

    def _get(self, db, oid):
        if not isinstance(oid, str) or len(oid) != 36:
            raise AdminError("invalid_operation_id")
        row = db.execute("SELECT * FROM operations WHERE id = ?", (oid,)).fetchone()
        if row is None:
            raise AdminError("admin_operation_not_found", 404)
        return row

    def approve(self, oid, confirmation):
        if confirmation != "ZATWIERDZAM ROOT W KONTENERZE":
            raise AdminError("explicit_root_confirmation_required", 403)
        with LOCK, self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            row = self._get(db, oid)
            if row["status"] != "pending_approval":
                raise AdminError("operation_not_pending", 409)
            if time.time() - row["created_ts"] > APPROVAL_TTL:
                db.execute("UPDATE operations SET status='expired' WHERE id=?", (oid,))
                db.commit()
                raise AdminError("approval_expired", 410)
            db.execute("UPDATE operations SET status='approved', approved_at=? WHERE id=?",
                       (_utc(), oid))
            db.commit()
        return {"id": oid, "status": "approved", "executionStarted": False,
                "runsAs": "root_in_isolated_container_only"}

    def list(self):
        with self._db() as db:
            records = db.execute("""SELECT id,agent,command_id,reason,status,
                created_at,approved_at,started_at,completed_at,exit_code,result
                FROM operations ORDER BY created_ts DESC LIMIT 100""").fetchall()
        return {"operations": [
            {"id": r["id"], "agent": r["agent"], "commandId": r["command_id"],
             "reason": r["reason"], "status": r["status"], "createdAt": r["created_at"],
             "approvedAt": r["approved_at"], "startedAt": r["started_at"],
             "completedAt": r["completed_at"], "exitCode": r["exit_code"],
             "output": r["result"][:2048]} for r in records
        ], "worker": "separate_isolated_container", "autoApprove": False}

    def claim(self):
        with LOCK, self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            record = db.execute("""SELECT * FROM operations WHERE status='approved'
                ORDER BY approved_at ASC LIMIT 1""").fetchone()
            if record is None:
                db.commit()
                return None
            db.execute("UPDATE operations SET status='executing',started_at=? WHERE id=?",
                       (_utc(), record["id"]))
            db.commit()
        return {"id": record["id"], "command": record["command_id"]}

    def finish(self, oid, success, exit_code, output):
        with LOCK, self._db() as db:
            db.execute("""UPDATE operations SET status=?,completed_at=?,exit_code=?,result=?
                WHERE id=? AND status='executing'""",
                ("completed" if success else "failed", _utc(), exit_code, output[:2048], oid))
            db.commit()


def execute_allowlisted(command_id, workspace: Path):
    """Runs only a compiled-in command as *container* root, never as host root."""
    if os.geteuid() != 0:
        return False, -1, "root_runner_not_privileged"
    if command_id not in COMMANDS:
        return False, -1, "root_command_not_allowlisted"
    if command_id == "repair_workspace_owner":
        try:
            root = Path(workspace)
            if str(root) != "/workspace" or root.is_symlink():
                return False, -1, "invalid_isolated_workspace"
            os.chown(root, 10001, 10001)
            # Only the exact known app DBs; never traverse the workspace.
            for name in (".debian13-work.sqlite3", ".debian13-admin.sqlite3"):
                child = root / name
                if child.is_symlink():
                    continue
                try:
                    mode = child.lstat().st_mode
                except FileNotFoundError:
                    continue
                if stat.S_ISREG(mode):
                    os.chown(child, 10001, 10001)
            return True, 0, "isolation_workspace_owner_repaired"
        except OSError:
            return False, -1, "workspace_owner_repair_failed"
    try:
        response = subprocess.run(COMMANDS[command_id], cwd="/workspace", shell=False,
                                  stdin=subprocess.DEVNULL, capture_output=True,
                                  text=True, timeout=8, env={"PATH": "/usr/bin:/bin",
                                  "LANG": "C.UTF-8", "HOME": "/workspace"})
        output = (response.stdout + "\n" + response.stderr).strip()[:2048]
        return response.returncode == 0, response.returncode, output
    except (OSError, subprocess.TimeoutExpired):
        return False, -1, "admin_command_failed_or_timed_out"


def run_approved_loop(workspace=Path("/workspace"), pause=2):
    store = AdminStore(workspace, create=False)
    if os.geteuid() != 0:
        raise SystemExit("admin_runner_requires_container_root")
    while True:
        try:
            job = store.claim()
            if job:
                succeeded, exit_code, result = execute_allowlisted(job["command"], workspace)
                store.finish(job["id"], succeeded, exit_code, result)
        except (OSError, sqlite3.Error):
            # API container may not have created the volume database yet.
            pass
        time.sleep(pause)


if __name__ == "__main__":
    run_approved_loop()
