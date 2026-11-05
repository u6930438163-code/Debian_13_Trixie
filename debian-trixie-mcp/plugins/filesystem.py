"""Wtyczka operacji plikowych i repozytorium Git.

Wszystkie operacje plikowe są rygorystycznie ograniczone do katalogu roboczego
(sandbox, ochrona przed Path Traversal / ..). Zapis do .git i .env jest zablokowany.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Callable


def register(mcp, run: Callable, clip: Callable, workspace: Path):
    def resolve_safe(path_str: str) -> Path:
        raw = Path(path_str)
        if raw.is_absolute():
            # Przekształcamy na ścieżkę względną do workspace lub sprawdzamy
            try:
                candidate = raw.resolve()
                if candidate.is_relative_to(workspace.resolve()):
                    return candidate
            except ValueError:
                pass
            raise PermissionError(f"Dostęp zabroniony: Ścieżka '{path_str}' znajduje się poza katalogiem roboczym.")

        target = (workspace / raw).resolve()
        if not target.is_relative_to(workspace.resolve()):
            raise PermissionError(f"Próba ucieczki poza katalog roboczy (Path Traversal): '{path_str}'.")
        return target

    @mcp.tool()
    def list_dir(path: str = ".") -> str:
        """Wyświetla zawartość katalogu wewnątrz obszaru roboczego."""
        try:
            target = resolve_safe(path)
            if not target.exists():
                return f"Błąd: Ścieżka '{path}' nie istnieje."
            if not target.is_dir():
                return f"Błąd: Ścieżka '{path}' nie jest katalogiem."

            items = []
            for entry in sorted(target.iterdir()):
                rel = entry.relative_to(workspace)
                if entry.is_dir():
                    items.append(f"[KATALOG] {rel}/")
                elif entry.is_symlink():
                    items.append(f"[SYMLINK] {rel}")
                else:
                    size = entry.stat().st_size
                    items.append(f"[PLIK]    {rel} ({size} B)")

            if not items:
                return f"Katalog '{path}' jest pusty."
            return "\n".join(items)
        except Exception as err:
            return f"Błąd list_dir: {err}"

    @mcp.tool()
    def read_file(path: str, max_bytes: int = 262144) -> str:
        """Odczytuje zawartość pliku tekstowego z katalogu roboczego (maks. 256 KB)."""
        try:
            target = resolve_safe(path)
            if not target.exists():
                return f"Błąd: Plik '{path}' nie istnieje."
            if not target.is_file():
                return f"Błąd: Ścieżka '{path}' nie jest zwykłym plikiem."

            limit = max(1024, min(max_bytes, 1048576))
            with target.open("r", encoding="utf-8", errors="replace") as f:
                content = f.read(limit + 1)

            if len(content) > limit:
                return content[:limit] + f"\n\n... [Przycięto: plik przekracza limit {limit} bajtów]"
            return content
        except Exception as err:
            return f"Błąd odczytu pliku: {err}"

    @mcp.tool()
    def write_file(path: str, content: str) -> str:
        """Zapisuje zawartość do pliku w katalogu roboczym (zapis do .git i .env zablokowany)."""
        try:
            target = resolve_safe(path)

            # Blokada zapisu do katalogu .git
            if ".git" in target.parts:
                return "Błąd bezpieczeństwa: Zapis do katalogu .git jest zablokowany."

            # Blokada bezpośredniego nadpisania pliku .env
            if target.name == ".env":
                return "Błąd bezpieczeństwa: Bezpośrednie nadpisywanie pliku .env jest zablokowane ze względów bezpieczeństwa."

            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open("w", encoding="utf-8") as f:
                f.write(content)

            return f"Pomyślnie zapisano plik '{target.relative_to(workspace)}' ({len(content.encode('utf-8'))} B)."
        except Exception as err:
            return f"Błąd zapisu pliku: {err}"

    @mcp.tool()
    def git_status() -> str:
        """Sprawdza skrócony status repozytorium Git w katalogu roboczym (git status -s)."""
        code, out, err = run(["git", "-C", str(workspace), "status", "-s"], timeout=10)
        if code != 0:
            return f"Błąd git status (czy katalog to repozytorium git?): {err.strip()}"
        return out.strip() or "Drzewo robocze czyste (brak zmian)."

    @mcp.tool()
    def git_diff(cached: bool = False) -> str:
        """Wyświetla różnice w kodzie repozytorium Git (git diff)."""
        cmd = ["git", "-C", str(workspace), "diff"]
        if cached:
            cmd.append("--cached")
        code, out, err = run(cmd, timeout=15)
        if code != 0:
            return f"Błąd git diff: {err.strip()}"
        return clip(out.strip() or "Brak różnic w wybranym obszarze.", 20000)

    @mcp.tool()
    def git_log(max_count: int = 10) -> str:
        """Wyświetla historię ostatnich commitów w repozytorium Git."""
        limit = max(1, min(max_count, 100))
        cmd = ["git", "-C", str(workspace), "log", f"-n{limit}", "--oneline", "--decorate"]
        code, out, err = run(cmd, timeout=10)
        if code != 0:
            return f"Błąd git log: {err.strip()}"
        return clip(out.strip() or "Brak commitów w repozytorium.", 10000)

    @mcp.tool()
    def git_commit(message: str) -> str:
        """Tworzy commit w repozytorium Git z podaną wiadomością."""
        msg = message.strip()
        if not msg:
            return "Błąd: Wiadomość commita nie może być pusta."
        cmd = ["git", "-C", str(workspace), "commit", "-m", msg]
        code, out, err = run(cmd, timeout=15)
        if code != 0:
            return f"Błąd tworzenia commita: {err.strip() or out.strip()}"
        return out.strip()
