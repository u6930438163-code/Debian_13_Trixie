#!/usr/bin/env python3
"""Debian 13 MCP server with read-only tools and authenticated Work queue write (JSON-RPC, newline delimited).

Configure DEBIAN13_SYSTEM_API_URL and DEBIAN13_API_TOKEN in the MCP client
process. Never put secrets in MCP arguments, stdout, or repository files.
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

from alex_bridge import read_existing_alex

PROTOCOL_VERSION = "2025-11-25"
TOOLS = {
    "debian13_profiles": ("List configured AI profiles and their real active flags.", "/v1/profiles"),
    "debian13_diagnostics": ("Read authenticated runtime diagnostics; no secrets.", "/v1/diagnostics"),
    "debian13_integrations": ("Read the actual integration configuration status.", "/v1/integrations"),
    "debian13_work_tasks": ("Read persistent review-only Work task queue; no task execution.", "/api/v1/tasks"),
    "debian13_original_agents": ("Check connection status of four original owner agents; profiles do not prove active AI.", "/api/v1/original-agents"),
    "alex_existing_status": (
        "Read live connectivity of the ORIGINAL owner's Alex task registry; no agent execution.", "alex:status"),
    "alex_existing_tasks": (
        "Read the ORIGINAL owner's Alex task statuses only; never create or modify tasks.", "alex:tasks"),
}
MAX_RESPONSE = 262144
WORK_TOOL = "debian13_queue_work_task"
WORK_AGENTS = ("alex", "aria", "astyria", "aurelius")
WORK_KINDS = ("development", "bugfix", "audit", "research")
WORK_SCHEMA = {
    "type": "object",
    "properties": {
        "agent": {"type": "string", "enum": list(WORK_AGENTS)},
        "kind": {"type": "string", "enum": list(WORK_KINDS)},
        "title": {"type": "string", "minLength": 1, "maxLength": 120},
        "description": {"type": "string", "minLength": 1, "maxLength": 2000},
    },
    "required": ["agent", "kind", "title", "description"],
    "additionalProperties": False,
}


def validate_work_task(arguments: object) -> dict:
    if not isinstance(arguments, dict) or set(arguments) != set(WORK_SCHEMA["required"]):
        raise ValueError("Invalid task fields")
    agent, kind = arguments["agent"], arguments["kind"]
    title, description = arguments["title"], arguments["description"]
    if (not isinstance(agent, str) or agent not in WORK_AGENTS
            or not isinstance(kind, str) or kind not in WORK_KINDS
            or not isinstance(title, str) or not 1 <= len(title.strip()) <= 120
            or not isinstance(description, str) or not 1 <= len(description.strip()) <= 2000):
        raise ValueError("Invalid task fields")
    return {"agent": agent, "kind": kind, "title": title.strip(),
            "description": description.strip()}



def api_base() -> str:
    value = os.environ.get("DEBIAN13_SYSTEM_API_URL", "http://127.0.0.1:8765").rstrip("/")
    parsed = urllib.parse.urlsplit(value)
    if (parsed.scheme not in ("http", "https")
            or not parsed.hostname or parsed.username or parsed.password
            or parsed.query or parsed.fragment or parsed.path not in ("", "/")):
        raise ValueError("Invalid DEBIAN13_SYSTEM_API_URL")
    if parsed.scheme == "http" and parsed.hostname not in ("localhost", "127.0.0.1", "::1", "debian13-system"):
        raise ValueError("Insecure remote HTTP URL forbidden")
    return value


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def read_api(path: str) -> dict:
    token = os.environ.get("DEBIAN13_API_TOKEN", "")
    if len(token) < 32 or any(ord(c) < 33 or ord(c) > 126 for c in token):
        raise ValueError("Set a valid DEBIAN13_API_TOKEN (32+ characters) in the MCP environment")
    request = urllib.request.Request(
        api_base() + path,
        headers={"Authorization": "Bearer " + token, "Accept": "application/json"},
        method="GET",
    )
    opener = urllib.request.build_opener(urllib.request.HTTPHandler, urllib.request.HTTPSHandler, NoRedirect)
    with opener.open(request, timeout=10) as response:
        raw = response.read(MAX_RESPONSE + 1)
    if len(raw) > MAX_RESPONSE:
        raise ValueError("API response too large")
    result = json.loads(raw)
    if not isinstance(result, dict):
        raise ValueError("Invalid API response")
    return result


def queue_work_task(payload: dict) -> dict:
    """Queue via the authenticated local API; never execute a task."""
    token = os.environ.get("DEBIAN13_API_TOKEN", "")
    if len(token) < 32 or any(ord(c) < 33 or ord(c) > 126 for c in token):
        raise ValueError("Invalid MCP operator credential")
    req = urllib.request.Request(
        api_base() + "/api/v1/tasks",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Authorization": "Bearer " + token,
                 "Accept": "application/json", "Content-Type": "application/json"},
        method="POST",
    )
    opener = urllib.request.build_opener(urllib.request.HTTPHandler, urllib.request.HTTPSHandler, NoRedirect)
    with opener.open(req, timeout=10) as reply:
        data = reply.read(MAX_RESPONSE + 1)
    if len(data) > MAX_RESPONSE:
        raise ValueError("Task receipt too large")
    result = json.loads(data)
    if not isinstance(result, dict) or result.get("executionStarted") is not False:
        raise ValueError("Invalid task receipt")
    return result


def response(request: dict, task_writer=None) -> dict | None:
    request_id = request.get("id")
    method = request.get("method")
    if not isinstance(method, str):
        return {"jsonrpc": "2.0", "id": request_id, "error": {"code": -32600, "message": "Invalid Request"}}
    if method.startswith("notifications/"):
        return None
    if request_id is None:
        return None
    if method == "initialize":
        result = {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": "debian13-work-mcp", "version": "0.2.0"},
        }
    elif method == "ping":
        result = {}
    elif method == "tools/list":
        result = {"tools": [
            {
                "name": name,
                "description": description,
                "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
                "annotations": {"readOnlyHint": True, "destructiveHint": False},
            }
            for name, (description, _) in TOOLS.items()
        ] + [{
            "name": WORK_TOOL,
            "description": ("Queue a review-only Work task. No agent execution, "
                            "shell commands, GitHub writes or purchases."),
            "inputSchema": WORK_SCHEMA,
            "annotations": {"readOnlyHint": False, "destructiveHint": False,
                            "idempotentHint": False},
        }]}
    elif method == "tools/call":
        params = request.get("params")
        if not isinstance(params, dict) or params.get("name") not in (*TOOLS, WORK_TOOL):
            return {"jsonrpc": "2.0", "id": request_id, "error": {"code": -32602, "message": "Unknown tool"}}
        name, arguments = params["name"], params.get("arguments", {})
        try:
            if name == WORK_TOOL:
                validated = validate_work_task(arguments)
            elif arguments != {}:
                raise ValueError("Unexpected arguments for read tool")
        except ValueError:
            return {"jsonrpc": "2.0", "id": request_id, "error": {"code": -32602, "message": "Invalid tool arguments"}}
        try:
            if name == WORK_TOOL:
                value = task_writer(validated) if task_writer is not None else queue_work_task(validated)
                if not isinstance(value, dict) or value.get("executionStarted") is not False:
                    raise ValueError("Invalid task receipt")
            else:
                path = TOOLS[name][1]
                value = read_existing_alex(name) if path.startswith("alex:") else read_api(path)
            result = {"content": [{"type": "text", "text": json.dumps(value, ensure_ascii=False)}], "isError": False}
        except (ValueError, OSError, urllib.error.URLError, json.JSONDecodeError) as exc:
            # Do not echo URLs, bearer credentials, response bodies, or internals.
            result = {"content": [{"type": "text", "text": "The configured Debian 13 or existing Alex API is unavailable or invalid."}], "isError": True}
    else:
        return {"jsonrpc": "2.0", "id": request_id, "error": {"code": -32601, "message": "Method not found"}}
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def main() -> None:
    for raw in sys.stdin.buffer:
        try:
            if len(raw) > 65536:
                raise ValueError("Request too large")
            data = json.loads(raw)
            if not isinstance(data, dict) or data.get("jsonrpc") != "2.0":
                answer = {"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "Invalid Request"}}
            else:
                answer = response(data)
        except (ValueError, UnicodeDecodeError, json.JSONDecodeError):
            answer = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}}
        if answer is not None:
            sys.stdout.write(json.dumps(answer, ensure_ascii=False) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
