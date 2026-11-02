"""Capability-gated gateway to the OWNER'S ORIGINAL AI Studio agent service.

All operations target the existing u6930438163-code/AI_Studio_BastillaOS API.
Never instantiate substitute agents, execute shell commands or infer worker
activity from profile names. Read existing registry; fail closed on writes
until the source advertises and implements the explicit contract below.
"""
from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
import uuid

from alex_bridge import SOURCE_REPO, NoRedirect, existing_api_base
from original_agents import ORIGINAL_AGENTS

TARGET_REPO = "u6930438163-code/Debian_13_Trixie"
MAX_RESPONSE_BYTES = 65536
MAX_MESSAGE_CHARS = 2000
MAX_TITLE_CHARS = 120
ALLOWED_KINDS = frozenset(("development", "bugfix", "audit", "research"))
ALLOWED_STATUSES = frozenset(("queued", "running", "completed", "failed", "cancelled"))
AGENTS = frozenset(ORIGINAL_AGENTS)
PROTOCOL_VERSION = 1
SEND_CAPABILITY = "debian13_agent_message_v1"
TASK_CAPABILITY = "debian13_review_task_v1"
_SAFE_ID = re.compile(r"^[A-Za-z0-9_-]{1,128}$")


class GatewayError(Exception):
    """Sanitized error safe to surface as an MCP tool error."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def _agent(value: object) -> str:
    if not isinstance(value, str) or value not in AGENTS:
        raise GatewayError("unknown_original_agent")
    return value


def _nonempty(value: object, maximum: int, field: str) -> str:
    if not isinstance(value, str) or not 1 <= len(value.strip()) <= maximum:
        raise GatewayError("invalid_" + field)
    return value.strip()


def _source_token() -> str:
    token = os.getenv("ORIGINAL_AGENTS_API_TOKEN") or os.getenv("ALEX_EXISTING_API_TOKEN", "")
    if not 32 <= len(token) <= 512 or any(ord(ch) < 33 or ord(ch) > 126 for ch in token):
        raise GatewayError("original_agents_token_not_configured")
    return token


def _source_base() -> str:
    try:
        base = existing_api_base()
    except (ValueError, OSError):
        raise GatewayError("invalid_original_agents_api_url") from None
    if not base:
        raise GatewayError("original_agents_api_not_configured")
    return base


def _request(method: str, path: str, payload: dict | None = None, idempotency_key: str = "") -> dict:
    base, token = _source_base(), _source_token()
    if method not in ("GET", "POST") or path not in (
        "/agent-tasks", "/agent-tasks/workers", "/agent-tasks/capabilities",
        "/agent-tasks/messages", "/agent-tasks/submit"):
        if not (method == "GET" and path.startswith("/agent-tasks/jobs/")
                and _SAFE_ID.fullmatch(path.rsplit("/", 1)[-1])):
            raise GatewayError("unsupported_source_operation")
    if method == "GET" and path.startswith("/agent-tasks/jobs/"):
        if not _SAFE_ID.fullmatch(path.removeprefix("/agent-tasks/jobs/")):
            raise GatewayError("unsupported_source_operation")
    elif method == "GET" and path not in ("/agent-tasks", "/agent-tasks/workers", "/agent-tasks/capabilities"):
        raise GatewayError("unsupported_source_operation")
    elif method == "POST" and path not in ("/agent-tasks/messages", "/agent-tasks/submit")
        raise GatewayError("unsupported_source_operation")
    headers = {"Accept": "application/json", "Authorization": "Bearer " + token}
    data = None
    if method == "POST":
        data = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        if len(data) > 8192:
            raise GatewayError("request_too_large")
        headers["Content-Type"] = "application/json"
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key
    req = urllib.request.Request(base + path, data=data, headers=headers, method=method)
    opener = urllib.request.build_opener(NoRedirect)
    try:
        with opener.open(req, timeout=8) as response:
            if response.status not in ((200,) if method == "GET" else (200, 201, 202)):
                raise GatewayError("original_agents_unavailable")
            raw = response.read(MAX_RESPONSE_BYTES + 1)
    except urllib.error.HTTPError as exc:
        if exc.code in (404, 405, 501):
            raise GatewayError("source_capability_not_implemented") from None
        raise GatewayError("original_agents_request_rejected") from None
    except (OSError, urllib.error.URLError, TimeoutError):
        raise GatewayError("original_agents_unreachable") from None
    if len(raw) > MAX_RESPONSE_BYTES:
        raise GatewayError("source_response_too_large")
    try:
        obj = json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        raise GatewayError("invalid_source_response") from None
    if not isinstance(obj, dict):
        raise GatewayError("invalid_source_response")
    return obj


def _verified_capabilities(agent: str, needed: str) -> None:
    if os.getenv("ORIGINAL_AGENTS_ALLOW_WRITES") != "1":
        raise GatewayError("agent_writes_disabled")
    data = _request("GET", "/agent-tasks/capabilities")
    if (data.get("sourceRepository") != SOURCE_REPO
            or data.get("protocolVersion") != PROTOCOL_VERSION
            or not isinstance(data.get("capabilities"), list)
            or needed not in data["capabilities"]
            or not isinstance(data.get("agents"), list)
            or agent not in data["agents"]):
        raise GatewayError("source_capability_not_verified")


def _registry() -> list:
    data = _request("GET", "/agent-tasks")
    if data.get("success") is not True or not isinstance(data.get("tasks"), list):
        raise GatewayError("invalid_source_registry")
    return data["tasks"]


def _verified_worker_registry():
    """Use only an authenticated source status response, never profile names."""
    state = _request("GET", "/agent-tasks/workers")
    if (state.get("sourceRepository") != SOURCE_REPO
            or state.get("protocolVersion") != PROTOCOL_VERSION
            or not isinstance(state.get("agents"), list)):
        raise GatewayError("invalid_worker_status")
    by_name = {}
    for item in state["agents"]:
        if not isinstance(item, dict) or item.get("name") not in AGENTS:
            raise GatewayError("invalid_worker_status")
        if item["name"] in by_name:
            raise GatewayError("invalid_worker_status")
        if item.get("state") not in ("disabled", "provider_unavailable", "idle", "running"):
            raise GatewayError("invalid_worker_status")
        by_name[item["name"]] = item
    if set(by_name) != AGENTS:
        raise GatewayError("invalid_worker_status")
    return state, by_name


def list_agents() -> dict:
    """Read real, authenticated source worker status if it exists.

    On old source API deployments report registry-only facts without
    claiming workers are running. No simulated or duplicate agents.
    """
    try:
        state, by_name = _verified_worker_registry()
    except GatewayError as exc:
        if exc.code != "source_capability_not_implemented":
            raise
        tasks = _registry()
        agents = []
        for name, (slug, role) in ORIGINAL_AGENTS.items():
            matching = [task for task in tasks if isinstance(task, dict) and task.get("agent") == name]
            agents.append({
                "id": slug, "name": name, "role": role,
                "tasksInRegistry": len(matching),
                "runningEntriesInRegistry": sum(task.get("status") == "running" for task in matching),
                "workerVerified": False, "connected": False, "state": "unverified",
            })
        return {"sourceRepository": SOURCE_REPO, "registryReachable": True,
                "workerStatusEndpointReachable": False,
                "originalAgentExecutionVerified": False, "agents": agents}
    agents = []
    for name, (slug, role) in ORIGINAL_AGENTS.items():
        worker = by_name[name]
        running = worker.get("state") == "running" and worker.get("workerVerified") is True
        agents.append({
            "id": slug, "name": name, "role": role,
            "connected": state.get("enabled") is True and state.get("providerConfigured") is True,
            "state": "running" if running else worker["state"],
            "workerVerified": running,
            "heartbeatAt": worker.get("heartbeatAt") if running else None,
            "activeTaskId": worker.get("activeTaskId") if running else None,
            "queuedTasks": worker.get("queued", 0) if isinstance(worker.get("queued"), int) else 0,
            "completedTasks": worker.get("completed", 0) if isinstance(worker.get("completed"), int) else 0,
            "failedTasks": worker.get("failed", 0) if isinstance(worker.get("failed"), int) else 0,
        })
    return {"sourceRepository": SOURCE_REPO, "registryReachable": True,
            "workerStatusEndpointReachable": True, "providerConfigured": state.get("providerConfigured") is True,
            "originalAgentExecutionVerified": any(item["workerVerified"] for item in agents),
            "agents": agents}


def get_task_status(agent: str, task_id: str) -> dict:
    agent = _agent(agent)
    if not isinstance(task_id, str) or not _SAFE_ID.fullmatch(task_id):
        raise GatewayError("invalid_task_id")
    try:
        task = _request("GET", "/agent-tasks/jobs/" + task_id)
    except GatewayError as exc:
        if exc.code != "source_capability_not_implemented":
            raise
        # Old registry is not proof the corresponding worker executed the task.
        for task in _registry():
            if (isinstance(task, dict) and task.get("agent") == agent
                    and str(task.get("id", "")) == task_id):
                status = task.get("status")
                if status not in ALLOWED_STATUSES:
                    raise GatewayError("invalid_remote_task_status")
                return {"agent": agent, "taskId": task_id, "status": status,
                        "title": str(task.get("title", ""))[:MAX_TITLE_CHARS],
                        "registryEntryFound": True,
                        "workerExecutionVerified": False, "sourceRepository": SOURCE_REPO}
        raise GatewayError("task_not_in_original_registry")
    if task.get("id") != task_id or task.get("agent") != agent or task.get("status") not in ALLOWED_STATUSES:
        raise GatewayError("invalid_remote_task_status")
    executed = (task.get("status") == "completed" and
                task.get("workerExecutionVerified") is True and
                isinstance(task.get("provider"), str) and bool(task["provider"]) and
                isinstance(task.get("result"), str) and bool(task["result"]))
    return {
        "agent": agent, "taskId": task_id, "status": task["status"],
        "title": str(task.get("title", ""))[:MAX_TITLE_CHARS],
        "workerExecutionVerified": executed,
        "result": task.get("result", "")[:8000] if executed else "",
        "pullRequestVerified": False, "sourceRepository": SOURCE_REPO,
    }


def send_message(agent: str, message: str) -> dict:
    agent = _agent(agent)
    message = _nonempty(message, MAX_MESSAGE_CHARS, "message")
    _verified_capabilities(agent, SEND_CAPABILITY)
    receipt = _request("POST", "/agent-tasks/messages", {
        "agent": agent,
        "message": message,
        "targetRepository": TARGET_REPO,
        "mode": "review_only",
        "purchaseAuthorized": False,
        "autoMerge": False,
    }, idempotency_key=str(uuid.uuid4()))
    msg_id = receipt.get("messageId")
    if (receipt.get("accepted") is not True or receipt.get("agent") != agent
            or not isinstance(msg_id, str) or not _SAFE_ID.fullmatch(msg_id)):
        raise GatewayError("message_receipt_not_verified")
    return {
        "agent": agent, "messageId": msg_id, "acceptedByRemoteAPI": True,
        "agentReplyVerified": False, "sourceRepository": SOURCE_REPO,
    }


def create_task(agent: str, title: str, description: str, kind: str = "development") -> dict:
    agent = _agent(agent)
    title = _nonempty(title, MAX_TITLE_CHARS, "title")
    description = _nonempty(description, MAX_MESSAGE_CHARS, "description")
    if kind not in ALLOWED_KINDS:
        raise GatewayError("invalid_task_kind")
    _verified_capabilities(agent, TASK_CAPABILITY)
    receipt = _request("POST", "/agent-tasks/submit", {
        "agent": agent,
        "capability": TASK_CAPABILITY,
        "task": {
            "title": title, "description": description, "kind": kind,
            "targetRepository": TARGET_REPO,
        },
        "mode": "pull_request_proposal",
        "purchaseAuthorized": False,
        "autoMerge": False,
    }, idempotency_key=str(uuid.uuid4()))
    task_id = receipt.get("taskId")
    if (receipt.get("accepted") is not True or receipt.get("agent") != agent
            or receipt.get("capability") != TASK_CAPABILITY
            or not isinstance(task_id, str) or not _SAFE_ID.fullmatch(task_id)):
        raise GatewayError("task_receipt_not_verified")
    return {
        "agent": agent, "taskId": task_id, "acceptedByRemoteAPI": True,
        "workerExecutionVerified": False, "pullRequestVerified": False,
        "sourceRepository": SOURCE_REPO, "targetRepository": TARGET_REPO,
    }
