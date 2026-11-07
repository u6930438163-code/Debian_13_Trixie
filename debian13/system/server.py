#!/usr/bin/env python3
"""Small authenticated API for the standalone Debian 13 system prototype."""

from __future__ import annotations

import base64
import binascii
import hmac
import http.server
import json
import os
import platform
import re
import selectors
import shlex
import signal
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

from work_store import WorkStore
from original_agents import report as original_agents_report
from alex_handoff import AlexHandoffError, capabilities as alex_capabilities, submit_task as submit_to_alex
import mcp_server
import interactive_scripts as scripts
import system_observability as observability
from agent_monitor import AgentMonitor
from ai_bridge import BridgeError, execute_bridge_session, terminate_session
from svl_core import kernel as svl_kernel

MAX_BODY_BYTES = 16 * 1024
MAX_VISION_BODY_BYTES = 3 * 1024 * 1024
MAX_IMAGE_BYTES = 2 * 1024 * 1024
MAX_PROVIDER_RESPONSE_BYTES = 64 * 1024
MAX_OUTPUT_BYTES = 1024 * 1024
MAX_MESSAGE_CHARS = 4000
MAX_COMMAND_CHARS = 2000
COMMAND_TIMEOUT_SECONDS = 10
CHAT_TIMEOUT_SECONDS = 110
RATE_WINDOW_SECONDS = 60
RATE_LIMIT = 30

TASK_UUID = r"[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}"
TASK_DETAIL_PATH = re.compile(r"^/api/v1/tasks/(" + TASK_UUID + r")$")
TASK_CANCEL_PATH = re.compile(r"^/api/v1/tasks/(" + TASK_UUID + r")/cancel$")
TASK_DISPATCH_PATH = re.compile(r"^/api/v1/tasks/(" + TASK_UUID + r")/dispatch$")
WORK_KINDS = frozenset({"development", "bugfix", "audit", "research"})

# These identifiers select the owner's existing agents; they are not personas,
# assigned departments, instructions, credentials or elevated privileges.
# Authorization and tool restrictions are enforced by the runtime, not a prompt.
PROFILES = {
    "aria": {"name": "Aria"},
    "astyria": {"name": "Astyria"},
    "aurelius": {"name": "Aurelius"},
    "alex": {"name": "Alex"},
}

COMMANDS = {
    "pwd": "/usr/bin/pwd",
    "ls": "/usr/bin/ls",
    "cat": "/usr/bin/cat",
    "head": "/usr/bin/head",
    "tail": "/usr/bin/tail",
    "wc": "/usr/bin/wc",
    "grep": "/usr/bin/grep",
    "date": "/usr/bin/date",
    "whoami": "/usr/bin/whoami",
    "id": "/usr/bin/id",
}


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status = status
        self.code = code


class NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def validate_provider_url(value: str) -> str:
    if not value:
        return ""
    parsed = urllib.parse.urlsplit(value)
    if parsed.scheme == "https":
        allowed = True
    elif parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "localhost", "::1"}:
        allowed = True
    elif parsed.scheme == "http" and parsed.hostname == "ollama" and os.environ.get("DEBIAN13_ALLOW_INTERNAL_OLLAMA") == "1" and parsed.port == 11434:
        allowed = True
    else:
        allowed = False
    if (
        not allowed
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or not (
            parsed.path.rstrip("/").endswith("/v1/chat/completions")
            or (
                parsed.hostname == "generativelanguage.googleapis.com"
                and parsed.path.rstrip("/") == "/v1beta/openai/chat/completions"
            )
        )
    ):
        raise ValueError("MODEL_CHAT_URL must be an HTTPS chat-completions URL (HTTP is allowed for loopback tests).")
    return value


def safe_workspace_path(workspace: Path, raw_path: str) -> str:
    if not raw_path or raw_path.startswith("-"):
        raise ApiError(400, "invalid_path", "File arguments must be relative workspace paths.")
    candidate = (workspace / raw_path).resolve()
    try:
        relative = candidate.relative_to(workspace)
    except ValueError as exc:
        raise ApiError(403, "path_outside_workspace", "Path is outside the terminal workspace.") from exc
    return str(relative) if str(relative) != "." else "."


