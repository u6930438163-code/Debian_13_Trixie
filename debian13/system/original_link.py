"""Link to the owner's REAL agents running in AI_Studio_BastillaOS.

BastillaOS (``src/kernel/debian13OriginalTaskService.mjs`` + ``server.ts``)
exposes protocol v1 for this repository:

* ``GET  /api/agent-tasks/capabilities`` -> advertised agents and capabilities
* ``GET  /api/agent-tasks/workers``      -> per-agent worker state
* ``POST /api/agent-tasks/messages``     -> a message for one agent (202 + id)
* ``POST /api/agent-tasks/submit``       -> a review task for one agent (202 + id)
* ``GET  /api/agent-tasks/jobs/<id>``    -> queued/running/completed/failed + reply

Every call carries ``Authorization: Bearer <DEBIAN13_ORIGINAL_AGENTS_TOKEN>``.
The agents answer there with their own identity, missions and provider chain
(Gemini -> xAI -> Ollama). Debian 13 adds nothing to what they receive: it only
forwards the operator's text and returns the agent's own saved reply. A reply
is reported only when BastillaOS marks the job completed with a provider and a
model; nothing is synthesised locally.
"""
from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request

from alex_bridge import NoRedirect, SOURCE_REPO, existing_api_base

TARGET_REPO = "u6930438163-code/Debian_13_Trixie"
PROTOCOL_VERSION = 1
TASK_CAPABILITY = "debian13_review_task_v1"
MESSAGE_CAPABILITY = "debian13_agent_message_v1"
# Debian 13 slug -> name used by BastillaOS (Aria is "si_1" internally there,
# but the Debian 13 protocol addresses her as "Aria").
AGENT_NAMES = {"alex": "Alex", "aria": "Aria", "astyria": "Astyria", "aurelius": "Aurelius"}
MAX_RESPONSE_BYTES = 65_536
MAX_MESSAGE_CHARS = 2000
SAFE_JOB_ID = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
TERMINAL = frozenset({"completed", "failed"})


class OriginalLinkError(Exception):
    def __init__(self, code: str, message: str = ""):
        super().__init__(message or code)
        self.code = code


def token() -> str:
    value = (os.getenv("BASTILLA_AGENTS_TOKEN") or os.getenv("ALEX_EXISTING_API_TOKEN") or "").strip()
    if not 32 <= len(value) <= 512 or any(ord(ch) < 33 or ord(ch) > 126 for ch in value):
        return ""
    return value


def configured() -> bool:
    try:
        return bool(existing_api_base()) and bool(token())
    except ValueError:
        return False


def _call(method: str, path: str, payload: dict | None = None,
          idempotency_key: str = "", timeout: float = 10) -> dict:
    try:
        base = existing_api_base()
    except ValueError as exc:
        raise OriginalLinkError("invalid_source_url", str(exc)) from exc
    secret = token()
    if not base or not secret:
        raise OriginalLinkError("original_agents_not_configured")
    headers = {"Accept": "application/json", "Authorization": "Bearer " + secret}
    data = None
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    if idempotency_key:
        headers["Idempotency-Key"] = idempotency_key
    request = urllib.request.Request(base + path, data=data, method=method, headers=headers)
    try:
        with urllib.request.build_opener(NoRedirect).open(request, timeout=timeout) as response:
            raw = response.read(MAX_RESPONSE_BYTES + 1)
    except urllib.error.HTTPError as exc:
        code = {401: "source_rejected_token", 404: "source_protocol_missing",
                409: "idempotency_conflict", 429: "source_queue_full",
                503: "source_agents_unavailable"}.get(exc.code, "source_http_error")
        raise OriginalLinkError(code, f"BastillaOS HTTP {exc.code}") from exc
    except (OSError, urllib.error.URLError, TimeoutError) as exc:
        raise OriginalLinkError("source_offline", "BastillaOS is not reachable.") from exc
    if len(raw) > MAX_RESPONSE_BYTES:
        raise OriginalLinkError("source_response_too_large")
    try:
        result = json.loads(raw)
    except (ValueError, UnicodeDecodeError) as exc:
        raise OriginalLinkError("source_invalid_json") from exc
    if not isinstance(result, dict):
        raise OriginalLinkError("source_invalid_json")
    return result


