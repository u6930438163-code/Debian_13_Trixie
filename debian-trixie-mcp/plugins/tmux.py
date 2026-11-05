"""Wtyczka inspekcji sesji terminalowych tmux.

Udostępnia narzędzia do listowania sesji, okien i przechwytywania zawartości bufora (pane).
Wysyłanie klawiszy (sterowanie) wymaga ustawienia TMUX_ALLOW_SEND=1 w pliku .env.
"""
from __future__ import annotations

import os
import re
import shutil
from pathlib import Path
from typing import Callable

TARGET_REGEX = re.compile(r"^[a-zA-Z0-9_\-\.:%]+$")


def register(mcp, run: Callable, clip: Callable, workspace: Path):
    def has_tmux() -> bool:
        return shutil.which("tmux") is not None

    @mcp.tool()
    def tmux_list_sessions() -> str:
        """Wyświetla listę aktywnych sesji tmux w systemie."""
        if not has_tmux():
            return "Narzędzie 'tmux' nie jest zainstalowane w systemie."
        code, out, err = run(["tmux", "list-sessions"], timeout=10)
        if code != 0:
            if "no server running" in err.lower() or "no server running" in out.lower():
                return "Brak uruchomionego serwera tmux (brak aktywnych sesji)."
            return f"Błąd tmux list-sessions: {err.strip() or out.strip()}"
        return out.strip() or "Brak sesji tmux."

    @mcp.tool()
    def tmux_list_windows(session: str = "") -> str:
        """Wyświetla okna w wybranej sesji tmux (lub bieżącej)."""
        if not has_tmux():
            return "Narzędzie 'tmux' nie jest zainstalowane w systemie."
        cmd = ["tmux", "list-windows"]
        if session.strip():
            s = session.strip()
            if not TARGET_REGEX.match(s):
                return "Błąd: Nieprawidłowa nazwa sesji tmux."
            cmd.extend(["-t", s])
        code, out, err = run(cmd, timeout=10)
        if code != 0:
            return f"Błąd tmux list-windows: {err.strip() or out.strip()}"
        return out.strip() or "Brak okien tmux."

    @mcp.tool()
    def tmux_capture_pane(target: str = "", lines: int = 50) -> str:
        """Pobiera zawartość tekstową (bufor) z wybranego panelu tmux (capture-pane)."""
        if not has_tmux():
            return "Narzędzie 'tmux' nie jest zainstalowane w systemie."
        limit = max(1, min(lines, 500))
        cmd = ["tmux", "capture-pane", "-p", f"-S-{limit}"]
        if target.strip():
            t = target.strip()
            if not TARGET_REGEX.match(t):
                return "Błąd: Nieprawidłowy cel panelu tmux."
            cmd.extend(["-t", t])
        code, out, err = run(cmd, timeout=10)
        if code != 0:
            return f"Błąd capture-pane: {err.strip() or out.strip()}"
        return clip(out.strip() or "Panel tmux jest pusty.", 20000)

    @mcp.tool()
    def tmux_send_keys(target: str, keys: str) -> str:
        """Wysyła polecenie lub sekwencję klawiszy do panelu tmux. Wymaga TMUX_ALLOW_SEND=1."""
        if os.environ.get("TMUX_ALLOW_SEND", "").strip() != "1":
            return (
                "Błąd bezpieczeństwa: Wysyłanie klawiszy do sesji tmux jest wyłączone. "
                "Aby zezwolić, ustaw TMUX_ALLOW_SEND=1 w pliku .env."
            )
        t = target.strip()
        if not TARGET_REGEX.match(t):
            return "Błąd: Nieprawidłowy identyfikator celu panelu tmux (np. 'sesja:0.0')."
        k = keys.strip()
        if not k:
            return "Błąd: Treść klawiszy do wysłania nie może być pusta."

        cmd = ["tmux", "send-keys", "-t", t, k, "C-m"]
        code, out, err = run(cmd, timeout=10)
        if code != 0:
            return f"Błąd send-keys: {err.strip() or out.strip()}"
        return f"Pomyślnie wysłano sekwencję klawiszy do panelu '{t}'."
