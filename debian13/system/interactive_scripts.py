"""Curated, interactive Python diagnostics scripts with explicit input steps.

No arbitrary code, shell, network or root execution. Every result derives from
the live workspace/runtime. In-memory sessions are intentionally short-lived.
"""
from __future__ import annotations

import os
import platform
import stat
import threading
import time
import uuid
from pathlib import Path

TTL_SECONDS = 900
MAX_SESSIONS = 32
MAX_USER_ANSWER = 240
CATALOG = (
    {"id": "workspace", "name": "Raport plików workspace",
     "description": "Policz rzeczywiste pliki i katalogi w wybranym katalogu roboczym."},
    {"id": "file", "name": "Inspekcja pliku",
     "description": "Sprawdź wielkość i liczbę wierszy wybranego pliku tekstowego."},
    {"id": "runtime", "name": "Diagnostyka Python/Linux",
     "description": "Odczytaj wersję środowiska, UID i podstawowe parametry procesu."},
)
SESSIONS: dict[str, dict] = {}
LOCK = threading.RLock()


class ScriptError(Exception):
    def __init__(self, status: int, code: str):
        super().__init__(code)
        self.status = status
        self.code = code


def catalog() -> dict:
    return {"scripts": list(CATALOG), "mode": "curated_python_only",
            "arbitraryExecution": False, "rootExecution": False}


def _prune() -> None:
    now = time.monotonic()
    for key, item in list(SESSIONS.items()):
        if now - item["updated_at"] > TTL_SECONDS:
            del SESSIONS[key]


def _describe(item: dict) -> dict:
    return {k: v for k, v in item.items() if k != "updated_at"}


def start(script_id: object) -> dict:
    if script_id not in ("workspace", "file", "runtime"):
        raise ScriptError(400, "unknown_script")
    with LOCK:
        _prune()
        if len(SESSIONS) >= MAX_SESSIONS:
            raise ScriptError(429, "script_session_limit")
        sid = str(uuid.uuid4())
        session = {
            "sessionId": sid, "scriptId": script_id, "status": "waiting_input",
            "step": 1, "stepCount": 2, "output": [],
            "prompt": {"workspace": "Czy policzyć pliki i katalogi? [tak/nie]",
                       "file": "Podaj względną ścieżkę pliku w workspace:",
                       "runtime": "Pokaż szczegóły środowiska Python/Linux? [tak/nie]"}[script_id],
            "updated_at": time.monotonic(),
        }
        SESSIONS[sid] = session
        return _describe(session)


def respond(session_id: object, answer: object, workspace: Path) -> dict:
    if not isinstance(session_id, str) or not isinstance(answer, str) or len(answer) > MAX_USER_ANSWER:
        raise ScriptError(400, "invalid_script_input")
    with LOCK:
        _prune()
        item = SESSIONS.get(session_id)
        if item is None:
            raise ScriptError(404, "script_session_not_found")
        if item["status"] != "waiting_input":
            raise ScriptError(409, "script_not_waiting")
        value = answer.strip()
        if not value:
            raise ScriptError(400, "empty_script_input")
        script = item["scriptId"]
        if script in ("workspace", "runtime") and value.lower() not in ("tak", "nie", "yes", "no"):
            raise ScriptError(400, "answer_yes_or_no")
        if script == "workspace":
            if value.lower() in ("tak", "yes"):
                # Never follow workspace links outside the trusted root.
                dirs = files = 0
                try:
                    for child in workspace.iterdir():
                        if child.is_symlink():
                            continue
                        if child.is_dir():
                            dirs += 1
                        elif child.is_file():
                            files += 1
                except OSError:
                    raise ScriptError(503, "workspace_unavailable") from None
                item["output"].append(f"Workspace: {workspace}\nPliki: {files}; katalogi: {dirs}.")
            else:
                item["output"].append("Inspekcja pominięta na życzenie operatora.")
        elif script == "runtime":
            if value.lower() in ("tak", "yes"):
                item["output"].append(
                    f"Python: {platform.python_version()}\n"
                    f"System: {platform.system()} {platform.release()}\n"
                    f"UID procesu: {os.geteuid()}\n"
                    "Wersja jądra nie potwierdza uruchomienia pełnej maszyny Debian 13."
                )
            else:
                item["output"].append("Inspekcja pominięta na życzenie operatora.")
        else:
            if value.startswith("-") or "\x00" in value or Path(value).is_absolute():
                raise ScriptError(400, "invalid_workspace_path")
            root = workspace.resolve()
            target = (root / value).resolve()
            try:
                target.relative_to(root)
            except ValueError:
                raise ScriptError(403, "path_outside_workspace") from None
            if not target.is_file() or target.is_symlink():
                raise ScriptError(404, "workspace_file_not_found")
            try:
                meta = target.stat()
                if not stat.S_ISREG(meta.st_mode) or meta.st_size > 256 * 1024:
                    raise ScriptError(413, "file_too_large")
                raw = target.read_bytes()
                if b"\x00" in raw:
                    raise ScriptError(415, "binary_file_unsupported")
                text = raw.decode("utf-8")
            except UnicodeError:
                raise ScriptError(415, "binary_file_unsupported") from None
            except OSError:
                raise ScriptError(503, "workspace_file_unavailable") from None
            item["output"].append(
                f"Plik: {target.relative_to(root)}\nRozmiar: {meta.st_size} B\n"
                f"Wiersze: {len(text.splitlines())}.")
        item.update(status="completed", step=2, prompt=None, updated_at=time.monotonic())
        return _describe(item)


def cancel(session_id: object) -> dict:
    if not isinstance(session_id, str):
        raise ScriptError(400, "invalid_script_session")
    with LOCK:
        _prune()
        item = SESSIONS.get(session_id)
        if item is None:
            raise ScriptError(404, "script_session_not_found")
        if item["status"] == "completed":
            raise ScriptError(409, "script_already_completed")
        item.update(status="cancelled", prompt=None, updated_at=time.monotonic())
        return _describe(item)
