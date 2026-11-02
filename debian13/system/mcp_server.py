#!/usr/bin/env python3
"""Read-only Debian 13 MCP server over stdio (JSON-RPC, newline delimited).

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
import original_agent_gateway as agent_gateway

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
# Keep stable legacy tools and append explicit gateway capabilities with typed arguments.
GATEWAY_TOOLS = {
    "list_agents": {
        "description": "Read the original four agent registry; no claims about live workers.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
        "readOnly": True,
    },
    "send_message": {
        "description": "Send a message ONLY to the owner's original AI Studio agent when source explicitly advertises a compatible endpoint; disabled by default.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "agent": {"type": "string", "enum": ["Alex", "Aria", "Astyria", "Aurelius"]},
                "message": {"type": "string", "minLength": 1, "maxLength": 2000},
            },
            "required": ["agent", "message"],
            "additionalProperties": False,
        },
        "readOnly": False,
    },
    "create_task": {
        "description": "Submit a PR-proposal task to original AI Studio agent only with configured opt-in and verified source capability. No automatic merge or admin operations.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "agent": {"type": "string", "enum": ["Alex", "Aria", "Astyria", "Aurelius"]},
                "title": {"type": "string", "minLength": 1, "maxLength": 120},
                "description": {"type": "string", "minLength": 1, "maxLength": 2000},
                "kind": {"type": "string", "enum": ["development", "bugfix", "audit", "research"]},
            },
            "required": ["agent", "title", "description"],
            "additionalProperties": False,
        },
        "readOnly": False,
    },
    "get_task_status": {
        "description": "Read the status of an exact agent task ID in the owner's original registry; status alone does not prove the worker executed.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "agent": {"type": "string", "enum": ["Alex", "Aria", "Astyria", "Aurelius"]},
                "task_id": {"type": "string", "minLength": 1, "maxLength": 128},
            },
            "required": ["agent", "task_id"],
            "additionalProperties": False,
        },
        "readOnly": True,
    },
}

MAX_RESPONSE = 262144


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


def response(request: dict) -> dict | None:
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
            "serverInfo": {"name": "debian13-readonly", "version": "0.1.0"},
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
        ] + [
            {
                "name": name,
                "description": details["description"],
                "inputSchema": details["inputSchema"],
                "annotations": {
                    "readOnlyHint": details["readOnly"],
                    "destructiveHint": False,
                    "idempotentHint": details["readOnly"],
                    "openWorldHint": not details["readOnly"],
                },
            }
            for name, details in GATEWAY_TOOLS.items()
        ]}
    elif method == "tools/call":
        params = request.get("params")
        if not isinstance(params, dict):
            return {"jsonrpc": "2.0", "id": request_id, "error": {"code": -32602, "message": "Invalid tool arguments"}}
        name = params.get("name")
        if name not in TOOLS and name not in GATEWAY_TOOLS:
            return {"jsonrpc": "2.0", "id": request_id, "error": {"code": -32602, "message": "Unknown tool"}}
        arguments = params.get("arguments", {})
        if not isinstance(arguments, dict):
            return {"jsonrpc": "2.0", "id": request_id, "error": {"code": -32602, "message": "Invalid arguments"}}
        schema = (GATEWAY_TOOLS[name]["inputSchema"] if name in GATEWAY_TOOLS
                  else {"required": [], "properties": {}})
        if (set(arguments) - set(schema.get("properties", {}))
                or any(field not in arguments for field in schema.get("required", []))):
            return {"jsonrpc": "2.0", "id": request_id, "error": {"code": -32602, "message": "Invalid tool arguments"}}
        try:
            if name in GATEWAY_TOOLS:
                if name == "list_agents":
                    value = agent_gateway.list_agents()
                elif name == "send_message":
                    value = agent_gateway.send_message(arguments["agent"], arguments["message"])
                elif name == "create_task":
                    value = agent_gateway.create_task(
                        arguments["agent"], arguments["title"], arguments["description"],
                        arguments.get("kind", "development"))
                else:
                    value = agent_gateway.get_task_status(arguments["agent"], arguments["task_id"])
            else:
                path = TOOLS[name][1]
                value = (read_existing_alex(name)
                         if path.startswith("alex:") else read_api(path))
            result = {"content": [{"type": "text", "text": json.dumps(value, ensure_ascii=False)}], "isError": False}
        except agent_gateway.GatewayError as exc:
            # Sanitized machine code; never surface remote response bodies or credentials.
            result = {"content": [{"type": "text", "text": exc.code}], "isError": True}
        except (ValueError, OSError, urllib.error.URLError, json.JSONDecodeError, KeyError, TypeError):
            result = {"content": [{"type": "text", "text": "The configured Debian 13 or original agent API is unavailable or invalid."}], "isError": True}
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
