#!/usr/bin/env python3
"""Debian 13 MCP with authenticated review-queue and model-chat tools (JSON-RPC).

Configure DEBIAN13_SYSTEM_API_URL and DEBIAN13_API_TOKEN in the MCP client
process. Never put secrets in MCP arguments, stdout, or repository files.
"""
from __future__ import annotations

import json
import os
import re
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
TASK_DETAIL_TOOL = "debian13_work_task_detail"
CANCEL_TOOL = "debian13_cancel_work_task"
CHAT_TOOL = "debian13_chat_agent"
TASK_ID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")
TASK_ID_SCHEMA = {
    "type": "object", "properties": {
        "task_id": {"type": "string", "pattern": TASK_ID.pattern},
    },
    "required": ["task_id"], "additionalProperties": False,
}
CHAT_SCHEMA = {
    "type": "object", "properties": {
        "agent": {"type": "string", "enum": ["alex", "aria", "astyria", "aurelius"]},
        "message": {"type": "string", "minLength": 1, "maxLength": 4000},
    },
    "required": ["agent", "message"], "additionalProperties": False,
}
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




def validate_task_id(arguments: object) -> dict:
    if not isinstance(arguments, dict) or set(arguments) != {"task_id"}:
        raise ValueError("Invalid task id arguments")
    value = arguments["task_id"]
    if not isinstance(value, str) or not TASK_ID.fullmatch(value):
        raise ValueError("Invalid task identifier")
    return {"task_id": value}


def validate_agent_chat(arguments: object) -> dict:
    if not isinstance(arguments, dict) or set(arguments) != {"agent", "message"}:
        raise ValueError("Invalid agent chat arguments")
    agent, message = arguments["agent"], arguments["message"]
    if (not isinstance(agent, str) or agent not in WORK_AGENTS
            or not isinstance(message, str) or not 1 <= len(message.strip()) <= 4000):
        raise ValueError("Invalid agent chat arguments")
    return {"agent": agent, "message": message.strip()}


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



def post_api(path: str, payload: dict | None = None) -> dict:
    """Authenticated API call; no redirects, no arbitrary paths or URLs."""
    token = os.environ.get("DEBIAN13_API_TOKEN", "")
    if len(token) < 32 or any(ord(c) < 33 or ord(c) > 126 for c in token):
        raise ValueError("Invalid MCP operator credential")
    if path not in ("/v1/chat",) and not re.fullmatch(
            r"/api/v1/tasks/" + TASK_ID.pattern[1:-1] + r"/cancel", path):
        raise ValueError("Unsupported API action")
    request = urllib.request.Request(
        api_base() + path,
        data=json.dumps(payload).encode("utf-8") if payload is not None else b"",
        headers={"Authorization": "Bearer " + token,
                 "Accept": "application/json", "Content-Type": "application/json"},
        method="POST",
    )
    opener = urllib.request.build_opener(urllib.request.HTTPHandler, urllib.request.HTTPSHandler, NoRedirect)
    with opener.open(request, timeout=115 if path == "/v1/chat" else 10) as reply:
        raw = reply.read(MAX_RESPONSE + 1)
    if len(raw) > MAX_RESPONSE:
        raise ValueError("API response too large")
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError("Invalid API response")
    return value


def chat_agent(payload: dict) -> dict:
    """Call the configured real model; do not claim it is an original autonomous worker."""
    value = post_api("/v1/chat", {"profile": payload["agent"], "message": payload["message"]})
    if (value.get("profile") != payload["agent"] or
            not isinstance(value.get("content"), str) or not value["content"].strip() or
            not isinstance(value.get("model"), str)):
        raise ValueError("Invalid model response")
    return {"agent": value["profile"], "reply": value["content"],
            "model": value["model"], "source": "configured_model_provider",
            "originalAgentExecutionVerified": False,
            "sessionPersisted": False}


def cancel_work_task(payload: dict) -> dict:
    value = post_api("/api/v1/tasks/" + payload["task_id"] + "/cancel")
    if not isinstance(value.get("task"), dict) or value["task"].get("id") != payload["task_id"]:
        raise ValueError("Invalid cancellation receipt")
    return {"task": value["task"], "executionStarted": False}


