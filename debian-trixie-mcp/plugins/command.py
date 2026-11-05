"""Wtyczka bezpiecznego wykonywania dozwolonych komend (run_command).

Działa bez powłoki (brak shell=True), parsuje argumenty przez shlex.split,
sprawdza białą listę poleceń oraz blokuje niebezpieczne flagi.
"""
from __future__ import annotations

import shlex
from pathlib import Path
from typing import Callable

# Ściśle określona biała lista komend inspekcyjno-diagnostycznych
ALLOWED_BINARIES = frozenset({
    "ls", "cat", "grep", "egrep", "fgrep", "head", "tail", "wc", "sort",
    "uniq", "awk", "sed", "tr", "cut", "diff", "uname", "uptime", "df",
    "du", "free", "ps", "lsof", "ip", "ss", "ping", "traceroute", "dig",
    "host", "nslookup", "curl", "wget", "git", "python3", "node", "jq",
    "which", "whereis", "whoami", "id", "groups", "date", "arch",
    "find", "tree", "file", "env"
})

BLOCKED_CHARS = {"|", ";", "&", ">", "<", "`", "$", "(", ")"}


def register(mcp, run: Callable, clip: Callable, workspace: Path):
    @mcp.tool()
    def run_command(command: str) -> str:
        """Wykonuje dozwolone polecenie diagnostyczne z białej listy bez powłoki shell."""
        raw = command.strip()
        if not raw:
            return "Błąd: Podano puste polecenie."

        try:
            tokens = shlex.split(raw)
        except ValueError as err:
            return f"Błąd parsowania polecenia (shlex): {err}"

        if not tokens:
            return "Błąd: Brak argumentów po sparsowaniu polecenia."

        executable = tokens[0]

        # Sprawdzenie białej listy komend
        if executable not in ALLOWED_BINARIES:
            return (
                f"Błąd bezpieczeństwa: Polecenie '{executable}' nie znajduje się na liście "
                f"dozwolonych komend. Dozwolone narzędzia: {', '.join(sorted(ALLOWED_BINARIES))}."
            )

        # Sprawdzenie obecności znaków potoków/przekierowań w tokenach
        for token in tokens:
            if any(char in token for char in BLOCKED_CHARS):
                return f"Błąd bezpieczeństwa: Wykryto niedozwolony znak sterujący powłoki w argumencie '{token}'."

        # Blokada niebezpiecznych flag w komendzie 'find'
        if executable == "find":
            lower_tokens = [t.lower() for t in tokens]
            dangerous_find = {"-exec", "-execdir", "-ok", "-okdir", "-delete"}
            if any(arg in dangerous_find for arg in lower_tokens):
                return "Błąd bezpieczeństwa: Opcje -exec, -execdir, -ok oraz -delete w 'find' są zablokowane."

        # Blokada niebezpiecznych opcji w komendzie 'git'
        if executable == "git":
            lower_tokens = [t.lower() for t in tokens]
            if any(t in ("-c", "--exec-path", "--config-env", "clean", "reset", "push") for t in lower_tokens):
                return "Błąd bezpieczeństwa: Zablokowano modyfikację konfiguracji lub niebezpieczne operacje w git."

        # Blokada zapisu do plików przez curl / wget
        if executable in ("curl", "wget"):
            for t in tokens:
                if t in ("-o", "-O", "--output", "--output-document"):
                    return f"Błąd bezpieczeństwa: Zapis do pliku przez {executable} jest zabroniony w tym narzędziu."

        code, out, err = run(tokens, timeout=30, cwd=workspace)
        result = []
        if out:
            result.append(out.strip())
        if err:
            result.append(f"[stderr]:\n{err.strip()}")
        if code != 0:
            result.append(f"\n[Kod zakończenia: {code}]")

        combined = "\n".join(result).strip()
        return clip(combined or "Polecenie wykonane pomyślnie (brak wyjścia tekstowego).", 25000)
