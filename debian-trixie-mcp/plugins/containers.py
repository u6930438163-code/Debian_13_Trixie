"""Wtyczka inspekcji i zarządzania kontenerami (Docker / Podman).

Domyślnie udostępnia podgląd kontenerów, obrazów i logów.
Operacje sterowania (start/stop/restart) wymagają ustawienia CONTAINER_ALLOW_CONTROL=1 w .env.
"""
from __future__ import annotations

import os
import re
import shutil
from pathlib import Path
from typing import Callable

ID_REGEX = re.compile(r"^[a-zA-Z0-9_\-\.]+$")


def register(mcp, run: Callable, clip: Callable, workspace: Path):
    def get_engine() -> str | None:
        if shutil.which("docker"):
            return "docker"
        if shutil.which("podman"):
            return "podman"
        return None

    @mcp.tool()
    def container_list(all: bool = False) -> str:
        """Wyświetla listę kontenerów (docker ps / podman ps)."""
        engine = get_engine()
        if not engine:
            return "W systemie nie znaleziono ani 'docker', ani 'podman'."
        cmd = [engine, "ps"]
        if all:
            cmd.append("-a")
        code, out, err = run(cmd, timeout=15)
        if code != 0:
            return f"Błąd {engine} ps: {err.strip() or out.strip()}"
        return clip(out.strip() or "Brak uruchomionych kontenerów.", 15000)

    @mcp.tool()
    def container_images() -> str:
        """Wyświetla listę lokalnych obrazów kontenerowych."""
        engine = get_engine()
        if not engine:
            return "W systemie nie znaleziono ani 'docker', ani 'podman'."
        code, out, err = run([engine, "images"], timeout=15)
        if code != 0:
            return f"Błąd {engine} images: {err.strip() or out.strip()}"
        return clip(out.strip() or "Brak obrazów kontenerowych.", 15000)

    @mcp.tool()
    def container_logs(container_id: str, tail: int = 50) -> str:
        """Pobiera ostatnie linie logów wybranego kontenera."""
        cid = container_id.strip()
        if not ID_REGEX.match(cid):
            return "Błąd: Nieprawidłowy identyfikator kontenera."
        engine = get_engine()
        if not engine:
            return "W systemie nie znaleziono ani 'docker', ani 'podman'."
        limit = max(1, min(tail, 500))
        code, out, err = run([engine, "logs", "--tail", str(limit), cid], timeout=20)
        text = out if out.strip() else err
        if code != 0:
            return f"Błąd odczytu logów kontenera {cid}: {err.strip()}"
        return clip(text.strip() or f"Brak logów dla kontenera {cid}.", 20000)

    @mcp.tool()
    def container_action(container_id: str, action: str) -> str:
        """Wykonuje akcję na kontenerze (start/stop/restart). Wymaga flagi CONTAINER_ALLOW_CONTROL=1."""
        if os.environ.get("CONTAINER_ALLOW_CONTROL", "").strip() != "1":
            return (
                "Błąd bezpieczeństwa: Sterowanie kontenerami jest zablokowane. "
                "Aby zezwolić na zatrzymywanie lub uruchamianie, ustaw CONTAINER_ALLOW_CONTROL=1 w .env."
            )
        cid = container_id.strip()
        act = action.strip().lower()
        if not ID_REGEX.match(cid):
            return "Błąd: Nieprawidłowy identyfikator kontenera."
        if act not in ("start", "stop", "restart"):
            return "Błąd: Dozwolone akcje na kontenerze to: start, stop, restart."
        engine = get_engine()
        if not engine:
            return "W systemie nie znaleziono ani 'docker', ani 'podman'."

        code, out, err = run([engine, act, cid], timeout=30)
        if code != 0:
            return f"Błąd wykonania {act} na {cid}: {err.strip() or out.strip()}"
        return f"Pomyślnie wykonano '{act}' dla kontenera {cid}."
