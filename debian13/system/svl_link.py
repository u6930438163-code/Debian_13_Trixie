"""Chat link to the owner's agents running in SVL_System (mmitek515-netizen/SVL_System).

SVL_System answers through ``POST /api/triad/message`` with the agents' own
missions, its model chain (Gemini -> xAI -> Ollama) and its own per-agent chat
history kept by the SVL Python kernel. ``GET /api/ai/status`` is read-only.

Safety, verified against SVL_System server.ts:

* ``/api/triad/message`` has no authentication and runs a message starting
  with ``/exec ``, ``!``, ``/propose|/approve|/reject|/proposals`` or a kernel
  word (``report status state root sudo clear_chat clear sync bump set infra``)
  directly on the SVL host (bash, ROOT UNLIMITED) instead of sending it to an
  agent. Every forwarded message is therefore prefixed with ``@model ``: all of
  those checks are anchored at the start of the raw message, so they never
  match, and SVL strips the prefix before the model sees the text. ``@model``
  also skips SVL's Best-Fit router (template answers without a model).
* A reply is accepted only when SVL reports a real model engine
  (``provider:model``); ``without_nlp``, ``best-fit:*`` and errors are rejected,
  so no scripted answer is ever shown as an agent's reply.
* Only the operator's own chat uses this link. Unattended inputs (the Work
  worker, MCP, GPT Actions) are never forwarded to SVL_System.
"""
from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request

SOURCE_REPO = "mmitek515-netizen/SVL_System"
# Debian 13 slug -> agent identifier used by SVL_System (Aria is "si_1" there).
SVL_AGENTS = {"alex": "Alex", "aria": "si_1", "astyria": "Astyria", "aurelius": "Aurelius"}
FORCE_MODEL_PREFIX = "@model "
MAX_MESSAGE_CHARS = 4000
MAX_RESPONSE_BYTES = 16 * 1024 * 1024  # SVL returns its whole kernel state with each reply
MODEL_ENGINE = re.compile(r"^(?!best-fit:)(?!without_nlp)[a-z][a-z0-9_-]*:[^\s].*$", re.I)


class SvlLinkError(Exception):
    def __init__(self, code: str, message: str = ""):
        super().__init__(message or code)
        self.code = code


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def base_url() -> str | None:
    """SVL_SYSTEM_URL: origin only; HTTPS, or HTTP on loopback."""
    raw = os.getenv("SVL_SYSTEM_URL", "").strip().rstrip("/")
    if not raw:
        return None
    parsed = urllib.parse.urlsplit(raw)
    if (parsed.scheme not in ("https", "http") or not parsed.hostname or parsed.username
            or parsed.password or parsed.query or parsed.fragment or parsed.path not in ("",)):
        raise ValueError("SVL_SYSTEM_URL must be an origin such as https://svl.example (no path)")
    if parsed.scheme == "http" and parsed.hostname not in ("localhost", "127.0.0.1", "::1"):
        raise ValueError("SVL_SYSTEM_URL must use HTTPS outside localhost")
    return raw


def configured() -> bool:
    try:
        return base_url() is not None
    except ValueError:
        return False


def _call(method: str, path: str, payload: dict | None, timeout: float) -> dict:
    try:
        base = base_url()
    except ValueError as exc:
        raise SvlLinkError("invalid_svl_url", str(exc)) from exc
    if not base:
        raise SvlLinkError("svl_not_configured")
    headers = {"Accept": "application/json"}
    token = os.getenv("SVL_SYSTEM_TOKEN", "").strip()  # optional, e.g. an auth proxy in front of SVL
    if token:
        headers["Authorization"] = "Bearer " + token
    data = None
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(base + path, data=data, method=method, headers=headers)
    try:
        with urllib.request.build_opener(_NoRedirect).open(request, timeout=timeout) as response:
            raw = response.read(MAX_RESPONSE_BYTES + 1)
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = str(json.loads(exc.read(4096)).get("error", ""))[:300]
        except (ValueError, AttributeError, OSError):
            pass
        code = {400: "svl_rejected_message", 404: "svl_protocol_missing",
                502: "svl_model_failed", 503: "svl_no_model"}.get(exc.code, "svl_http_error")
        raise SvlLinkError(code, detail or f"SVL_System HTTP {exc.code}") from exc
    except (OSError, urllib.error.URLError, TimeoutError) as exc:
        raise SvlLinkError("svl_offline", "SVL_System is not reachable.") from exc
    if len(raw) > MAX_RESPONSE_BYTES:
        raise SvlLinkError("svl_response_too_large")
    try:
        result = json.loads(raw)
    except (ValueError, UnicodeDecodeError) as exc:
        raise SvlLinkError("svl_invalid_json") from exc
    if not isinstance(result, dict):
        raise SvlLinkError("svl_invalid_json")
    return result


def status() -> dict:
    """Read-only probe of SVL_System's model chain (GET /api/ai/status)."""
    if not configured():
        return {"configured": False, "reachable": False, "modelConfigured": False}
    result = _call("GET", "/api/ai/status", None, timeout=10)
    chain = [str(x)[:120] for x in result.get("chain", []) if isinstance(x, str)][:6]
    model_chain = [c for c in chain if not c.lower().startswith("without_nlp")]
    return {
        "configured": True,
        "reachable": True,
        "chatProtocol": result.get("chatProtocol") == "/api/triad/message",
        "modelConfigured": bool(model_chain),
        "provider": str(result.get("provider", ""))[:120],
        "chain": chain,
    }


def chat(agent: str, message: str, timeout: float = 110) -> tuple[str, str]:
    """Send the operator's message to one SVL agent; return (reply, engine)."""
    name = SVL_AGENTS.get(agent)
    if not name:
        raise SvlLinkError("unknown_agent")
    text = message.strip()
    if not text or len(text) > MAX_MESSAGE_CHARS:
        raise SvlLinkError("message_too_long", "Wiadomość dla SVL_System może mieć do 4000 znaków.")
    result = _call("POST", "/api/triad/message",
                   {"message": FORCE_MODEL_PREFIX + text, "agent": name}, timeout=timeout)
    if result.get("success") is not True or result.get("executed"):
        raise SvlLinkError("svl_unexpected_reply")
    reply = (result.get("replies") or {}).get(name)
    engine = str((result.get("engines") or {}).get(name, ""))
    if not isinstance(reply, str) or not reply.strip():
        raise SvlLinkError("svl_empty_reply")
    if not MODEL_ENGINE.fullmatch(engine):
        # Template/deterministic answers are not shown as the agent's reply.
        raise SvlLinkError("svl_reply_without_model", f"SVL_System answered without a model ({engine or 'unknown'}).")
    return reply.strip(), engine[:160]
