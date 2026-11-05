"""Wtyczka dedykowana modelom Google Gemini.

Zawiera obsługę listy modeli, czat z trwałą historią sesji, zadawanie pytań
o pliki z blokadą .git i .env oraz pętlę agenta z bezpiecznym function calling.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Callable

import httpx


def _sessions_dir() -> Path:
    custom = os.environ.get("MCP_STATE_DIR")
    if custom:
        path = Path(custom).expanduser() / "gemini_sessions"
    else:
        path = Path.home() / ".local" / "state" / "debian-trixie-mcp" / "gemini_sessions"
    path.mkdir(parents=True, exist_ok=True)
    return path


def register(mcp, run: Callable, clip: Callable, workspace: Path):
    sessions_dir = _sessions_dir()

    def get_api_key() -> str:
        key = os.environ.get("GEMINI_API_KEY", "").strip()
        if not key:
            raise ValueError("Brak klucza GEMINI_API_KEY w pliku .env.")
        return key

    @mcp.tool()
    def gemini_models() -> str:
        """Pobiera listę dostępnych modeli Google Gemini z Google AI Studio."""
        try:
            key = get_api_key()
            url = f"https://generativelanguage.googleapis.com/v1beta/models?key={key}"
            with httpx.Client(timeout=20.0) as client:
                resp = client.get(url)
            if resp.status_code != 200:
                return f"Błąd API Gemini ({resp.status_code}): {resp.text}"
            data = resp.json()
            models = []
            for m in data.get("models", []):
                name = m.get("name", "").replace("models/", "")
                methods = m.get("supportedGenerationMethods", [])
                if "generateContent" in methods:
                    desc = m.get("displayName", "")
                    models.append(f"- {name} ({desc})")
            return "Dostępne modele Gemini:\n" + "\n".join(sorted(models))
        except Exception as err:
            return f"Błąd gemini_models: {err}"

    @mcp.tool()
    def gemini_chat(message: str, session_id: str = "default") -> str:
        """Prowadzi wieloturową rozmowę z Gemini, zachowując historię sesji w pliku JSON."""
        msg = message.strip()
        if not msg:
            return "Błąd: Wiadomość nie może być pusta."

        clean_id = re.sub(r"[^a-zA-Z0-9_\-]", "_", session_id.strip() or "default")
        sess_file = sessions_dir / f"{clean_id}.json"

        history = []
        if sess_file.exists():
            try:
                history = json.loads(sess_file.read_text(encoding="utf-8"))
            except Exception:
                history = []

        history.append({"role": "user", "parts": [{"text": msg}]})

        try:
            key = get_api_key()
            url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={key}"
            payload = {"contents": history}

            with httpx.Client(timeout=60.0) as client:
                resp = client.post(url, json=payload)
            if resp.status_code != 200:
                return f"Błąd API Gemini ({resp.status_code}): {resp.text}"

            data = resp.json()
            reply = data["candidates"][0]["content"]["parts"][0]["text"]
            history.append({"role": "model", "parts": [{"text": reply}]})

            # Zapisujemy historię (maksymalnie 40 ostatnich tur)
            sess_file.write_text(json.dumps(history[-40:], ensure_ascii=False, indent=2), encoding="utf-8")
            return reply
        except Exception as err:
            return f"Błąd gemini_chat: {err}"

    @mcp.tool()
    def gemini_ask_about_files(paths: list[str], question: str) -> str:
        """Wczytuje podane pliki z katalogu roboczego (blokada .git i .env) i pyta o nie model Gemini."""
        q = question.strip()
        if not q:
            return "Błąd: Pytanie nie może być puste."
        if not paths:
            return "Błąd: Nie podano żadnych ścieżek do plików."

        file_contexts = []
        for p_str in paths:
            target = (workspace / p_str.strip()).resolve()
            if not target.is_relative_to(workspace.resolve()):
                return f"Błąd bezpieczeństwa: Ścieżka '{p_str}' wychodzi poza katalog roboczy."
            if ".git" in target.parts or target.name == ".env":
                return f"Błąd bezpieczeństwa: Dostęp do pliku/katalogu '{p_str}' (.git/.env) jest zablokowany."
            if not target.exists() or not target.is_file():
                return f"Błąd: Plik '{p_str}' nie istnieje lub nie jest plikiem."

            content = target.read_text(encoding="utf-8", errors="replace")[:40000]
            rel = target.relative_to(workspace)
            file_contexts.append(f"=== PLIK: {rel} ===\n{content}\n")

        full_prompt = (
            "Przeanalizuj poniższe pliki z projektu i odpowiedz na pytanie użytkownika:\n\n"
            + "\n".join(file_contexts)
            + f"\n\nPYTANIE UŻYTKOWNIKA:\n{q}"
        )

        try:
            key = get_api_key()
            url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={key}"
            payload = {"contents": [{"parts": [{"text": full_prompt}]}]}

            with httpx.Client(timeout=90.0) as client:
                resp = client.post(url, json=payload)
            if resp.status_code != 200:
                return f"Błąd API Gemini ({resp.status_code}): {resp.text}"
            data = resp.json()
            return data["candidates"][0]["content"]["parts"][0]["text"]
        except Exception as err:
            return f"Błąd gemini_ask_about_files: {err}"

    @mcp.tool()
    def gemini_agent(task: str, max_steps: int = 5) -> str:
        """Pętla agenta Gemini (Function Calling) z narzędziami tylko do odczytu (.env i .git zablokowane)."""
        t = task.strip()
        if not t:
            return "Błąd: Zadanie dla agenta nie może być puste."

        try:
            key = get_api_key()
        except Exception as err:
            return str(err)

        # Definicja narzędzi dla Gemini
        tools_spec = [
            {
                "functionDeclarations": [
                    {
                        "name": "list_files",
                        "description": "Zwraca listę plików w katalogu roboczym.",
                        "parameters": {
                            "type": "OBJECT",
                            "properties": {"subdir": {"type": "STRING", "description": "Podkatalog (opcjonalny)"}},
                        },
                    },
                    {
                        "name": "read_source_file",
                        "description": "Odczytuje zawartość pliku (tylko odczyt, brak dostępu do .git i .env).",
                        "parameters": {
                            "type": "OBJECT",
                            "properties": {"filepath": {"type": "STRING", "description": "Ścieżka do pliku"}},
                            "required": ["filepath"],
                        },
                    },
                    {
                        "name": "get_system_summary",
                        "description": "Zwraca podsumowanie stanu systemu Debian 13.",
                        "parameters": {"type": "OBJECT", "properties": {}},
                    },
                ]
            }
        ]

        def exec_local_tool(name: str, args: dict) -> str:
            if name == "list_files":
                sub = args.get("subdir", ".") or "."
                target = (workspace / sub).resolve()
                if not target.is_relative_to(workspace.resolve()) or not target.exists():
                    return "Błąd: Nieprawidłowy katalog."
                entries = [p.name + ("/" if p.is_dir() else "") for p in target.iterdir() if p.name != ".git"]
                return ", ".join(sorted(entries)[:100]) or "Katalog pusty."
            elif name == "read_source_file":
                fp = args.get("filepath", "")
                target = (workspace / fp).resolve()
                if not target.is_relative_to(workspace.resolve()):
                    return "Błąd: Ścieżka poza katalogiem roboczym."
                if ".git" in target.parts or target.name == ".env":
                    return "Błąd: Dostęp do .git/.env jest zablokowany."
                if not target.exists() or not target.is_file():
                    return "Błąd: Plik nie istnieje."
                return target.read_text(encoding="utf-8", errors="replace")[:10000]
            elif name == "get_system_summary":
                c, out, _ = run(["uptime", "-p"], timeout=5)
                return f"Debian 13 Trixie, uptime: {out.strip() if c == 0 else 'N/A'}"
            return f"Nieznane narzędzie: {name}"

        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={key}"
        contents = [
            {
                "role": "user",
                "parts": [{"text": f"Rozwiąż poniższe zadanie używając dostępnych narzędzi do odczytu:\n{t}"}],
            }
        ]

        steps = max(1, min(max_steps, 8))
        with httpx.Client(timeout=60.0) as client:
            for _ in range(steps):
                payload = {"contents": contents, "tools": tools_spec}
                resp = client.post(url, json=payload)
                if resp.status_code != 200:
                    return f"Błąd w pętli agenta ({resp.status_code}): {resp.text}"

                data = resp.json()
                candidate = data.get("candidates", [{}])[0]
                content = candidate.get("content", {})
                parts = content.get("parts", [])

                func_call = None
                text_part = ""
                for part in parts:
                    if "functionCall" in part:
                        func_call = part["functionCall"]
                    if "text" in part:
                        text_part += part["text"]

                contents.append(content)

                if func_call:
                    fname = func_call.get("name", "")
                    fargs = func_call.get("args", {})
                    call_result = exec_local_tool(fname, fargs)
                    contents.append({
                        "role": "user",
                        "parts": [{
                            "functionResponse": {
                                "name": fname,
                                "response": {"result": call_result},
                            }
                        }],
                    })
                else:
                    return text_part or "Zadanie ukończone bez dodatkowej odpowiedzi."

        return "Osiągnięto limit kroków pętli agenta bez ostatecznej odpowiedzi."
