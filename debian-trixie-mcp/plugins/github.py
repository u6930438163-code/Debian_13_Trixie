"""Wtyczka integracji z API GitHub (REST).

Obsługuje pobieranie informacji o użytkowniku, repozytoriach, zgłoszeniach (Issues)
i Pull Requestach. Modyfikacje (zapis) wymagają flagi GITHUB_ALLOW_WRITE=1.
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Callable

import httpx

REPO_REGEX = re.compile(r"^[a-zA-Z0-9_\-\.]+/[a-zA-Z0-9_\-\.]+$")


def register(mcp, run: Callable, clip: Callable, workspace: Path):
    def get_headers() -> dict[str, str]:
        token = os.environ.get("GITHUB_TOKEN", "").strip()
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "Debian-Trixie-MCP-Server",
        }
        if token:
            headers["Authorization"] = f"Bearer {token}"
        return headers

    @mcp.tool()
    def github_user() -> str:
        """Pobiera informacje o uwierzytelnionym użytkowniku GitHub (zgodnie z GITHUB_TOKEN)."""
        headers = get_headers()
        if "Authorization" not in headers:
            return "Błąd: Brak zdefiniowanego tokenu GITHUB_TOKEN w pliku .env."
        try:
            with httpx.Client(timeout=15.0) as client:
                resp = client.get("https://api.github.com/user", headers=headers)
            if resp.status_code != 200:
                return f"Błąd GitHub API ({resp.status_code}): {resp.text}"
            data = resp.json()
            return f"Użytkownik: {data.get('login')} ({data.get('name')})\nProfil: {data.get('html_url')}\nPubliczne repozytoria: {data.get('public_repos')}"
        except Exception as err:
            return f"Błąd połączenia z GitHubem: {err}"

    @mcp.tool()
    def github_repo(owner_repo: str) -> str:
        """Pobiera podstawowe informacje o repozytorium GitHub (np. 'torvalds/linux')."""
        target = owner_repo.strip()
        if not REPO_REGEX.match(target):
            return "Błąd: Nieprawidłowy format repozytorium. Wymagany format: 'właściciel/nazwa'."
        headers = get_headers()
        try:
            with httpx.Client(timeout=15.0) as client:
                resp = client.get(f"https://api.github.com/repos/{target}", headers=headers)
            if resp.status_code != 200:
                return f"Błąd GitHub API ({resp.status_code}): {resp.text}"
            d = resp.json()
            lines = [
                f"Repozytorium: {d.get('full_name')}",
                f"Opis: {d.get('description') or 'Brak'}",
                f"Gwiazdki (Stars): {d.get('stargazers_count')}",
                f"Forks: {d.get('forks_count')}",
                f"Domyślna gałąź: {d.get('default_branch')}",
                f"URL: {d.get('html_url')}",
            ]
            return "\n".join(lines)
        except Exception as err:
            return f"Błąd pobierania danych repozytorium: {err}"

    @mcp.tool()
    def github_issues(owner_repo: str, state: str = "open") -> str:
        """Wyświetla listę zgłoszeń (Issues) z repozytorium GitHub."""
        target = owner_repo.strip()
        if not REPO_REGEX.match(target):
            return "Błąd: Nieprawidłowy format repozytorium. Wymagany format: 'właściciel/nazwa'."
        st = state.strip().lower() if state.strip().lower() in ("open", "closed", "all") else "open"
        headers = get_headers()
        try:
            with httpx.Client(timeout=20.0) as client:
                resp = client.get(f"https://api.github.com/repos/{target}/issues?state={st}&per_page=20", headers=headers)
            if resp.status_code != 200:
                return f"Błąd GitHub API ({resp.status_code}): {resp.text}"
            data = resp.json()
            if not data:
                return f"Brak zgłoszeń (stan: {st}) w repozytorium {target}."
            lines = []
            for item in data:
                # Odsiewamy pull requesty (GitHub zwraca PR w endpoint /issues)
                if "pull_request" in item:
                    continue
                num = item.get("number")
                title = item.get("title")
                user = item.get("user", {}).get("login", "?")
                lines.append(f"#{num} [{item.get('state')}]: {title} (autor: {user})")
            return "\n".join(lines) or "Brak zwykłych zgłoszeń na tej liście."
        except Exception as err:
            return f"Błąd pobierania issues: {err}"

    @mcp.tool()
    def github_pulls(owner_repo: str, state: str = "open") -> str:
        """Wyświetla listę Pull Requestów z repozytorium GitHub."""
        target = owner_repo.strip()
        if not REPO_REGEX.match(target):
            return "Błąd: Nieprawidłowy format repozytorium. Wymagany format: 'właściciel/nazwa'."
        st = state.strip().lower() if state.strip().lower() in ("open", "closed", "all") else "open"
        headers = get_headers()
        try:
            with httpx.Client(timeout=20.0) as client:
                resp = client.get(f"https://api.github.com/repos/{target}/pulls?state={st}&per_page=20", headers=headers)
            if resp.status_code != 200:
                return f"Błąd GitHub API ({resp.status_code}): {resp.text}"
            data = resp.json()
            if not data:
                return f"Brak Pull Requestów (stan: {st}) w repozytorium {target}."
            lines = []
            for item in data:
                num = item.get("number")
                title = item.get("title")
                user = item.get("user", {}).get("login", "?")
                branch = item.get("head", {}).get("ref", "?")
                lines.append(f"PR #{num} [{item.get('state')}]: {title} (gałąź: {branch}, autor: {user})")
            return "\n".join(lines)
        except Exception as err:
            return f"Błąd pobierania pull requests: {err}"

    @mcp.tool()
    def github_create_issue(owner_repo: str, title: str, body: str) -> str:
        """Tworzy nowe zgłoszenie (Issue) w repozytorium. Wymaga flagi GITHUB_ALLOW_WRITE=1."""
        if os.environ.get("GITHUB_ALLOW_WRITE", "").strip() != "1":
            return (
                "Błąd bezpieczeństwa: Operacje zapisu na GitHubie są wyłączone. "
                "Aby zezwolić na tworzenie zgłoszeń, ustaw GITHUB_ALLOW_WRITE=1 w pliku .env."
            )
        target = owner_repo.strip()
        if not REPO_REGEX.match(target):
            return "Błąd: Nieprawidłowy format repozytorium. Wymagany format: 'właściciel/nazwa'."
        t = title.strip()
        if not t:
            return "Błąd: Tytuł zgłoszenia nie może być pusty."

        headers = get_headers()
        if "Authorization" not in headers:
            return "Błąd: Brak zdefiniowanego tokenu GITHUB_TOKEN w pliku .env."

        try:
            payload = {"title": t, "body": body.strip()}
            with httpx.Client(timeout=20.0) as client:
                resp = client.post(f"https://api.github.com/repos/{target}/issues", json=payload, headers=headers)
            if resp.status_code not in (200, 201):
                return f"Błąd tworzenia zgłoszenia ({resp.status_code}): {resp.text}"
            d = resp.json()
            return f"Pomyślnie utworzono Issue #{d.get('number')}: {d.get('html_url')}"
        except Exception as err:
            return f"Błąd podczas tworzenia issue: {err}"