def capabilities() -> dict:
    """Which of the four real agents BastillaOS currently serves, and how."""
    if not configured():
        return {"configured": False, "chat": False, "tasks": False, "agents": []}
    result = _call("GET", "/agent-tasks/capabilities")
    caps = result.get("capabilities") if isinstance(result.get("capabilities"), list) else []
    agents = result.get("agents") if isinstance(result.get("agents"), list) else []
    valid = result.get("sourceRepository") == SOURCE_REPO and result.get("protocolVersion") == PROTOCOL_VERSION
    served = [slug for slug, name in AGENT_NAMES.items() if valid and name in agents]
    return {
        "configured": True,
        "chat": valid and MESSAGE_CAPABILITY in caps,
        "tasks": valid and TASK_CAPABILITY in caps,
        "agents": served,
        "mode": str(result.get("mode", ""))[:40],
    }


def workers() -> dict:
    result = _call("GET", "/agent-tasks/workers")
    states = {}
    for entry in result.get("agents", []) if isinstance(result.get("agents"), list) else []:
        if not isinstance(entry, dict):
            continue
        slug = next((s for s, n in AGENT_NAMES.items() if n == entry.get("name")), None)
        if slug:
            states[slug] = {
                "state": str(entry.get("state", "unknown"))[:32],
                "workerVerified": entry.get("workerVerified") is True,
                "completed": int(entry.get("completed") or 0),
                "queued": int(entry.get("queued") or 0),
            }
    return {"enabled": result.get("enabled") is True,
            "providerConfigured": result.get("providerConfigured") is True, "agents": states}


def send_message(agent: str, message: str, key: str = "") -> str:
    if agent not in AGENT_NAMES:
        raise OriginalLinkError("unknown_agent")
    text = message.strip()
    if not text or len(text) > MAX_MESSAGE_CHARS:
        raise OriginalLinkError("message_too_long", "The real agents accept messages up to 2000 characters.")
    result = _call("POST", "/agent-tasks/messages", {
        "agent": AGENT_NAMES[agent], "message": text, "mode": "review_only",
        "autoMerge": False, "purchaseAuthorized": False, "targetRepository": TARGET_REPO,
    }, idempotency_key=key)
    job_id = result.get("messageId")
    if result.get("accepted") is not True or result.get("agent") != AGENT_NAMES[agent] \
            or not isinstance(job_id, str) or not SAFE_JOB_ID.fullmatch(job_id):
        raise OriginalLinkError("source_did_not_accept")
    return job_id


def submit_task(agent: str, task: dict) -> str:
    if agent not in AGENT_NAMES:
        raise OriginalLinkError("unknown_agent")
    result = _call("POST", "/agent-tasks/submit", {
        "agent": AGENT_NAMES[agent], "capability": TASK_CAPABILITY,
        "task": {"id": task["id"], "title": task["title"], "description": task["description"],
                 "kind": task["kind"], "targetRepository": TARGET_REPO},
        "mode": "pull_request_proposal", "autoMerge": False, "purchaseAuthorized": False,
    }, idempotency_key=task["id"])
    job_id = result.get("taskId")
    if result.get("accepted") is not True or result.get("agent") != AGENT_NAMES[agent] \
            or not isinstance(job_id, str) or not SAFE_JOB_ID.fullmatch(job_id):
        raise OriginalLinkError("source_did_not_accept")
    return job_id


def get_job(job_id: str) -> dict:
    if not SAFE_JOB_ID.fullmatch(job_id or ""):
        raise OriginalLinkError("invalid_job_id")
    return _call("GET", "/agent-tasks/jobs/" + job_id)


def wait_for_reply(job_id: str, timeout_seconds: float, poll_seconds: float = 2.0,
                   sleep=time.sleep, clock=time.monotonic) -> tuple[str, str]:
    """Return (reply, 'provider/model') once BastillaOS saved the agent's answer."""
    deadline = clock() + timeout_seconds
    while True:
        job = get_job(job_id)
        status = job.get("status")
        if status == "completed":
            reply, provider, model = job.get("result"), job.get("provider"), job.get("model")
            if not isinstance(reply, str) or not reply.strip() or not provider or not model:
                raise OriginalLinkError("source_reply_unverified")
            return reply.strip(), f"{str(provider)[:40]}/{str(model)[:80]}"
        if status == "failed":
            raise OriginalLinkError(str(job.get("errorCode") or "agent_model_failed")[:64])
        if clock() >= deadline:
            raise OriginalLinkError("agent_reply_timeout",
                                    "The real agent has not answered yet; the job stays queued in BastillaOS.")
        sleep(poll_seconds)
