"""Wtyczka wysyłania powiadomień przez ntfy (HTTP).

Umożliwia modelom AI powiadamianie operatora na telefon lub pulpit
za pośrednictwem otwartego protokołu i serwera ntfy.
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Callable

import httpx

TOPIC_REGEX = re.compile(r"^[a-zA-Z0-9_\-]+$")


def register(mcp, run: Callable, clip: Callable, workspace: Path):
    @mcp.tool()
    def notify_ntfy(topic: str, message: str, title: str = "", priority: int = 3, server_url: str = "") -> str:
        """Wysyła powiadomienie push do tematu ntfy (np. na telefon lub pulpit)."""
        top = topic.strip()
        msg = message.strip()
        if not top or not TOPIC_REGEX.match(top):
            return "Błąd: Nieprawidłowa nazwa tematu ntfy (dozwolone znaki: litery, cyfry, myślnik, podkreślenie)."
        if not msg:
            return "Błąd: Treść powiadomienia nie może być pusta."

        base = (server_url.strip() or os.environ.get("NTFY_SERVER", "https://ntfy.sh")).rstrip("/")
        url = f"{base}/{top}"

        prio = max(1, min(priority, 5))
        headers = {
            "Priority": str(prio),
            "User-Agent": "Debian-Trixie-MCP-Notify/1.0",
        }
        if title.strip():
            headers["Title"] = title.strip().encode("utf-8").decode("latin-1", errors="replace")

        try:
            with httpx.Client(timeout=15.0) as client:
                resp = client.post(url, content=msg.encode("utf-8"), headers=headers)
            if resp.status_code != 200:
                return f"Błąd serwera ntfy ({resp.status_code}): {resp.text}"
            return f"Powiadomienie pomyślnie wysłane do tematu '{top}' (priorytet {prio})."
        except Exception as err:
            return f"Błąd podczas wysyłania powiadomienia ntfy: {err}"