def task_detail(payload: dict) -> dict:
    value = read_api("/api/v1/tasks/" + payload["task_id"])
    if not isinstance(value.get("task"), dict) or value["task"].get("id") != payload["task_id"]:
        raise ValueError("Invalid task receipt")
    return value


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


def response(request: dict, task_writer=None, chat_writer=None, cancel_writer=None,
             detail_reader=None) -> dict | None:
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
            "serverInfo": {"name": "debian13-work-mcp", "version": "0.3.0"},
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
        }, {
            "name": TASK_DETAIL_TOOL,
            "description": "Read a task by UUID from the persistent Debian 13 Work queue.",
            "inputSchema": TASK_ID_SCHEMA,
            "annotations": {"readOnlyHint": True, "destructiveHint": False},
        }, {
            "name": CANCEL_TOOL,
            "description": "Cancel an unsubmitted Work task by UUID; cannot recall tasks sent to Alex.",
            "inputSchema": TASK_ID_SCHEMA,
            "annotations": {"readOnlyHint": False, "destructiveHint": True, "idempotentHint": True},
        }, {
            "name": CHAT_TOOL,
            "description": (
                "Ask the configured model on behalf of a selected agent profile; "
                "returns a real model response only if a provider is configured. "
                "Does not verify the owner's original worker or persist a conversation. "
                "Model API may have usage costs; does not execute system commands."
            ),
            "inputSchema": CHAT_SCHEMA,
            "annotations": {"readOnlyHint": False, "destructiveHint": False, "idempotentHint": False,
                            "openWorldHint": True},
        }]}
    elif method == "tools/call":
        params = request.get("params")
        if not isinstance(params, dict) or params.get("name") not in (
                *TOOLS, WORK_TOOL, TASK_DETAIL_TOOL, CANCEL_TOOL, CHAT_TOOL):
            return {"jsonrpc": "2.0", "id": request_id, "error": {"code": -32602, "message": "Unknown tool"}}
        name, arguments = params["name"], params.get("arguments", {})
        try:
            if name == WORK_TOOL:
                validated = validate_work_task(arguments)
            elif name in (TASK_DETAIL_TOOL, CANCEL_TOOL):
                validated = validate_task_id(arguments)
            elif name == CHAT_TOOL:
                validated = validate_agent_chat(arguments)
            elif arguments != {}:
                raise ValueError("Unexpected arguments for read tool")
        except ValueError:
            return {"jsonrpc": "2.0", "id": request_id, "error": {"code": -32602, "message": "Invalid tool arguments"}}
        try:
            if name == WORK_TOOL:
                value = task_writer(validated) if task_writer is not None else queue_work_task(validated)
                if not isinstance(value, dict) or value.get("executionStarted") is not False:
                    raise ValueError("Invalid task receipt")
            elif name == TASK_DETAIL_TOOL:
                value = detail_reader(validated) if detail_reader is not None else task_detail(validated)
                if not isinstance(value, dict) or value.get("task", {}).get("id") != validated["task_id"]:
                    raise ValueError("Invalid task detail")
            elif name == CANCEL_TOOL:
                value = cancel_writer(validated) if cancel_writer is not None else cancel_work_task(validated)
                if not isinstance(value, dict) or value.get("task", {}).get("id") != validated["task_id"]:
                    raise ValueError("Invalid cancellation receipt")
            elif name == CHAT_TOOL:
                value = chat_writer(validated) if chat_writer is not None else chat_agent(validated)
                if (not isinstance(value, dict) or value.get("agent") != validated["agent"]
                        or not isinstance(value.get("reply"), str) or not value["reply"].strip()
                        or value.get("originalAgentExecutionVerified") is not False):
                    raise ValueError("Invalid agent model response")
            else:
                path = TOOLS[name][1]
                value = read_existing_alex(name) if path.startswith("alex:") else read_api(path)
            result = {"content": [{"type": "text", "text": json.dumps(value, ensure_ascii=False)}], "isError": False}
        except (ValueError, OSError, urllib.error.URLError, json.JSONDecodeError):
            # Do not echo URLs, bearer credentials, responses, or internals.
            result = {"content": [{"type": "text", "text": "The configured Debian 13 API or model is unavailable or invalid."}], "isError": True}
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