def command_argv(command: str, workspace: Path) -> list[str]:
    if not isinstance(command, str) or not command.strip() or len(command) > MAX_COMMAND_CHARS:
        raise ApiError(400, "invalid_command", "Supply a command of 1–2000 characters.")
    try:
        parts = shlex.split(command, posix=True)
    except ValueError as exc:
        raise ApiError(400, "invalid_command", "Command quoting is invalid.") from exc
    if not parts or parts[0] not in COMMANDS:
        raise ApiError(403, "command_not_allowed", "This terminal command is not in the allowed command list.")

    name, args = parts[0], parts[1:]
    if name in {"pwd", "date", "whoami", "id"}:
        if args:
            raise ApiError(400, "invalid_arguments", f"{name} does not accept arguments.")
        return [COMMANDS[name]]

    if name == "grep":
        if len(args) < 2 or args[0].startswith("-") or len(args[0]) > 200:
            raise ApiError(400, "invalid_arguments", "Use grep PATTERN WORKSPACE_FILE [...].")
        files = [safe_workspace_path(workspace, path) for path in args[1:]]
        return [COMMANDS[name], "--", args[0], *files]

    if name in {"head", "tail"}:
        count = "10"
        if args and args[0] == "-n":
            if (
                len(args) < 3
                or not args[1].isdigit()
                or len(args[1]) > 3
                or not 1 <= int(args[1]) <= 200
            ):
                raise ApiError(400, "invalid_arguments", f"Use {name} [-n 1..200] WORKSPACE_FILE [...].")
            count, args = args[1], args[2:]
        if not args:
            raise ApiError(400, "invalid_arguments", f"{name} requires a workspace file.")
        files = [safe_workspace_path(workspace, path) for path in args]
        return [COMMANDS[name], "-n", count, "--", *files]

    if name == "ls":
        paths = [safe_workspace_path(workspace, path) for path in args] if args else ["."]
        return [COMMANDS[name], "--", *paths]

    if name in {"cat", "wc"}:
        paths = [safe_workspace_path(workspace, path) for path in args] if args else []
        return [COMMANDS[name], "--", *paths]

    raise ApiError(403, "command_not_allowed", "This terminal command is not in the allowed command list.")


def run_terminal(command: str, workspace: Path) -> dict:
    argv = command_argv(command, workspace)
    try:
        process = subprocess.Popen(
            argv,
            cwd=workspace,
            env={"HOME": str(workspace), "LANG": "C.UTF-8", "PATH": "/usr/bin:/bin"},
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            close_fds=True,
            start_new_session=True,
        )
    except OSError as exc:
        raise ApiError(500, "command_start_failed", "The terminal command could not be started.") from exc

    selector = selectors.DefaultSelector()
    output = {"stdout": bytearray(), "stderr": bytearray()}
    for stream, key in ((process.stdout, "stdout"), (process.stderr, "stderr")):
        selector.register(stream, selectors.EVENT_READ, key)
    deadline = time.monotonic() + COMMAND_TIMEOUT_SECONDS
    total = 0
    try:
        while selector.get_map() or process.poll() is None:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
                raise ApiError(504, "command_timeout", "The terminal command exceeded its time limit.")
            for key, _ in selector.select(min(remaining, 0.2)):
                chunk = os.read(key.fileobj.fileno(), min(65536, MAX_OUTPUT_BYTES + 1 - total))
                if not chunk:
                    selector.unregister(key.fileobj)
                    continue
                total += len(chunk)
                if total > MAX_OUTPUT_BYTES:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait()
                    raise ApiError(413, "output_too_large", "Terminal output exceeded the 1 MiB limit.")
                output[key.data].extend(chunk)
        code = process.wait()
    finally:
        selector.close()
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait()
        for stream in (process.stdout, process.stderr):
            if stream:
                stream.close()
    return {
        "exitCode": code,
        "stdout": output["stdout"].decode("utf-8", errors="replace"),
        "stderr": output["stderr"].decode("utf-8", errors="replace"),
    }


def terminal_events(command: str, workspace: Path):
    """Real chunks from an allowlisted subprocess, without a shell or inherited secrets."""
    argv = command_argv(command, workspace)
    yield ("meta", {"command": argv[0].rsplit("/", 1)[-1], "mode": "live_readonly"})
    try:
        process = subprocess.Popen(
            argv, cwd=workspace,
            env={"HOME": str(workspace), "LANG": "C.UTF-8", "PATH": "/usr/bin:/bin"},
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            close_fds=True, start_new_session=True,
        )
    except OSError:
        yield ("error", {"code": "command_start_failed"})
        return
    selector = selectors.DefaultSelector()
    for stream, name in ((process.stdout, "stdout"), (process.stderr, "stderr")):
        selector.register(stream, selectors.EVENT_READ, name)
    total = 0
    deadline = time.monotonic() + COMMAND_TIMEOUT_SECONDS
    try:
        while selector.get_map() or process.poll() is None:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                yield ("error", {"code": "command_timeout"})
                return
            for key, _ in selector.select(min(0.2, remaining)):
                chunk = os.read(key.fileobj.fileno(), 65536)
                if not chunk:
                    selector.unregister(key.fileobj)
                    continue
                total += len(chunk)
                if total > MAX_OUTPUT_BYTES:
                    yield ("error", {"code": "output_too_large"})
                    return
                yield ("delta", {"stream": key.data, "text": chunk.decode("utf-8", errors="replace")})
        yield ("done", {"exitCode": process.wait()})
    finally:
        selector.close()
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait()
        for stream in (process.stdout, process.stderr):
            if stream:
                stream.close()


