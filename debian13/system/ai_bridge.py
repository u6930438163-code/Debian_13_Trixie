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
SESSIONS: dict[str, list[dict]] = {}


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
        raise RuntimeError(f"OpenAI API error: {str(exc)}") from exc


def call_gemini_api(prompt: str, api_key: str, model: str = "gemini-1.5-flash") -> str:
    if not api_key:
        raise ValueError("GEMINI_API_KEY is not configured")
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
    payload = {
        "contents": [{"parts": [{"text": prompt}]}]
    }
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
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
        raise RuntimeError(f"Gemini API error: {str(exc)}") from exc


def execute_bridge_session(session_id: str, prompt: str, use_openai: bool = True) -> dict:
    if not session_id:
        session_id = str(uuid.uuid4())
    if session_id not in SESSIONS:
        SESSIONS[session_id] = [
            {"role": "system", "content": "You are a cooperative AI assistant in Debian 13 Trixie operating alongside MCP tools."}
        ]

    history = SESSIONS[session_id]
    if len(prompt.strip()) > MAX_MESSAGE_CHARS:
        raise ValueError("Message exceeds maximum character length.")

    history.append({"role": "user", "content": prompt.strip()})

    openai_key = os.getenv("OPENAI_API_KEY", "")
    gemini_key = os.getenv("GEMINI_API_KEY", "")

    response_text = ""
    provider = "openai" if use_openai else "gemini"

    try:
        if use_openai and openai_key:
            response_text = call_openai_api(history, openai_key)
        elif gemini_key:
            full_prompt = "\n".join([f"{m['role']}: {m['content']}" for m in history])
            response_text = call_gemini_api(full_prompt, gemini_key)
            provider = "gemini"
        else:
            response_text = "Simulated bridge response (no external API keys configured in environment)."
            provider = "simulation"
    except Exception as err:
        response_text = f"Bridge invocation error: {type(err).__name__} (secrets omitted)."
        provider = "error"

    history.append({"role": "assistant", "content": response_text})

    # Execute read-only MCP diagnostics tool to enrich response context
    mcp_tool_result = {}
    try:
        mcp_res = mcp_server.response({
            "jsonrpc": "2.0",
            "id": str(uuid.uuid4()),
            "method": "tools/call",
            "params": {"name": "debian13_diagnostics", "arguments": {}}
        })
        if mcp_res and "result" in mcp_res:
            mcp_tool_result = mcp_res["result"]
    except Exception:
        pass

    return {
        "sessionId": session_id,
        "provider": provider,
        "response": response_text,
        "historyLength": len(history),
        "mcpToolResult": mcp_tool_result,
    }


def terminate_session(session_id: str) -> bool:
    if session_id in SESSIONS:
        del SESSIONS[session_id]
        return True
    return False
