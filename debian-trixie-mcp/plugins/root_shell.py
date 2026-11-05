"""Wtyczka powłoki użytkownika i uprawnień Root (root_shell).

NAJWAŻNIEJSZE ZASADY BEZPIECZEŃSTWA:
1. shell_exec: bash -c jako zwykły użytkownik (wymaga SHELL_ENABLED=1).
2. root_exec: bash -c jako root przez sudo (wymaga ROOT_SHELL_ENABLED=1).
3. ROOT_SHELL_MODE=approve (DOMYŚLNY): root_exec NIE wykonuje polecenia,
   lecz zapisuje je do kolejki ~/.local/state/debian-trixie-mcp/pending.
   Operator zatwierdza w terminalu skryptem bin/root-approve (sudo pyta o hasło).
   AI odbiera wynik przez root_result(id). AI NIE MOŻE samo zatwierdzić polecenia!
4. ROOT_SHELL_MODE=open: wykonuje od razu przez sudo -n (wymaga reguły sudoers
   zainstalowanej przez install.sh --root-open po potwierdzeniu).
5. Pełny audyt każdego wywołania w pliku audit.jsonl poza katalogiem roboczym.
"""
from __future__ import annotations

import fcntl
import getpass
import json
import os
import subprocess
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable


def _state_dirs() -> tuple[Path, Path, Path, Path]:
    custom = os.environ.get("MCP_STATE_DIR")
    if custom:
        base = Path(custom).expanduser()
    else:
        base = Path.home() / ".local" / "state" / "debian-trixie-mcp"

    pending = base / "pending"
    results = base / "results"
    audit = base / "audit.jsonl"

    pending.mkdir(parents=True, exist_ok=True)
    results.mkdir(parents=True, exist_ok=True)
    return base, pending, results, audit