def validate_image(image: object) -> str:
    """Validate PNG/JPEG base64 and return a bounded data URI for the model."""
    if not isinstance(image, dict) or set(image) != {"mime_type", "base64"}:
        raise ApiError(400, "invalid_image", "Expected an image with mime_type and base64.")
    mime = image.get("mime_type")
    encoded = image.get("base64")
    if mime not in ("image/png", "image/jpeg") or not isinstance(encoded, str):
        raise ApiError(400, "invalid_image", "Only PNG and JPEG images are supported.")
    if len(encoded) > 4 * ((MAX_IMAGE_BYTES + 2) // 3):
        raise ApiError(413, "image_too_large", "Image exceeds 2 MiB.")
    try:
        raw = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ApiError(400, "invalid_image", "Invalid base64-encoded image.") from exc
    if len(raw) > MAX_IMAGE_BYTES or not raw:
        raise ApiError(413, "image_too_large", "Image exceeds 2 MiB or is empty.")
    if mime == "image/png":
        correct = raw.startswith(b"\x89PNG\r\n\x1a\n")
    else:
        correct = raw.startswith(b"\xff\xd8\xff")
    if not correct:
        raise ApiError(400, "invalid_image", "Image bytes do not match the declared MIME type.")
    return "data:" + mime + ";base64," + encoded


def provider_configured(config: dict) -> bool:
    url = config.get("model_chat_url", "")
    if not url or not config.get("model_name"):
        return False
    host = urllib.parse.urlsplit(url).hostname
    return bool(config.get("model_api_key") or host in {"127.0.0.1", "localhost", "::1", "ollama"})


def complete_chat(profile: str, message: str, config: dict, image: str | None = None) -> tuple[str, str]:
    if not provider_configured(config):
        raise ApiError(503, "provider_not_configured", "No AI model provider is configured.")
    model = config.get("vision_model_name", "") if image is not None else config["model_name"]
    if not model:
        raise ApiError(503, "vision_not_configured", "A vision model is not configured.")
    is_ollama = urllib.parse.urlsplit(config["model_chat_url"]).hostname == "ollama"
    user_content = message if image is None else [
        {"type": "text", "text": message},
        # Ollama's /v1 Chat Completions docs use a string image_url.
        {"type": "image_url", "image_url": image if is_ollama else {"url": image}},
    ]
    body = {
        "model": model,
        # No forced personality, specialty, root claims or system prompt.
        # The actual provider handles its model's base behavior; API tools
        # still enforce real permissions independently from chat output.
        "messages": [{"role": "user", "content": user_content}],
    }
    # Gemini 3.x does not require legacy sampling parameters.
    if urllib.parse.urlsplit(config["model_chat_url"]).hostname != "generativelanguage.googleapis.com":
        body["temperature"] = 0.2
    payload = json.dumps(body).encode("utf-8")
    request = urllib.request.Request(
        config["model_chat_url"],
        data=payload,
        headers={
            **({"Authorization": "Bearer " + config["model_api_key"]} if config["model_api_key"] else {}),
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        method="POST",
    )
    opener = urllib.request.build_opener(NoRedirectHandler())
    try:
        with opener.open(request, timeout=CHAT_TIMEOUT_SECONDS) as response:
            if response.status != 200:
                raise ApiError(502, "provider_error", "The configured model provider returned an error.")
            raw = response.read(MAX_PROVIDER_RESPONSE_BYTES + 1)
    except urllib.error.HTTPError as exc:
        raise ApiError(502, "provider_error", "The configured model provider returned an error.") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise ApiError(504, "provider_unavailable", "The configured model provider did not respond.") from exc
    if len(raw) > MAX_PROVIDER_RESPONSE_BYTES:
        raise ApiError(502, "provider_response_too_large", "The model provider response exceeded the size limit.")
    try:
        data = json.loads(raw)
        content = data["choices"][0]["message"]["content"]
    except (ValueError, KeyError, IndexError, TypeError) as exc:
        raise ApiError(502, "invalid_provider_response", "The model provider returned an invalid response.") from exc
    if not isinstance(content, str) or not content.strip():
        raise ApiError(502, "empty_provider_response", "The model provider returned no message.")
    return content.strip()[:MAX_MESSAGE_CHARS], model


def runtime_diagnostics(server) -> dict:
    linux_security = {}
    try:
        status = Path("/proc/self/status").read_text(encoding="utf-8")
        for line in status.splitlines():
            name, separator, value = line.partition(":")
            if not separator:
                continue
            if name == "NoNewPrivs":
                linux_security["noNewPrivileges"] = value.strip() == "1"
            elif name == "Seccomp":
                linux_security["seccompMode"] = int(value.strip())
            elif name == "CapEff":
                linux_security["effectiveCapabilities"] = value.strip()
    except (OSError, ValueError):
        pass

    return {
        "service": "debian13-system",
        "pythonVersion": platform.python_version(),
        "effectiveUid": os.geteuid(),
        "effectiveGid": os.getegid(),
        "workspaceWritable": os.access(server.workspace, os.W_OK),
        "providerConfigured": provider_configured(server.config),
        "linuxSecurity": linux_security,
    }


def ollama_status(config: dict) -> dict:
    selected_model = config.get("vision_model_name") or config.get("model_name") or ""
    if not config.get("model_chat_url"):
        return {"state": "not_selected"}
    request = urllib.request.Request(
        "http://127.0.0.1:11434/api/version",
        headers={"Accept": "application/json"},
    )
    try:
        with urllib.request.build_opener(NoRedirectHandler()).open(request, timeout=0.4) as response:
            if response.status == 200:
                payload = json.loads(response.read(4096))
                version = payload.get("version") if isinstance(payload, dict) else ""
                return {
                    "state": "reachable",
                    "version": version if isinstance(version, str) else "",
                    "selectedModel": selected_model or "llama3.2-vision:11b",
                }
    except (OSError, ValueError, urllib.error.URLError):
        pass
    return {
        "state": "unavailable",
        "selectedModel": selected_model or "llama3.2-vision:11b",
    }


class Debian13Server(http.server.ThreadingHTTPServer):
    daemon_threads = True
    request_queue_size = 32

    def __init__(self, address: tuple[str, int], config: dict):
        self.config = config
        self.workspace = Path(config["workspace"]).resolve()
        self.workspace.mkdir(parents=True, exist_ok=True)
        self.work_store = WorkStore(self.workspace)
        self._alex_dispatch_lock = threading.Lock()
        self._rates: dict[str, tuple[float, int]] = {}
        self._rates_lock = threading.Lock()
        self.agent_monitor = None
        super().__init__(address, SystemHandler)
        if config.get("agent_monitor_enabled", False):
            self.agent_monitor = AgentMonitor(
                interval_seconds=config.get("agent_monitor_interval", 30))

    def server_close(self):
        if self.agent_monitor is not None:
            self.agent_monitor.close()
        super().server_close()


class SystemHandler(http.server.BaseHTTPRequestHandler):
    server: Debian13Server
    protocol_version = "HTTP/1.1"

    def log_message(self, format: str, *args) -> None:
        # Never log request bodies, authorization headers, or model credentials.
        super().log_message("%s %s", self.address_string(), self.path.split("?", 1)[0])

    def _send_json(self, status: int, data: dict, headers: dict | None = None) -> None:
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        if self.close_connection:
            self.send_header("Connection", "close")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(body)

    def _send_chat_events(self, content: str, profile: str, model: str) -> None:
        # A provider response is delivered as a single event after completion.
        # This is NOT token-by-token model streaming and is not persisted.
        parsed_url = urllib.parse.urlsplit(self.server.config["model_chat_url"])
        source = "ollama" if (
            parsed_url.hostname == "ollama"
            or parsed_url.port == 11434
            or model.startswith("llama3.2-vision")
        ) else "model_provider"
        frames = [
            ("meta", {"model": model, "session_persisted": False}),
            ("delta", {"text": content}),
            ("done", {"agent": profile, "source": source, "model": model}),
        ]
        body = "".join(
            "event: " + event + "\n" +
            "data: " + json.dumps(payload, ensure_ascii=False) + "\n\n"
            for event, payload in frames
        ).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def _send_terminal_stream(self, command: str) -> None:
        # Validate BEFORE sending SSE headers so invalid commands retain HTTP errors.
        command_argv(command, self.server.workspace)
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache, no-store")
        self.send_header("X-Accel-Buffering", "no")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True
        events = terminal_events(command, self.server.workspace)
        try:
            for name, payload in events:
                frame = "event: " + name + "\n" + "data: " + json.dumps(payload, ensure_ascii=False) + "\n\n"
                self.wfile.write(frame.encode("utf-8"))
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            events.close()

    def _authorize(self) -> bool:
        expected = "Bearer " + self.server.config["api_token"]
        supplied = self.headers.get("Authorization", "")
        if not hmac.compare_digest(supplied.encode(), expected.encode()):
            # A rejected POST body is unread; never parse it as another request.
            self.close_connection = True
            self._send_json(401, {"error": {
                "code": "unauthorized",
                "message": "A valid bearer token is required.",
                "requestId": str(uuid.uuid4()),
            }})
            return False
        return True

    def _allow_request(self) -> bool:
        now = time.monotonic()
        address = self.client_address[0]
        with self.server._rates_lock:
            start, count = self.server._rates.get(address, (now, 0))
            if now - start >= RATE_WINDOW_SECONDS:
                start, count = now, 0
            if count >= self.server.config.get("rate_limit", RATE_LIMIT):
                retry = max(1, int(RATE_WINDOW_SECONDS - (now - start)))
                self._send_json(
                    429,
                    {"error": {
                        "code": "rate_limited",
                        "message": "Request rate limit exceeded.",
                        "requestId": str(uuid.uuid4()),
                    }},
                    {"Retry-After": str(retry)},
                )
                return False
            self.server._rates[address] = (start, count + 1)
            if len(self.server._rates) > 1024:
                self.server._rates = {
                    key: value for key, value in self.server._rates.items()
                    if now - value[0] < RATE_WINDOW_SECONDS
                }
        return True

    def _read_json(self, limit: int = MAX_BODY_BYTES) -> dict:
        raw_length = self.headers.get("Content-Length", "")
        if not raw_length.isdigit():
            raise ApiError(400, "invalid_content_length", "A valid Content-Length header is required.")
        if len(raw_length) > 8:
            raise ApiError(413, "request_too_large", "Request body exceeds 16 KiB.")
        length = int(raw_length)
        if length > limit:
            raise ApiError(413, "request_too_large", "Request body exceeds the size limit.")
        try:
            data = json.loads(self.rfile.read(length))
        except (ValueError, UnicodeDecodeError) as exc:
            raise ApiError(400, "invalid_json", "Request body must be valid JSON.") from exc
        if not isinstance(data, dict):
            raise ApiError(400, "invalid_json", "Request body must be a JSON object.")
        return data

    def do_GET(self) -> None:
        if self.path == "/healthz":
            self._send_json(200, {"status": "ok"})
            return
        if not self._allow_request():
            return
        if not self._authorize():
            return
        if self.path == "/mcp":
            # This stateless MCP endpoint is POST-only; GET streaming is not offered.
            self.send_response(405)
            self.send_header("Allow", "POST")
            self.send_header("Content-Length", "0")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            return
        if self.path == "/api/v1/diagnostics":
            self._send_json(200, observability.snapshot(self.server))
            return
        if self.path == "/api/v1/scripts":
            self._send_json(200, scripts.catalog())
            return
        if self.path == "/v1/profiles":
            self._send_json(200, {
                "profiles": [
                    {"id": key, "name": value["name"], "active": False}
                    for key, value in PROFILES.items()
                ],
                "providerConfigured": provider_configured(self.server.config),
            })
            return
        if self.path == "/v1/diagnostics":
            self._send_json(200, runtime_diagnostics(self.server))
            return
        if self.path in ("/v1/integrations", "/api/v1/integrations"):
            self._send_json(200, {"integrations": [
                {"id": "mcp", "name": "MCP", "status": "read_and_queue_available",
                 "connected": False, "description": "MCP reads and review-only Work queue writes available; external client not verified."},
                {"id": "gpt_actions", "name": "GPT Actions", "status": "schema_available",
                 "connected": False, "description": "OpenAPI document available; HTTPS deployment and GPT setup required."},
                {"id": "codex", "name": "Codex", "status": "workflow_available",
                 "connected": False, "description": "GitHub Actions workflow; requires OPENAI_API_KEY and a verified run."},
            ]})
            return
        if self.path == "/api/v1/status":
            report = runtime_diagnostics(self.server)
            try:
                svl_report = json.loads(svl_kernel.generate_system_status_report())
            except Exception:
                svl_report = {"status": "active", "version": "5.6.0-QuantumWheel"}
            self._send_json(200, {
                "application": {"status": "running", "product": "Debian 13 Console"},
                "operating_system": {"runtime": {
                    "distribution": platform.platform(),
                    "debian_13_boot_verified": False,
                }},
                "model": {
                    "status": "configured" if report["providerConfigured"] else "not_configured",
                    "inference": "unverified",
                },
                "ollama": ollama_status(self.server.config),
                "svl_core": svl_report,
                "task_state": "Brak uruchomionych workerów",
            })
            return
        if self.path == "/api/v1/original-agents":
            self._send_json(200, original_agents_report())
            return
        if self.path == "/api/v1/agents":
            self._send_json(200, {"agents": [
                {"id": key, "name": profile["name"],
                 "privileges": "not_granted_by_profile",
                 "access_level": "runtime_enforced",
                 "status": "profile_configured", "worker": "not_running"}
                for key, profile in PROFILES.items()
            ]})
            return
        if self.path == "/api/v1/tasks":
            tasks = self.server.work_store.list_tasks()
            for task in tasks:
                if task.get("agent") == "alex":
                    task["alexSubmission"] = self.server.work_store.get_alex_submission(task["id"])
            self._send_json(200, {"tasks": tasks,
                                  "storage": "sqlite", "workersRunning": 0})
            return
        if self.path == "/api/v1/events":
            self._send_json(200, {"events": self.server.work_store.list_events(),
                                  "storage": "sqlite"})
            return
        if self.path == "/api/v1/alex/capabilities":
            try:
                status = alex_capabilities()
            except (AlexHandoffError, ValueError):
                status = {"configured": True, "supported": False,
                          "agentRuntimeVerified": False, "reason": "alex_endpoint_unavailable"}
            self._send_json(200, {"alex": status})
            return
        match = TASK_DETAIL_PATH.fullmatch(self.path)
        if match:
            task = self.server.work_store.get_task(match.group(1))
            if task is None:
                self._send_json(404, {"error": {"code": "task_not_found", "message": "Task not found."}})
            else:
                self._send_json(200, {
                    "task": task, "alexSubmission":
                    self.server.work_store.get_alex_submission(task["id"])
                })
            return
        self._send_json(404, {"error": {"code": "not_found", "requestId": str(uuid.uuid4())}})

    def do_POST(self) -> None:
        if not self._allow_request():
            return
        if not self._authorize():
            return
        request_id = str(uuid.uuid4())
        try:
            if self.path == "/mcp":
                # Reject browser origins to prevent DNS rebinding; clients are server-to-server.
                if self.headers.get("Origin"):
                    raise ApiError(403, "origin_forbidden", "Browser Origin is not allowed for MCP.")
                content_type = self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
                accept = self.headers.get("Accept", "").lower()
                if content_type != "application/json":
                    raise ApiError(415, "invalid_content_type", "MCP requires application/json.")
                if "application/json" not in accept or "text/event-stream" not in accept:
                    raise ApiError(406, "invalid_accept", "MCP requires JSON and SSE in Accept.")
                message = self._read_json()
                def queue_task(arguments):
                    task = self.server.work_store.create_task(
                        arguments["kind"], arguments["agent"],
                        arguments["title"], arguments["description"])
                    return {"task": task, "executionStarted": False}

                def read_task(arguments):
                    task = self.server.work_store.get_task(arguments["task_id"])
                    if task is None:
                        raise ValueError("Task not found")
                    return {"task": task}

                def cancel_task(arguments):
                    with self.server._alex_dispatch_lock:
                        if self.server.work_store.get_alex_submission(arguments["task_id"]):
                            raise ValueError("Task already handed off")
                        task = self.server.work_store.cancel_task(arguments["task_id"])
                    if task is None:
                        raise ValueError("Task not found")
                    return {"task": task, "executionStarted": False}

                def chat_with_model(arguments):
                    try:
                        content, model = complete_chat(
                            arguments["agent"], arguments["message"], self.server.config)
                    except ApiError:
                        # No synthetic replies and no error details or credentials in MCP.
                        raise ValueError("Model not available") from None
                    return {"agent": arguments["agent"], "reply": content,
                            "model": model, "source": "configured_model_provider",
                            "originalAgentExecutionVerified": False,
                            "sessionPersisted": False}

                result = mcp_server.response(
                    message, task_writer=queue_task, detail_reader=read_task,
                    cancel_writer=cancel_task, chat_writer=chat_with_model)
                if result is None:
                    self.send_response(202)
                    self.send_header("Content-Length", "0")
                    self.send_header("Cache-Control", "no-store")
                    self.end_headers()
                else:
                    self._send_json(200, result)
                return
            dispatch = TASK_DISPATCH_PATH.fullmatch(self.path)
            if dispatch:
                if self.headers.get("Content-Length", "0") not in ("", "0") or self.headers.get("Transfer-Encoding"):
                    self.close_connection = True
                    raise ApiError(400, "unexpected_body", "Task dispatch takes no request body.")
                task_id = dispatch.group(1)
                with self.server._alex_dispatch_lock:
                    existing = self.server.work_store.get_alex_submission(task_id)
                    if existing:
                        self._send_json(200, {"alexSubmission": existing,
                                              "executionStarted": False})
                        return
                    task = self.server.work_store.get_task(task_id)
                    if task is None:
                        raise ApiError(404, "task_not_found", "Task not found.")
                    receipt = submit_to_alex(task)
                    submission = self.server.work_store.record_alex_submission(
                        task_id, receipt["remoteTaskId"])
                self._send_json(202, {"alexSubmission": submission,
                                      "executionStarted": False,
                                      "agentExecutionVerified": False})
                return
            cancel = TASK_CANCEL_PATH.fullmatch(self.path)
            if cancel:
                if self.headers.get("Content-Length", "0") not in ("", "0") or self.headers.get("Transfer-Encoding"):
                    self.close_connection = True
                    raise ApiError(400, "unexpected_body", "Task cancellation takes no request body.")
                with self.server._alex_dispatch_lock:
                    if self.server.work_store.get_alex_submission(cancel.group(1)):
                        raise ApiError(409, "already_handed_off",
                                       "Task accepted by Alex API; local cancellation cannot recall it.")
                    task = self.server.work_store.cancel_task(cancel.group(1))
                if task is None:
                    raise ApiError(404, "task_not_found", "Task not found.")
                self._send_json(200, {"task": task})
                return
            data = self._read_json(MAX_VISION_BODY_BYTES if self.path == "/v1/vision" else MAX_BODY_BYTES)
            if self.path == "/api/v1/terminal/stream":
                if set(data) != {"command"}:
                    raise ApiError(400, "invalid_command", "Provide command only.")
                self._send_terminal_stream(data["command"])
                return
            if self.path == "/api/v1/scripts/start":
                if set(data) != {"scriptId"}:
                    raise ApiError(400, "invalid_script", "Provide scriptId only.")
                try:
                    result = scripts.start(data["scriptId"])
                except scripts.ScriptError as exc:
                    raise ApiError(exc.status, exc.code, "Script request could not start.") from None
                self._send_json(201, result)
                return
            if self.path == "/api/v1/scripts/respond":
                if set(data) != {"sessionId", "answer"}:
                    raise ApiError(400, "invalid_script", "Provide sessionId and answer.")
                try:
                    result = scripts.respond(data["sessionId"], data["answer"], self.server.workspace)
                except scripts.ScriptError as exc:
                    raise ApiError(exc.status, exc.code, "Script input was rejected.") from None
                self._send_json(200, result)
                return
            if self.path == "/api/v1/scripts/cancel":
                if set(data) != {"sessionId"}:
                    raise ApiError(400, "invalid_script", "Provide sessionId.")
                try:
                    result = scripts.cancel(data["sessionId"])
                except scripts.ScriptError as exc:
                    raise ApiError(exc.status, exc.code, "Script cannot be cancelled.") from None
                self._send_json(200, result)
                return
            if self.path == "/api/v1/bridge":
                if set(data) - {"sessionId", "message", "useOpenai"}:
                    raise ApiError(400, "invalid_bridge_request", "Unsupported bridge request fields.")
                try:
                    result = execute_bridge_session(
                        data.get("sessionId", ""), data.get("message"),
                        data.get("useOpenai", True))
                except BridgeError as exc:
                    raise ApiError(exc.status, exc.code, "AI Bridge request did not complete.") from None
                self._send_json(200, result)
                return
            if self.path == "/api/v1/bridge/terminate":
                session_id = data.get("sessionId")
                if set(data) != {"sessionId"} or not isinstance(session_id, str) or not re.fullmatch(TASK_UUID, session_id):
                    raise ApiError(400, "invalid_session", "Provide a valid bridge session ID.")
                self._send_json(200, {"terminated": terminate_session(session_id)})
                return
            if self.path == "/api/v1/tasks":
                if set(data) != {"agent", "kind", "title", "description"}:
                    raise ApiError(400, "invalid_task", "Required: agent, kind, title, description.")
                agent, kind = data["agent"], data["kind"]
                title, description = data["title"], data["description"]
                if (not isinstance(agent, str) or agent not in PROFILES
                        or not isinstance(kind, str) or kind not in WORK_KINDS
                        or not isinstance(title, str) or not 1 <= len(title.strip()) <= 120
                        or not isinstance(description, str) or not 1 <= len(description.strip()) <= 2000):
                    raise ApiError(400, "invalid_task", "Invalid task fields or length.")
                try:
                    task = self.server.work_store.create_task(
                        kind, agent, title.strip(), description.strip())
                except ValueError as exc:
                    raise ApiError(409, "task_limit", "Task ledger is at capacity.") from exc
                self._send_json(201, {"task": task, "executionStarted": False})
                return
            if self.path in ("/v1/terminal", "/api/v1/terminal"):
                result = run_terminal(data.get("command"), self.server.workspace)
                if self.path == "/api/v1/terminal":
                    self._send_json(200, {"requestId": request_id, "output": result["stdout"] + result["stderr"],
                                          "exitCode": result["exitCode"]})
                else:
                    self._send_json(200, {"requestId": request_id, **result})
                return
            if self.path in ("/v1/chat", "/v1/vision", "/api/v1/chat/stream"):
                profile = data.get("agent") if self.path == "/api/v1/chat/stream" else data.get("profile")
                message = data.get("message")
                if not isinstance(profile, str) or profile not in PROFILES:
                    raise ApiError(400, "invalid_profile", "Select aria, astyria, aurelius, or alex.")
                if not isinstance(message, str) or not message.strip() or len(message) > MAX_MESSAGE_CHARS:
                    raise ApiError(400, "invalid_message", "Message must contain 1–4000 characters.")
                image = validate_image(data.get("image")) if self.path == "/v1/vision" else None
                content, model = complete_chat(profile, message.strip(), self.server.config, image)
                if self.path == "/api/v1/chat/stream":
                    self._send_chat_events(content, profile, model)
                    return
                self._send_json(200, {
                    "requestId": request_id,
                    "profile": profile,
                    "content": content,
                    "model": model,
                })
                return
            self._send_json(404, {"error": {"code": "not_found", "requestId": request_id}})
        except ApiError as exc:
            self._send_json(exc.status, {"error": {"code": exc.code, "message": str(exc), "requestId": request_id}})
        except AlexHandoffError as exc:
            code = 409 if exc.code in ("invalid_task", "task_already_executed",
                                       "dispatch_not_enabled", "unsupported_capability",
                                       "submission_unverified") else 503
            self._send_json(code, {"error": {"code": exc.code, "message": str(exc),
                                             "requestId": request_id}})
        except ValueError:
            self._send_json(409, {"error": {"code": "dispatch_conflict",
                                             "message": "Task cannot be dispatched.",
                                             "requestId": request_id}})
        except (BrokenPipeError, ConnectionResetError):
            return


def load_config() -> dict:
    token = os.environ.get("DEBIAN13_API_TOKEN", "")
    if len(token) < 32:
        raise SystemExit("DEBIAN13_API_TOKEN must contain at least 32 characters.")
    custom_url = os.environ.get("MODEL_CHAT_URL", "").strip()
    custom_key = os.environ.get("MODEL_API_KEY", "")
    gemini_key = os.environ.get("GEMINI_API_KEY", "")
    # Gemini is opt-in via its own key. Never send a Gemini key to a
    # custom MODEL_CHAT_URL; explicitly configured providers take precedence.
    use_gemini = bool(
        gemini_key and not any((custom_url, custom_key, os.environ.get("MODEL_NAME", "")))
    )
    requested_url = (
        "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"
        if use_gemini else custom_url
    )
    try:
        provider_url = validate_provider_url(requested_url)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    model_name = (
        (os.environ.get("GEMINI_MODEL", "").strip() or "gemini-3.8-flash")
        if use_gemini else os.environ.get("MODEL_NAME", "")
    )
    vision_model_name = (
        (os.environ.get("GEMINI_VISION_MODEL", "").strip() or model_name)
        if use_gemini else os.environ.get("VISION_MODEL_NAME", "")
    )
    return {
        "api_token": token,
        "model_chat_url": provider_url,
        "model_api_key": gemini_key if use_gemini else custom_key,
        "model_name": model_name,
        "vision_model_name": vision_model_name,
        "workspace": os.environ.get("WORKSPACE_DIR", "/workspace"),
        "agent_monitor_enabled": os.environ.get("DEBIAN13_AGENT_MONITOR_ENABLED") == "1",
        "agent_monitor_interval": 30,
        "host": os.environ.get("HOST", "127.0.0.1"),
        "port": int(os.environ.get("PORT", "8080")),
    }


def main() -> None:
    config = load_config()
    server = Debian13Server((config["host"], config["port"]), config)
    print(f"Debian 13 system API listening on {config['host']}:{config['port']}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
