"""Wtyczka mostu komunikacyjnego AI i skrzynek wiadomości JSONL.

Udostępnia atomową skrzynkę wiadomości z blokowaniem plikowym (fcntl.flock)
oraz narzędzie zadawania pytań modelom AI (Google Gemini oraz API zgodne z OpenAI/Ollama).
"""
from __future__ import annotations

import fcntl
import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

import httpx


def _get_state_dir() -> Path:
    custom = os.environ.get("MCP_STATE_DIR")
    if custom:
        path = Path(custom).expanduser()
    else:
        path = Path.home() / ".local" / "state" / "debian-trixie-mcp"
    path.mkdir(parents=True, exist_ok=True)
    return path


def register(mcp, run: Callable, clip: Callable, workspace: Path):
    state_dir = _get_state_dir()
    messages_file = state_dir / "messages.jsonl"

    @mcp.tool()
    def post_message(sender: str, recipient: str, text: str) -> str:
        """Zapisuje nową wiadomość do wspólnej skrzynki JSONL z blokadą flock."""
        snd = sender.strip() or "anonim"
        rcp = recipient.strip() or "wszyscy"
        body = text.strip()
        if not body:
            return "Błąd: Treść wiadomości nie może być pusta."

        entry = {
            "id": uuid.uuid4().hex[:8],
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "sender": snd,
            "recipient": rcp,
            "text": body,
        }
        line = json.dumps(entry, ensure_ascii=False) + "\n"

        try:
            with messages_file.open("a", encoding="utf-8") as f:
                fcntl.flock(f.fileno(), fcntl.LOCK_EX)
                try:
                    f.write(line)
                    f.flush()
                finally:
                    fcntl.flock(f.fileno(), fcntl.LOCK_UN)
            return f"Wiadomość wysłana pomyślnie [id: {entry['id']}] do '{rcp}'."
        except Exception as err:
            return f"Błąd zapisu do skrzynki wiadomości: {err}"

    @mcp.tool()
    def read_messages(recipient: str = "", limit: int = 50) -> str:
        """Odczytuje ostatnie wiadomości ze wspólnej skrzynki JSONL z blokadą flock."""
        if not messages_file.exists():
            return "Skrzynka wiadomości jest pusta."

        target_rcp = recipient.strip().lower()
        max_items = max(1, min(limit, 200))
        entries = []

        try:
            with messages_file.open("r", encoding="utf-8", errors="replace") as f:
                fcntl.flock(f.fileno(), fcntl.LOCK_SH)
                try:
                    lines = f.readlines()
                finally:
                    fcntl.flock(f.fileno(), fcntl.LOCK_UN)

            for raw in lines:
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    data = json.loads(raw)
                    if target_rcp:
                        r = data.get("recipient", "").lower()
                        if r != target_rcp and r != "wszyscy":
                            continue
                    entries.append(data)
                except Exception:
                    continue

            recent = entries[-max_items:]
            if not recent:
                return f"Brak wiadomości dla odbiorcy '{recipient or 'wszystkich'}'."

            formatted = []
            for item in recent:
                ts = item.get("timestamp", "")[:19].replace("T", " ")
                snd = item.get("sender", "?")
                rcp = item.get("recipient", "?")
                txt = item.get("text", "")
                mid = item.get("id", "")
                formatted.append(f"[{ts}] ({mid}) {snd} -> {rcp}:\n{txt}\n")

            return clip("\n".join(formatted), 25000)
        except Exception as err:
            return f"Błąd odczytu skrzynki wiadomości: {err}"

    @mcp.tool()
    def ask_ai(prompt: str, provider: str = "gemini", model: str = "") -> str:
        """Zadaje pytanie zewnętrznemu lub lokalnemu modelowi AI (Gemini lub API zgodne z OpenAI/Ollama)."""
        p_type = provider.strip().lower()
        q = prompt.strip()
        if not q:
            return "Błąd: Zapytanie (prompt) nie może być puste."

        if p_type == "gemini":
            key = os.environ.get("GEMINI_API_KEY", "").strip()
            if not key:
                return "Błąd: Brak klucza GEMINI_API_KEY w pliku .env."
            m_name = model.strip() or "gemini-2.5-flash"
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{m_name}:generateContent?key={key}"
            payload = {
                "contents": [{"parts": [{"text": q}]}]
            }
            try:
                with httpx.Client(timeout=60.0) as client:
                    resp = client.post(url, json=payload)
                if resp.status_code != 200:
                    return f"Błąd API Gemini ({resp.status_code}): {resp.text}"
                data = resp.json()
                text = data["candidates"][0]["content"]["parts"][0]["text"]
                return clip(text, 25000)
            except Exception as err:
                return f"Wyjątek podczas zapytania do Gemini: {err}"

        elif p_type in ("openai", "ollama"):
            base = os.environ.get("OPENAI_API_BASE", "http://127.0.0.1:11434/v1").rstrip("/")
            key = os.environ.get("OPENAI_API_KEY", "ollama").strip() or "ollama"
            m_name = model.strip() or "llama3.2-vision:11b"
            url = f"{base}/chat/completions"
            payload = {
                "model": m_name,
                "messages": [{"role": "user", "content": q}],
                "temperature": 0.7,
            }
            headers = {"Authorization": f"Bearer {key}"}
            try:
                with httpx.Client(timeout=120.0) as client:
                    resp = client.post(url, json=payload, headers=headers)
                if resp.status_code != 200:
                    return f"Błąd API OpenAI/Ollama ({resp.status_code}): {resp.text}"
                data = resp.json()
                text = data["choices"][0]["message"]["content"]
                return clip(text, 25000)
            except Exception as err:
                return f"Wyjątek podczas zapytania do OpenAI/Ollama ({url}): {err}"
        else:
            return f"Błąd: Nieobsługiwany dostawca AI '{provider}'. Wybierz 'gemini' lub 'ollama'/'openai'."
