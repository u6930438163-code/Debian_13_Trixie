#!/usr/bin/env python3
"""Read-only verification of an operator-controlled Debian 13 HTTPS API / MCP.

No shell commands, agent task submission, data mutation or token logging.
The test proves this endpoint's response now, not ongoing availability.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

MAX_REPLY_BYTES = 65536


class CheckFailure(Exception):
    """A deliberately non-sensitive, stable failure code."""


class RejectRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def canonical_base(raw: str) -> str:
    parsed = urllib.parse.urlsplit(raw.rstrip("/"))
    local = parsed.hostname in ("127.0.0.1", "::1", "localhost")
    if (not parsed.hostname or parsed.username or parsed.password
            or parsed.path not in ("", "/") or parsed.query or parsed.fragment
            or (parsed.scheme != "https" and not (local and parsed.scheme == "http"))):
        raise CheckFailure("invalid_base_url")
    return raw.rstrip("/")


def read_json(base: str, path: str, token: str | None = None,
              message: dict | None = None) -> tuple[int, dict]:
    headers = {"Accept": "application/json, text/event-stream" if message is not None
               else "application/json"}
    if token is not None:
        headers["Authorization"] = "Bearer " + token
    body = None
    if message is not None:
        body = json.dumps(message, separators=(",", ":")).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(
        base + path, method="POST" if message is not None else "GET",
        data=body, headers=headers,
    )
    opener = urllib.request.build_opener(RejectRedirects)
    try:
        with opener.open(request, timeout=7) as response:
            status, raw = response.status, response.read(MAX_REPLY_BYTES + 1)
    except urllib.error.HTTPError as exc:
        status, raw = exc.code, exc.read(MAX_REPLY_BYTES + 1)
    except (OSError, TimeoutError) as exc:
        raise CheckFailure("endpoint_unreachable") from None
    if len(raw) > MAX_REPLY_BYTES:
        raise CheckFailure("oversized_response")
    try:
        result = json.loads(raw) if raw else {}
    except (ValueError, UnicodeDecodeError):
        raise CheckFailure("invalid_json_response") from None
    if not isinstance(result, dict):
        raise CheckFailure("invalid_json_response")
    return status, result


def verify(base_url: str, token: str) -> list[str]:
    base = canonical_base(base_url)
    if not 32 <= len(token) <= 512 or any(ord(x) < 33 or ord(x) > 126 for x in token):
        raise CheckFailure("invalid_operator_token")
    steps = []
    status, body = read_json(base, "/healthz")
    if status != 200 or body.get("status") != "ok":
        raise CheckFailure("health_not_ready")
    steps.append("health")

    status, _ = read_json(base, "/v1/diagnostics")
    if status != 401:
        raise CheckFailure("unauthorized_diagnostics_not_rejected")
    steps.append("unauthorized_rejected")

    status, body = read_json(base, "/v1/diagnostics", token)
    if status != 200 or body.get("service") != "debian13-system":
        raise CheckFailure("authenticated_diagnostics_failed")
    steps.append("diagnostics")

    status, body = read_json(base, "/mcp", token, {
        "jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {"protocolVersion": "2025-11-25", "capabilities": {},
                   "clientInfo": {"name": "debian13-connection-probe", "version": "1.0"}},
    })
    if (status != 200 or body.get("jsonrpc") != "2.0"
            or not isinstance(body.get("result"), dict)
            or body["result"].get("serverInfo", {}).get("name") != "debian13-readonly"):
        raise CheckFailure("mcp_initialize_failed")
    steps.append("mcp_initialize")

    status, body = read_json(base, "/mcp", token, {
        "jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {},
    })
    if (status != 200 or body.get("jsonrpc") != "2.0"
            or not isinstance(body.get("result"), dict)
            or not isinstance(body["result"].get("tools"), list)
            or "debian13_diagnostics" not in {
                x.get("name") for x in body["result"]["tools"] if isinstance(x, dict)
            }):
        raise CheckFailure("mcp_tools_list_failed")
    steps.append("mcp_tools_list")

    status, body = read_json(base, "/mcp", token, {
        "jsonrpc": "2.0", "id": 3, "method": "tools/call",
        "params": {"name": "debian13_diagnostics", "arguments": {}},
    })
    if (status != 200 or not isinstance(body.get("result"), dict)
            or body["result"].get("isError") is not False):
        raise CheckFailure("mcp_read_only_tool_failed")
    steps.append("mcp_diagnostics_tool")
    return steps


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default=os.environ.get("DEBIAN13_REMOTE_BASE_URL", ""),
                        help="verified HTTPS origin; local http://127.0.0.1 allowed for tests")
    args = parser.parse_args()
    try:
        completed = verify(args.base_url, os.environ.get("DEBIAN13_REMOTE_API_TOKEN", ""))
    except CheckFailure as exc:
        print("FAIL: " + str(exc), file=sys.stderr)
        return 1
    print("PASS: " + ", ".join(completed))
    print("LIMIT: no external ChatGPT MCP client or original AI agent was verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
