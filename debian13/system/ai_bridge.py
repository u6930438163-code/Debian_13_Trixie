#!/usr/bin/env python3
"""AI Bridge: Secure bidirectional messaging between Gemini and OpenAI APIs,
integrating Debian 13 MCP read-only tools and session history.
"""
from __future__ import annotations

import json
import os
import urllib.request
import urllib.error
import uuid
from pathlib import Path

import mcp_server

MAX_MESSAGE_CHARS = 4000
MAX_SESSIONS = 64
MAX_HISTORY_MESSAGES = 20
SESSIONS: dict[str, list[dict]] = {}


class BridgeError(Exception):
    """Safe HTTP failure; never include credentials or provider URLs."""

    def __init__(self, status: int, code: str):
        super().__init__(code)
        self.status = status
        self.code = code


def call_openai_api(messages: list[dict], api_key: str, model: str = "gpt-4o-mini") -> str:
    if not api_key:
        raise ValueError("OPENAI_API_KEY is not configured")
    url = "https://api.openai.com/v1/chat/completions"
    payload = {
        "model": model,
        "messages": messages,
        "max_tokens": 1000,
    }
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as response:
            result = json.loads(response.read().decode("utf-8"))
            return result["choices"][0]["message"]["content"]
    except Exception as exc:
        raise RuntimeError("OpenAI provider request failed") from None


def call_gemini_api(prompt: str, api_key: str, model: str = "gemini-1.5-flash") -> str:
    if not api_key:
        raise ValueError("GEMINI_API_KEY is not configured")
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    payload = {
        "contents": [{"parts": [{"text": prompt}]}]
    }
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json", "x-goog-api-key": api_key},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as response:
            result = json.loads(response.read().decode("utf-8"))
            candidates = result.get("candidates", [])
            if candidates:
                parts = candidates[0].get("content", {}).get("parts", [])
                if parts:
                    return parts[0].get("text", "")
            return "No response text from Gemini."
    except Exception as exc:
        raise RuntimeError("Gemini provider request failed") from None


def execute_bridge_session(session_id: str, prompt: str, use_openai: bool = True) -> dict:
    """One authenticated model turn, sharing bounded conversation context across providers.

    This is an API bridge. It is not a connection to the user's private ChatGPT conversation.
    """
    if not isinstance(prompt, str) or not 1 <= len(prompt.strip()) <= MAX_MESSAGE_CHARS:
        raise BridgeError(400, "invalid_message")
    if not isinstance(use_openai, bool):
        raise BridgeError(400, "invalid_provider")
    if session_id:
        if not isinstance(session_id, str):
            raise BridgeError(400, "invalid_session")
        try:
            session_id = str(uuid.UUID(session_id))
        except (ValueError, AttributeError):
            raise BridgeError(400, "invalid_session") from None
    else:
        session_id = str(uuid.uuid4())

    provider = "openai" if use_openai else "gemini"
    key_var = "OPENAI_API_KEY" if use_openai else "GEMINI_API_KEY"
    api_key = os.getenv(key_var, "")
    if not api_key:
        raise BridgeError(503, "provider_not_configured")

    if session_id not in SESSIONS:
        if len(SESSIONS) >= MAX_SESSIONS:
            raise BridgeError(429, "session_limit")
        SESSIONS[session_id] = []
    history = SESSIONS[session_id]
    user_message = {"role": "user", "content": prompt.strip()}
    # Preserve conversation only; never inject a fixed model identity or behavior.
    messages = [*history, user_message][-MAX_HISTORY_MESSAGES:]

    try:
        if use_openai:
            response_text = call_openai_api(
                messages, api_key, model=os.getenv("OPENAI_MODEL", "gpt-4o-mini"))
        else:
            full_prompt = "\n".join(f"{m['role']}: {m['content']}" for m in messages)
            response_text = call_gemini_api(
                full_prompt, api_key, model=os.getenv("GEMINI_MODEL", "gemini-2.5-flash"))
    except Exception:
        # Fail closed: an unavailable model is never reported as a real AI reply.
        raise BridgeError(502, "provider_unavailable") from None
    if not isinstance(response_text, str) or not response_text.strip():
        raise BridgeError(502, "empty_provider_response")

    history.extend([user_message, {"role": "assistant", "content": response_text.strip()}])
    if len(history) > MAX_HISTORY_MESSAGES:
        SESSIONS[session_id] = history[-MAX_HISTORY_MESSAGES:]

    mcp_tool_result = {}
    try:
        mcp_res = mcp_server.response({
            "jsonrpc": "2.0",
            "id": str(uuid.uuid4()),
            "method": "tools/call",
            "params": {"name": "debian13_diagnostics", "arguments": {}},
        })
        if isinstance(mcp_res, dict) and "result" in mcp_res:
            mcp_tool_result = mcp_res["result"]
    except Exception:
        pass

    return {
        "sessionId": session_id,
        "provider": provider,
        "response": response_text.strip(),
        "historyLength": len(SESSIONS[session_id]),
        "mcpToolResult": mcp_tool_result,
    }


def terminate_session(session_id: str) -> bool:
    if session_id in SESSIONS:
        del SESSIONS[session_id]
        return True
    return False
