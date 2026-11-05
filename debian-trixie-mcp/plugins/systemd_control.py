"""Wtyczka kontrolowanego sterowania wybranymi usługami systemd.

Zezwala na operacje start/stop/restart/reload/status wyłącznie dla usług
jawnie wymienionych w zmiennej środowiskowej SYSTEMD_ALLOWED_SERVICES.
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Callable

SERVICE_REGEX = re.compile(r"^[a-zA-Z0-9_\-\.@:]+$")


def register(mcp, run: Callable, clip: Callable, workspace: Path):
    def get_allowed_services() -> set[str]:
        raw = os.environ.get("SYSTEMD_ALLOWED_SERVICES", "nginx,postgresql,ollama,docker,redis")
        return {s.strip().lower() for s in raw.split(",") if s.strip()}

    @mcp.tool()
    def systemd_service_action(service: str, action: str) -> str:
        """Wykonuje akcję (start/stop/restart/reload/status) wyłącznie na dozwolonej usłudze systemd."""
        svc = service.strip().lower()
        act = action.strip().lower()

        if not SERVICE_REGEX.match(svc):
            return "Błąd: Nieprawidłowa nazwa usługi systemd."

        if act not in ("start", "stop", "restart", "reload", "status"):
            return "Błąd: Dozwolone akcje to: start, stop, restart, reload, status."

        allowed = get_allowed_services()
        # Akceptujemy zarówno z końcówką .service jak i bez niej
        base_svc = svc[:-8] if svc.endswith(".service") else svc
        if base_svc not in allowed and svc not in allowed:
            return (
                f"Błąd bezpieczeństwa: Usługa '{svc}' nie znajduje się na liście dozwolonych "
                f"usług systemd (SYSTEMD_ALLOWED_SERVICES: {', '.join(sorted(allowed)) or 'brak'})."
            )

        cmd = ["systemctl", act, svc, "--no-pager"]
        code, out, err = run(cmd, timeout=20)
        text = out if out.strip() else err
        if code != 0:
            return f"Błąd systemctl {act} {svc} (kod {code}):\n{text.strip()}"
        return f"Polecenie 'systemctl {act} {svc}' wykonane pomyślnie.\n{text.strip()}".strip()
