"""Persistent Work task ledger for Debian 13.

Task creation itself DOES NOT start anything (``executionStarted: false``).
An optional agent worker may later hand a queued task to the assigned agent's
own model and record the answer (status ``completed``) or the failure code
(``failed``). Nothing here executes shell commands, submits purchases or issues
GitHub requests. SQLite is stored in the mounted private workspace.
"""
from __future__ import annotations

import os
from contextlib import contextmanager
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

MAX_TASKS = 2000
MAX_EVENTS = 5000
PAGE_SIZE = 100
MAX_RESULT_CHARS = 8000

TASKS_TABLE = """
    CREATE TABLE IF NOT EXISTS {name} (
        id TEXT PRIMARY KEY,
        kind TEXT NOT NULL,
        agent TEXT NOT NULL,
        title TEXT NOT NULL,
        description TEXT NOT NULL,
        status TEXT NOT NULL CHECK(status IN ('queued','running','completed','failed','cancelled')),
        execution TEXT NOT NULL DEFAULT 'not_started',
        requires_approval INTEGER NOT NULL DEFAULT 1,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        result TEXT,
        result_model TEXT
    )
"""
V1_COLUMNS = ("id,kind,agent,title,description,status,execution,"
              "requires_approval,created_at,updated_at")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class WorkStore:
    def __init__(self, workspace: Path):
        self.db_path = workspace / ".debian13-work.sqlite3"
        with self._open() as db:
            self._migrate_v1(db)
            db.executescript(TASKS_TABLE.format(name="tasks") + """;
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id TEXT NOT NULL,
                    agent TEXT NOT NULL,
                    type TEXT NOT NULL,
                    at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS tasks_latest ON tasks(created_at DESC);
                CREATE TABLE IF NOT EXISTS alex_submissions (
                    task_id TEXT PRIMARY KEY REFERENCES tasks(id),
                    remote_task_id TEXT NOT NULL,
                    submitted_at TEXT NOT NULL
                );
            """)
        os.chmod(self.db_path, 0o600)

    @staticmethod
    def _migrate_v1(db) -> None:
        """Rebuild a v1 table (queued/cancelled only) keeping every existing row."""
        row = db.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='tasks'"
        ).fetchone()
        if row is None or "'completed'" in row["sql"]:
            return
        # SQLite's documented procedure: create new, copy, drop old, rename new.
        db.execute(TASKS_TABLE.format(name="tasks_v2"))
        db.execute(f"INSERT INTO tasks_v2({V1_COLUMNS}) SELECT {V1_COLUMNS} FROM tasks")
        db.execute("DROP TABLE tasks")
        db.execute("ALTER TABLE tasks_v2 RENAME TO tasks")
        db.commit()

    @contextmanager
    def _open(self):
        db = sqlite3.connect(self.db_path, timeout=10)
        try:
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA busy_timeout=10000")
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def list_tasks(self) -> list[dict]:
        with self._open() as db:
            return [dict(row) for row in db.execute(
                "SELECT * FROM tasks ORDER BY created_at DESC, rowid DESC LIMIT ?",
                (PAGE_SIZE,),
            )]

    def list_events(self) -> list[dict]:
        with self._open() as db:
            return [dict(row) for row in db.execute(
                "SELECT task_id,agent,type,at FROM events ORDER BY id DESC LIMIT ?",
                (PAGE_SIZE,),
            )]

    def get_task(self, task_id: str) -> dict | None:
        with self._open() as db:
            row = db.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
            return dict(row) if row else None

    def create_task(self, kind: str, agent: str, title: str, description: str) -> dict:
        ident = str(uuid.uuid4())
        created = _now()
        with self._open() as db:
            # Lock before checking the quota so concurrent requests cannot exceed it.
            db.execute("BEGIN IMMEDIATE")
            count = db.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]
            if count >= MAX_TASKS:
                raise ValueError("Task quota reached; archive with operator review.")
            db.execute(
                "INSERT INTO tasks(id,kind,agent,title,description,status,execution,"
                "requires_approval,created_at,updated_at) "
                "VALUES (?,?,?,?,?,'queued','not_started',1,?,?)",
                (ident, kind, agent, title, description, created, created),
            )
            db.execute(
                "INSERT INTO events(task_id,agent,type,at) VALUES (?,?,?,?)",
                (ident, agent, "task_queued", created),
            )
            db.execute(
                "DELETE FROM events WHERE id NOT IN "
                "(SELECT id FROM events ORDER BY id DESC LIMIT ?)",
                (MAX_EVENTS,),
            )
        return self.get_task(ident)

    def cancel_task(self, task_id: str) -> dict | None:
        with self._open() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT agent,status FROM tasks WHERE id=?", (task_id,)).fetchone()
            if row is None:
                return None
            if row["status"] == "queued":
                stamp = _now()
                db.execute(
                    "UPDATE tasks SET status='cancelled',updated_at=? WHERE id=? AND status='queued'",
                    (stamp, task_id),
                )
                db.execute(
                    "INSERT INTO events(task_id,agent,type,at) VALUES (?,?,?,?)",
                    (task_id, row["agent"], "task_cancelled", stamp),
                )
        return self.get_task(task_id)

    def get_alex_submission(self, task_id: str) -> dict | None:
        with self._open() as db:
            row = db.execute(
                "SELECT remote_task_id, submitted_at FROM alex_submissions WHERE task_id=?",
                (task_id,),
            ).fetchone()
        return {
            "status": "accepted_by_remote_api",
            "remoteTaskId": row["remote_task_id"],
            "submittedAt": row["submitted_at"],
            "agentExecutionVerified": False,
            "pullRequestVerified": False,
        } if row else None

    def record_alex_submission(self, task_id: str, remote_task_id: str) -> dict:
        with self._open() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT status,agent FROM tasks WHERE id=?", (task_id,)
            ).fetchone()
            if row is None or row["status"] != "queued" or row["agent"] != "alex":
                raise ValueError("Task no longer eligible for Alex dispatch.")
            stamp = _now()
            db.execute(
                "INSERT OR IGNORE INTO alex_submissions(task_id,remote_task_id,submitted_at) "
                "VALUES (?,?,?)", (task_id, remote_task_id, stamp)
            )
            db.execute(
                "INSERT INTO events(task_id,agent,type,at) "
                "SELECT ?,?,?,? WHERE changes() > 0",
                (task_id, "alex", "task_accepted_by_remote_api", stamp)
            )
        return self.get_alex_submission(task_id)

    # ---- Agent worker lifecycle (only used when the worker is enabled) ----

    def claim_next_task(self, agents: set[str]) -> dict | None:
        """Atomically move the oldest queued task of a ready agent to 'running'."""
        if not agents:
            return None
        marks = ",".join("?" for _ in agents)
        with self._open() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                f"SELECT * FROM tasks WHERE status='queued' AND agent IN ({marks}) "
                "AND id NOT IN (SELECT task_id FROM alex_submissions) "
                "ORDER BY created_at ASC, rowid ASC LIMIT 1",
                tuple(sorted(agents)),
            ).fetchone()
            if row is None:
                return None
            stamp = _now()
            db.execute(
                "UPDATE tasks SET status='running',execution='agent_model_running',updated_at=? "
                "WHERE id=? AND status='queued'", (stamp, row["id"]),
            )
            self._event(db, row["id"], row["agent"], "task_started_by_agent", stamp)
            claimed = dict(row)
        claimed.update(status="running", execution="agent_model_running")
        return claimed

    def complete_task(self, task_id: str, result: str, model: str) -> dict | None:
        return self._finish(task_id, "completed", "agent_model_response_recorded",
                            str(result)[:MAX_RESULT_CHARS], str(model)[:120],
                            "task_completed_by_agent")

    def fail_task(self, task_id: str, reason: str) -> dict | None:
        return self._finish(task_id, "failed", "agent_model_failed",
                            str(reason)[:200], None, "task_failed")

    def requeue_interrupted(self) -> int:
        """Tasks left 'running' by a stopped process go back to the queue."""
        with self._open() as db:
            cursor = db.execute(
                "UPDATE tasks SET status='queued',execution='not_started',updated_at=? "
                "WHERE status='running'", (_now(),),
            )
            return cursor.rowcount

    def _finish(self, task_id, status, execution, result, model, event) -> dict | None:
        with self._open() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT agent,status FROM tasks WHERE id=?", (task_id,)).fetchone()
            if row is None or row["status"] != "running":
                return None
            stamp = _now()
            db.execute(
                "UPDATE tasks SET status=?,execution=?,result=?,result_model=?,updated_at=? WHERE id=?",
                (status, execution, result, model, stamp, task_id),
            )
            self._event(db, task_id, row["agent"], event, stamp)
        return self.get_task(task_id)

    @staticmethod
    def _event(db, task_id: str, agent: str, kind: str, stamp: str) -> None:
        db.execute("INSERT INTO events(task_id,agent,type,at) VALUES (?,?,?,?)",
                   (task_id, agent, kind, stamp))
        db.execute("DELETE FROM events WHERE id NOT IN "
                   "(SELECT id FROM events ORDER BY id DESC LIMIT ?)", (MAX_EVENTS,))