def register(mcp, run: Callable, clip: Callable, workspace: Path):
    base_dir, pending_dir, results_dir, audit_file = _state_dirs()

    def log_audit(event_type: str, command: str, status: str, exit_code: int | None = None,
                  duration: float | None = None, task_id: str | None = None, error: str | None = None):
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "event_type": event_type,
            "user": getpass.getuser(),
            "task_id": task_id,
            "command": command,
            "status": status,
            "exit_code": exit_code,
            "duration_seconds": round(duration, 3) if duration is not None else None,
            "error": error,
        }
        try:
            with audit_file.open("a", encoding="utf-8") as f:
                fcntl.flock(f.fileno(), fcntl.LOCK_EX)
                try:
                    f.write(json.dumps(entry, ensure_ascii=False) + "\n")
                    f.flush()
                finally:
                    fcntl.flock(f.fileno(), fcntl.LOCK_UN)
        except Exception:
            pass

    @mcp.tool()
    def shell_exec(command: str, timeout_seconds: int = 30) -> str:
        """Wykonuje polecenie powłoki (bash -c) jako bieżący użytkownik. Wymaga SHELL_ENABLED=1."""
        if os.environ.get("SHELL_ENABLED", "0").strip() != "1":
            return (
                "Błąd bezpieczeństwa: Wykonywanie poleceń powłoki użytkownika (shell_exec) "
                "jest wyłączone. Ustaw SHELL_ENABLED=1 w pliku .env, aby odblokować to narzędzie."
            )

        cmd_str = command.strip()
        if not cmd_str:
            return "Błąd: Treść polecenia nie może być pusta."

        timeout = max(1, min(timeout_seconds, 300))
        start_time = time.time()

        try:
            proc = subprocess.run(
                ["/usr/bin/bash", "-c", cmd_str],
                capture_output=True,
                text=True,
                timeout=timeout,
                cwd=workspace,
                shell=False,
            )
            duration = time.time() - start_time
            out = proc.stdout or ""
            err = proc.stderr or ""
            code = proc.returncode

            log_audit("shell_exec", cmd_str, "completed", exit_code=code, duration=duration)

            res = []
            if out:
                res.append(out.strip())
            if err:
                res.append(f"[stderr]:\n{err.strip()}")
            if code != 0:
                res.append(f"\n[Kod zakończenia: {code}]")

            return clip("\n".join(res).strip() or "Polecenie wykonane pomyślnie (brak wyjścia).", 30000)

        except subprocess.TimeoutExpired:
            duration = time.time() - start_time
            log_audit("shell_exec", cmd_str, "timeout", duration=duration, error="Przekroczono limit czasu")
            return f"Błąd: Przekroczono limit czasu wykonania ({timeout}s)."
        except Exception as exc:
            duration = time.time() - start_time
            log_audit("shell_exec", cmd_str, "error", duration=duration, error=str(exc))
            return f"Błąd wykonania powłoki: {exc}"

    @mcp.tool()
    def root_exec(command: str, timeout_seconds: int = 60) -> str:
        """Zleca wykonanie polecenia z uprawnieniami ROOT przez sudo.

        W domyślnym trybie ROOT_SHELL_MODE=approve polecenie trafia do kolejki oczekujących
        i wymaga potwierdzenia przez człowieka w terminalu poleceniem: bin/root-approve.
        """
        if os.environ.get("ROOT_SHELL_ENABLED", "0").strip() != "1":
            return (
                "Błąd bezpieczeństwa: Wykonywanie poleceń roota (root_exec) jest wyłączone. "
                "Ustaw ROOT_SHELL_ENABLED=1 w pliku .env, aby odblokować to narzędzie."
            )

        cmd_str = command.strip()
        if not cmd_str:
            return "Błąd: Treść polecenia root nie może być pusta."

        mode = os.environ.get("ROOT_SHELL_MODE", "approve").strip().lower()
        timeout = max(5, min(timeout_seconds, 600))

        # TRYB 1: APPROVE (DOMYŚLNY - BEZPIECZNY)
        if mode != "open":
            task_id = uuid.uuid4().hex[:10]
            pending_record = {
                "id": task_id,
                "command": cmd_str,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "timeout_seconds": timeout,
                "user": getpass.getuser(),
                "status": "pending",
            }
            task_file = pending_dir / f"{task_id}.json"
            task_file.write_text(json.dumps(pending_record, ensure_ascii=False, indent=2), encoding="utf-8")

            log_audit("root_exec", cmd_str, "pending_approval", task_id=task_id)

            return json.dumps({
                "status": "pending_approval",
                "id": task_id,
                "command": cmd_str,
                "komunikat": "Polecenie ROOT zostało zarejestrowane i oczekuje na zatwierdzenie przez operatora.",
                "instrukcja_dla_uzytkownika": "Wpisz w terminalu serwera: bin/root-approve aby przejrzeć i zatwierdzić.",
                "dalsze_kroki_ai": f"Odczekaj chwilę, a następnie wywołaj root_result(id='{task_id}'), aby pobrać wynik.",
            }, ensure_ascii=False, indent=2)

        # TRYB 2: OPEN (WYMAGA BEZHASŁOWEGO SUDO ZAINSTALOWANEGO PRZEZ install.sh --root-open)
        start_time = time.time()
        try:
            proc = subprocess.run(
                ["sudo", "-n", "/usr/bin/bash", "-c", cmd_str],
                capture_output=True,
                text=True,
                timeout=timeout,
                shell=False,
            )
            duration = time.time() - start_time
            out = proc.stdout or ""
            err = proc.stderr or ""
            code = proc.returncode

            # Sprawdzenie, czy sudo zgłasza brak uprawnień bez hasła
            if code != 0 and ("password is required" in err.lower() or "a password is required" in err.lower()):
                log_audit("root_exec", cmd_str, "sudo_denied", exit_code=code, duration=duration,
                          error="Brak reguły NOPASSWD w sudoers")
                return (
                    "Błąd bezpieczeństwa: Tryb ROOT_SHELL_MODE=open wymaga skonfigurowanego sudo bez hasła (sudo -n).\n"
                    "Aby bezpiecznie włączyć ten tryb, uruchom w terminalu:\n"
                    "  ./install.sh --root-open\n"
                    "Albo przełącz tryb w .env na: ROOT_SHELL_MODE=approve"
                )

            log_audit("root_exec", cmd_str, "completed", exit_code=code, duration=duration)

            res = [f"[WYKONANO JAKO ROOT (tryb open)]"]
            if out:
                res.append(out.strip())
            if err:
                res.append(f"[stderr]:\n{err.strip()}")
            if code != 0:
                res.append(f"\n[Kod zakończenia: {code}]")

            return clip("\n".join(res).strip(), 30000)

        except subprocess.TimeoutExpired:
            duration = time.time() - start_time
            log_audit("root_exec", cmd_str, "timeout", duration=duration)
            return f"Błąd: Przekroczono limit czasu polecenia root ({timeout}s)."
        except Exception as exc:
            duration = time.time() - start_time
            log_audit("root_exec", cmd_str, "error", duration=duration, error=str(exc))
            return f"Błąd wykonania root: {exc}"

    @mcp.tool()
    def root_pending() -> str:
        """Zwraca listę wszystkich poleceń root oczekujących na zatwierdzenie przez operatora w terminalu."""
        items = []
        for pf in sorted(pending_dir.glob("*.json")):
            try:
                data = json.loads(pf.read_text(encoding="utf-8"))
                items.append({
                    "id": data.get("id"),
                    "created_at": data.get("created_at"),
                    "command": data.get("command"),
                })
            except Exception:
                continue

        if not items:
            return "Brak poleceń oczekujących na zatwierdzenie w kolejce."
        return json.dumps(items, ensure_ascii=False, indent=2)

    @mcp.tool()
    def root_result(id: str) -> str:
        """Pobiera wynik wykonania polecenia root o podanym ID po zatwierdzeniu przez operatora."""
        task_id = id.strip()
        if not task_id:
            return "Błąd: Podaj ID zadania."

        res_file = results_dir / f"{task_id}.json"
        pen_file = pending_dir / f"{task_id}.json"

        if res_file.exists():
            try:
                data = json.loads(res_file.read_text(encoding="utf-8"))
                return json.dumps(data, ensure_ascii=False, indent=2)
            except Exception as err:
                return f"Błąd odczytu pliku wyniku: {err}"

        if pen_file.exists():
            return json.dumps({
                "status": "pending_approval",
                "id": task_id,
                "komunikat": "Polecenie wciąż oczekuje na zatwierdzenie przez człowieka w terminalu.",
                "instrukcja": "Operator musi uruchomić w terminalu: bin/root-approve",
            }, ensure_ascii=False, indent=2)

        return f"Nie znaleziono zadania o ID '{task_id}'. Być może zostało usunięte lub nigdy nie istniało."
