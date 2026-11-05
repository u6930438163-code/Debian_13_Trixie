"""Wtyczka systemowa dla Debiana 13 Trixie.

Udostępnia narzędzia do inspekcji parametrów systemu, menedżera APT
oraz usług i dzienników systemd.
"""
from __future__ import annotations

import os
import platform
import re
from pathlib import Path
from typing import Callable

PACKAGE_REGEX = re.compile(r"^[a-zA-Z0-9_\-\.\+]+$")
SERVICE_REGEX = re.compile(r"^[a-zA-Z0-9_\-\.@:]+$")
QUERY_REGEX = re.compile(r"^[a-zA-Z0-9_\-\.\+ ]+$")


def register(mcp, run: Callable, clip: Callable, workspace: Path):
    @mcp.tool()
    def system_info() -> str:
        """Pobiera kluczowe informacje o maszynie Debian 13: jądro, wersja, uptime, CPU, RAM i dysk."""
        debian_ver = "Nieznana"
        try:
            if Path("/etc/debian_version").exists():
                debian_ver = Path("/etc/debian_version").read_text(encoding="utf-8").strip()
        except Exception:
            pass

        code_uptime, out_uptime, _ = run(["uptime", "-p"], timeout=5)
        code_free, out_free, _ = run(["free", "-h"], timeout=5)
        code_df, out_df, _ = run(["df", "-h", "/"], timeout=5)

        info_lines = [
            f"Host: {platform.node()}",
            f"Dystrybucja: Debian {debian_ver} (Trixie)",
            f"Jądro: {platform.release()} ({platform.machine()})",
            f"Czas pracy (uptime): {out_uptime.strip() if code_uptime == 0 else 'N/A'}",
            f"Liczba rdzeni CPU: {os.cpu_count() or 1}",
            "",
            "--- Pamięć RAM (free -h) ---",
            out_free.strip() if code_free == 0 else "Brak danych o pamięci RAM",
            "",
            "--- Dysk główny / (df -h /) ---",
            out_df.strip() if code_df == 0 else "Brak danych o dysku",
        ]
        return "\n".join(info_lines)

    @mcp.tool()
    def apt_search(query: str) -> str:
        """Wyszukuje pakiety w repozytorium APT Debiana (apt-cache search)."""
        q = query.strip()
        if not q or not QUERY_REGEX.match(q):
            return "Błąd: Zapytanie zawiera niedozwolone znaki lub jest puste."
        code, out, err = run(["apt-cache", "search", q], timeout=15)
        if code != 0:
            return f"Błąd podczas wyszukiwania pakietów: {err.strip()}"
        return clip(out.strip() or "Nie znaleziono żadnych pasujących pakietów.", 12000)

    @mcp.tool()
    def apt_show(package: str) -> str:
        """Wyświetla szczegółowe metadane pakietu z bazy APT (apt-cache show)."""
        pkg = package.strip()
        if not pkg or not PACKAGE_REGEX.match(pkg):
            return "Błąd: Nieprawidłowa nazwa pakietu APT."
        code, out, err = run(["apt-cache", "show", pkg], timeout=10)
        if code != 0:
            return f"Błąd lub pakiet '{pkg}' nie istnieje: {err.strip()}"
        return clip(out.strip(), 12000)

    @mcp.tool()
    def apt_upgradable() -> str:
        """Wyświetla listę pakietów gotowych do aktualizacji w systemie (apt list --upgradable)."""
        code, out, err = run(["apt", "list", "--upgradable"], timeout=15)
        if code != 0:
            return f"Błąd sprawdzania aktualizacji: {err.strip()}"
        return clip(out.strip() or "Wszystkie pakiety są aktualne.", 12000)

    @mcp.tool()
    def systemd_status(service: str) -> str:
        """Sprawdza stan usługi systemd (systemctl status <service>)."""
        svc = service.strip()
        if not svc or not SERVICE_REGEX.match(svc):
            return "Błąd: Nieprawidłowa nazwa usługi systemd."
        code, out, err = run(["systemctl", "status", svc, "--no-pager"], timeout=10)
        text = out if out.strip() else err
        return clip(text.strip() or f"Brak wyjścia dla usługi {svc}", 12000)

    @mcp.tool()
    def journal_tail(lines: int = 50, unit: str = "") -> str:
        """Odczytuje ostatnie wpisy z dziennika systemd (journalctl)."""
        limit = max(1, min(lines, 500))
        cmd = ["journalctl", "-n", str(limit), "--no-pager"]
        if unit.strip():
            u = unit.strip()
            if not SERVICE_REGEX.match(u):
                return "Błąd: Nieprawidłowa nazwa jednostki journalctl."
            cmd.extend(["-u", u])
        code, out, err = run(cmd, timeout=15)
        if code != 0:
            return f"Błąd odczytu dziennika: {err.strip()}"
        return clip(out.strip() or "Dziennik systemd jest pusty dla podanych kryteriów.", 20000)
