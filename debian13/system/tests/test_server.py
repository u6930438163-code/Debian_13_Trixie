import base64
import http.client
import json
import os
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from server import Debian13Server, validate_provider_url


TOKEN = "test-token-with-at-least-thirty-two-characters"


class ProviderHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        self.server.authorization = self.headers.get("Authorization")
        self.server.body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        body = json.dumps({"choices": [{"message": {"content": "Real test-provider response."}}]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_):
        pass


class Debian13ApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.workspace = Path(self.temp.name) / "workspace"
        self.workspace.mkdir()
        (self.workspace / "hello.txt").write_text("hello Debian 13\n", encoding="utf-8")
        self.server = Debian13Server(("127.0.0.1", 0), {
            "api_token": TOKEN,
            "model_chat_url": "",
            "model_api_key": "",
            "model_name": "",
            "workspace": str(self.workspace),
        })
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.connection = http.client.HTTPConnection(*self.server.server_address)

    def tearDown(self):
        self.connection.close()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.temp.cleanup()

    def request_json(self, method, path, body=None, token=TOKEN):
        headers = {"Accept": "application/json"}
        if token is not None:
            headers["Authorization"] = "Bearer " + token
        payload = None
        if body is not None:
            payload = json.dumps(body)
            headers["Content-Type"] = "application/json"
        self.connection.request(method, path, body=payload, headers=headers)
        response = self.connection.getresponse()
        self.last_retry_after = response.getheader("Retry-After")
        data = json.loads(response.read())
        return response.status, data

    def test_health_is_public_but_profiles_require_authentication(self):
        self.assertEqual(self.request_json("GET", "/healthz", token=None)[0], 200)
        status, _ = self.request_json("GET", "/v1/profiles", token=None)
        self.assertEqual(status, 401)
        status, data = self.request_json("GET", "/v1/profiles")
        self.assertEqual(status, 200)
        self.assertEqual([profile["id"] for profile in data["profiles"]], ["aria", "astyria", "aurelius", "alex"])
        self.assertTrue(all(profile["active"] is False for profile in data["profiles"]))
        self.assertTrue(all("role" not in profile and "prompt" not in profile
                            and "privileges" not in profile for profile in data["profiles"]))
        status, agents = self.request_json("GET", "/api/v1/agents")
        self.assertEqual(status, 200)
        self.assertTrue(all(agent["access_level"] == "runtime_enforced"
                            and agent["privileges"] == "not_granted_by_profile"
                            and "role" not in agent
                            for agent in agents["agents"]))

    def test_diagnostics_are_authenticated_and_do_not_disclose_secrets(self):
        self.assertEqual(self.request_json("GET", "/v1/diagnostics", token=None)[0], 401)
        self.assertEqual(self.request_json("GET", "/v1/diagnostics", token="invalid-token")[0], 401)
        self.server.config.update({
            "model_chat_url": "https://provider.example/v1/chat/completions",
            "model_api_key": "diagnostics-provider-secret",
            "model_name": "diagnostics-test-model",
        })
        status, data = self.request_json("GET", "/v1/diagnostics")
        self.assertEqual(status, 200)
        self.assertEqual(data["service"], "debian13-system")
        self.assertEqual(data["effectiveUid"], os.geteuid())
        self.assertTrue(data["workspaceWritable"])
        self.assertTrue(data["providerConfigured"])
        self.assertIsInstance(data["linuxSecurity"], dict)
        serialized = json.dumps(data)
        self.assertNotIn(TOKEN, serialized)
        self.assertNotIn("diagnostics-provider-secret", serialized)
        self.assertNotIn("diagnostics-test-model", serialized)

    def test_terminal_executes_allowlisted_argv_without_shell_or_secret_inheritance(self):
        status, data = self.request_json("POST", "/v1/terminal", {"command": "cat hello.txt"})
        self.assertEqual(status, 200)
        self.assertEqual(data["stdout"], "hello Debian 13\n")
        self.assertEqual(data["exitCode"], 0)

    def test_terminal_sse_frames_use_real_line_breaks(self):
        self.connection.request(
            "POST", "/api/v1/terminal/stream",
            body=json.dumps({"command": "cat hello.txt"}),
            headers={"Authorization": "Bearer " + TOKEN,
                     "Content-Type": "application/json"},
        )
        response = self.connection.getresponse()
        events = response.read().decode("utf-8")
        self.assertEqual(response.status, 200)
        self.assertTrue(response.getheader("Content-Type").startswith("text/event-stream"))
        self.assertEqual(response.getheader("Cache-Control"), "no-cache, no-store")
        self.assertEqual(response.getheader("X-Content-Type-Options"), "nosniff")
        self.assertEqual(response.getheader("X-Accel-Buffering"), "no")
        self.assertIn('event: meta\n', events)
        self.assertIn('event: delta\n', events)
        self.assertIn('event: done\n', events)
        self.assertIn("hello Debian 13", events)
        frames = [frame for frame in events.split("\n\n") if frame]
        self.assertEqual([frame.split("\n", 1)[0] for frame in frames],
                         ["event: meta", "event: delta", "event: done"])
        for frame in frames:
            lines = frame.split("\n")
            self.assertEqual(len(lines), 2)
            self.assertTrue(lines[1].startswith("data: "))
            self.assertIsInstance(json.loads(lines[1][6:]), dict)
        self.assertNotIn(r"\\n", events)

    def test_terminal_stream_rejects_unauthenticated_post_and_closes_connection(self):
        for token in (None, "invalid-token"):
            headers = {"Content-Type": "application/json"}
            if token is not None:
                headers["Authorization"] = "Bearer " + token
            self.connection.request(
                "POST", "/api/v1/terminal/stream",
                body=json.dumps({"command": "cat hello.txt"}),
                headers=headers,
            )
            response = self.connection.getresponse()
            payload = json.loads(response.read())
            self.assertEqual(response.status, 401)
            self.assertEqual(payload["error"]["code"], "unauthorized")
            self.assertEqual(response.getheader("Connection"), "close")

    def test_terminal_rejects_shells_unknown_commands_and_paths_outside_workspace(self):
        self.assertEqual(self.request_json("POST", "/v1/terminal", {"command": "sh -c id"})[0], 403)
        status, data = self.request_json("POST", "/v1/terminal", {"command": "cat ../../etc/passwd"})
        self.assertEqual(status, 403)
        self.assertEqual(data["error"]["code"], "path_outside_workspace")
        (self.workspace / "outside.txt").symlink_to("/etc/passwd")
        self.assertEqual(self.request_json("POST", "/v1/terminal", {"command": "cat outside.txt"})[0], 403)

    def test_terminal_stream_rejects_workspace_escape_before_sse_headers(self):
        (self.workspace / "escape.txt").symlink_to("/etc/passwd")
        for command in ("cat ../../etc/passwd", "cat escape.txt"):
            with self.subTest(command=command):
                self.connection.request(
                    "POST", "/api/v1/terminal/stream",
                    body=json.dumps({"command": command}),
                    headers={"Authorization": "Bearer " + TOKEN,
                             "Content-Type": "application/json"},
                )
                response = self.connection.getresponse()
                payload = json.loads(response.read())
                self.assertEqual(response.status, 403)
                self.assertEqual(payload["error"]["code"], "path_outside_workspace")
                self.assertTrue(response.getheader("Content-Type").startswith("application/json"))

    def test_terminal_output_is_bounded(self):
        (self.workspace / "large.txt").write_bytes(b"x" * (1024 * 1024 + 1))
        status, data = self.request_json("POST", "/v1/terminal", {"command": "cat large.txt"})
        self.assertEqual(status, 413)
        self.assertEqual(data["error"]["code"], "output_too_large")

    def test_chat_rejects_non_string_profile(self):
        status, data = self.request_json("POST", "/v1/chat", {"profile": [], "message": "test"})
        self.assertEqual(status, 400)
        self.assertEqual(data["error"]["code"], "invalid_profile")

    def test_chat_and_vision_reject_invalid_message_before_provider(self):
        for path in ("/v1/chat", "/v1/vision"):
            for message in (None, 42, [], {"text": "hello"}, "", " ", "x" * 4001):
                with self.subTest(path=path, message=message):
                    status, data = self.request_json("POST", path, {
                        "profile": "aria", "message": message,
                    })
                    self.assertEqual(status, 400)
                    self.assertEqual(data["error"]["code"], "invalid_message")

    def test_chat_and_vision_require_authentication_before_provider(self):
        for path in ("/v1/chat", "/v1/vision"):
            for token in (None, "invalid-token"):
                with self.subTest(path=path, token=token):
                    status, data = self.request_json("POST", path, {
                        "profile": "aria", "message": "Hello",
                    }, token=token)
                    self.assertEqual(status, 401)
                    self.assertEqual(data["error"]["code"], "unauthorized")

    def test_chat_fails_closed_without_a_model_provider(self):
        status, data = self.request_json("POST", "/v1/chat", {"profile": "aria", "message": "test"})
        self.assertEqual(status, 503)
        self.assertEqual(data["error"]["code"], "provider_not_configured")

    def test_vision_fails_closed_without_a_model_provider(self):
        photo = base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"test-image-bytes").decode()
        status, data = self.request_json("POST", "/v1/vision", {
            "profile": "aria", "message": "Describe this image.",
            "image": {"mime_type": "image/png", "base64": photo},
        })
        self.assertEqual(status, 503)
        self.assertEqual(data["error"]["code"], "provider_not_configured")

    def test_mobile_gui_chat_stream_requires_authentication(self):
        for token in (None, "invalid-token"):
            headers = {"Content-Type": "application/json"}
            if token is not None:
                headers["Authorization"] = "Bearer " + token
            self.connection.request(
                "POST", "/api/v1/chat/stream",
                body=json.dumps({"agent": "alex", "message": "Write tests"}),
                headers=headers,
            )
            response = self.connection.getresponse()
            payload = json.loads(response.read())
            self.assertEqual(response.status, 401)
            self.assertEqual(payload["error"]["code"], "unauthorized")
            self.assertEqual(response.getheader("Connection"), "close")

    def test_mobile_gui_chat_stream_rejects_invalid_input_before_provider(self):
        for payload, code in (
            ({"agent": None, "message": "Hello"}, "invalid_profile"),
            ({"agent": 42, "message": "Hello"}, "invalid_profile"),
            ({"agent": [], "message": "Hello"}, "invalid_profile"),
            ({"agent": {"name": "aria"}, "message": "Hello"}, "invalid_profile"),
            ({"agent": "unknown", "message": "Hello"}, "invalid_profile"),
            ({"agent": "aria", "message": None}, "invalid_message"),
            ({"agent": "aria", "message": 42}, "invalid_message"),
            ({"agent": "aria", "message": []}, "invalid_message"),
            ({"agent": "aria", "message": {"text": "Hello"}}, "invalid_message"),
            ({"agent": "aria", "message": ""}, "invalid_message"),
            ({"agent": "aria", "message": " "}, "invalid_message"),
            ({"agent": "aria", "message": "x" * 4001}, "invalid_message"),
        ):
            with self.subTest(code=code, payload=payload):
                self.connection.request(
                    "POST", "/api/v1/chat/stream",
                    body=json.dumps(payload),
                    headers={"Authorization": "Bearer " + TOKEN,
                             "Content-Type": "application/json"},
                )
                response = self.connection.getresponse()
                result = json.loads(response.read())
                self.assertEqual(response.status, 400)
                self.assertEqual(result["error"]["code"], code)
                self.assertTrue(response.getheader("Content-Type").startswith("application/json"))

    def test_mobile_gui_chat_stream_fails_closed_without_provider(self):
        self.connection.request(
            "POST", "/api/v1/chat/stream",
            body=json.dumps({"agent": "alex", "message": "Write tests"}),
            headers={"Authorization": "Bearer " + TOKEN,
                     "Content-Type": "application/json"},
        )
        response = self.connection.getresponse()
        payload = json.loads(response.read())
        self.assertEqual(response.status, 503)
        self.assertEqual(payload["error"]["code"], "provider_not_configured")
        self.assertTrue(response.getheader("Content-Type").startswith("application/json"))

    def test_local_ollama_compatible_provider_works_without_api_key(self):
        provider = ThreadingHTTPServer(("127.0.0.1", 0), ProviderHandler)
        worker = threading.Thread(target=provider.serve_forever, daemon=True)
        worker.start()
        try:
            self.server.config.update({
                "model_chat_url": f"http://127.0.0.1:{provider.server_port}/v1/chat/completions",
                "model_api_key": "",
                "model_name": "local-test-model",
            })
            status, profiles = self.request_json("GET", "/v1/profiles")
            self.assertEqual(status, 200)
            self.assertTrue(profiles["providerConfigured"])
            status, diagnostics = self.request_json("GET", "/v1/diagnostics")
            self.assertEqual(status, 200)
            self.assertTrue(diagnostics["providerConfigured"])
            status, response = self.request_json("POST", "/v1/chat", {
                "profile": "aria", "message": "Local inference fixture",
            })
            self.assertEqual(status, 200)
            self.assertEqual(response["content"], "Real test-provider response.")
            self.assertIsNone(provider.authorization)
            self.assertEqual(provider.body["model"], "local-test-model")
        finally:
            provider.shutdown()
            provider.server_close()
            worker.join(timeout=2)

    def test_local_keyless_provider_supports_authenticated_gui_sse(self):
        provider = ThreadingHTTPServer(("127.0.0.1", 0), ProviderHandler)
        worker = threading.Thread(target=provider.serve_forever, daemon=True)
        worker.start()
        try:
            self.server.config.update({
                "model_chat_url": f"http://127.0.0.1:{provider.server_port}/v1/chat/completions",
                "model_api_key": "",
                "model_name": "local-sse-model",
            })
            self.connection.request(
                "POST", "/api/v1/chat/stream",
                body=json.dumps({"agent": "aria", "message": "Test local SSE"}),
                headers={"Authorization": "Bearer " + TOKEN,
                         "Content-Type": "application/json"},
            )
            reply = self.connection.getresponse()
            events = reply.read().decode("utf-8")
            self.assertEqual(reply.status, 200)
            self.assertTrue(reply.getheader("Content-Type").startswith("text/event-stream"))
            self.assertIn("event: delta\n", events)
            self.assertIn("Real test-provider response.", events)
            self.assertIn("event: done\n", events)
            self.assertEqual(provider.body["model"], "local-sse-model")
            self.assertEqual(provider.body["messages"], [
                {"role": "user", "content": "Test local SSE"},
            ])
            self.assertIsNone(provider.authorization)
        finally:
            provider.shutdown()
            provider.server_close()
            worker.join(timeout=2)

    def test_remote_provider_without_key_is_not_configured(self):
        self.server.config.update({
            "model_chat_url": "https://provider.example/v1/chat/completions",
            "model_api_key": "",
            "model_name": "remote-model",
        })
        status, profiles = self.request_json("GET", "/v1/profiles")
        self.assertEqual(status, 200)
        self.assertFalse(profiles["providerConfigured"])
        status, response = self.request_json("POST", "/v1/chat", {
            "profile": "aria", "message": "Must not send",
        })
        self.assertEqual(status, 503)
        self.assertEqual(response["error"]["code"], "provider_not_configured")

    def test_chat_calls_configured_provider_with_selected_profile(self):
        provider = ThreadingHTTPServer(("127.0.0.1", 0), ProviderHandler)
        provider_thread = threading.Thread(target=provider.serve_forever, daemon=True)
        provider_thread.start()
        try:
            self.server.config.update({
                "model_chat_url": f"http://127.0.0.1:{provider.server_port}/v1/chat/completions",
                "model_api_key": "provider-secret",
                "model_name": "integration-test-model",
            })
            for profile in ("alex", "aria", "astyria", "aurelius"):
                with self.subTest(profile=profile):
                    status, data = self.request_json("POST", "/v1/chat", {
                        "profile": profile,
                        "message": "Review this test.",
                    })
                    self.assertEqual(status, 200)
                    self.assertEqual(data["content"], "Real test-provider response.")
                    self.assertEqual(data["profile"], profile)
                    self.assertEqual(provider.authorization, "Bearer " + self.server.config["model_api_key"])
                    self.assertEqual(provider.body["model"], "integration-test-model")
                    self.assertEqual(provider.body["messages"], [
                        {"role": "user", "content": "Review this test."},
                    ])
                    self.assertNotIn("system", [m["role"] for m in provider.body["messages"]])
        finally:
            provider.shutdown()
            provider.server_close()
            provider_thread.join(timeout=2)

    def test_mobile_gui_chat_returns_real_provider_reply_in_sse_frames(self):
        provider = ThreadingHTTPServer(("127.0.0.1", 0), ProviderHandler)
        worker = threading.Thread(target=provider.serve_forever, daemon=True)
        worker.start()
        try:
            self.server.config.update({
                "model_chat_url": f"http://127.0.0.1:{provider.server_port}/v1/chat/completions",
                "model_api_key": "fixture-provider-key",
                "model_name": "fixture-model",
            })
            for profile in ("alex", "aria", "astyria", "aurelius"):
                with self.subTest(profile=profile):
                    self.connection.request("POST", "/api/v1/chat/stream",
                                            body=json.dumps({"agent": profile, "message": "Write tests"}),
                                            headers={"Authorization": "Bearer " + TOKEN,
                                                     "Content-Type": "application/json"})
                    reply = self.connection.getresponse()
                    events = reply.read().decode("utf-8")
                    self.assertEqual(reply.status, 200)
                    self.assertTrue(reply.getheader("Content-Type").startswith("text/event-stream"))
                    self.assertEqual(reply.getheader("Cache-Control"), "no-store")
                    self.assertEqual(reply.getheader("X-Content-Type-Options"), "nosniff")
                    self.assertIn('event: delta\n', events)
                    self.assertIn('"text": "Real test-provider response."', events)
                    self.assertIn('event: done\n', events)
                    frames = [frame for frame in events.split("\n\n") if frame]
                    self.assertEqual([frame.split("\n", 1)[0] for frame in frames],
                                     ["event: meta", "event: delta", "event: done"])
                    for frame in frames:
                        lines = frame.split("\n")
                        self.assertEqual(len(lines), 2)
                        self.assertTrue(lines[1].startswith("data: "))
                        self.assertIsInstance(json.loads(lines[1][6:]), dict)
                    self.assertIn('"session_persisted": false', events)
                    self.assertIn('"agent": "' + profile + '"', events)
                    self.assertEqual(provider.body["messages"][0]["content"], "Write tests")
                    self.assertEqual(provider.authorization, "Bearer fixture-provider-key")
                    self.assertEqual(provider.body["model"], "fixture-model")
                    self.assertEqual(provider.body["messages"], [
                        {"role": "user", "content": "Write tests"},
                    ])
        finally:
            provider.shutdown()
            provider.server_close()
            worker.join(timeout=3)

    def test_real_vision_payload_to_provider_and_invalid_input_rejected(self):
        provider = ThreadingHTTPServer(("127.0.0.1", 0), ProviderHandler)
        worker = threading.Thread(target=provider.serve_forever, daemon=True)
        worker.start()
        try:
            self.server.config.update({
                "model_chat_url": f"http://127.0.0.1:{provider.server_port}/v1/chat/completions",
                "model_api_key": "fixture-provider-key",
                "model_name": "fixture-text-model",
                "vision_model_name": "llama3.2-vision:11b",
            })
            photo = base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"test-image-bytes").decode()
            status, result = self.request_json("POST", "/v1/vision", {
                "profile": "aria", "message": "Describe this image.",
                "image": {"mime_type": "image/png", "base64": photo},
            })
            self.assertEqual(status, 200)
            self.assertEqual(result["model"], "llama3.2-vision:11b")
            self.assertEqual(provider.body["model"], "llama3.2-vision:11b")
            self.assertEqual(provider.authorization, "Bearer fixture-provider-key")
            self.assertEqual([message["role"] for message in provider.body["messages"]], ["user"])
            segments = provider.body["messages"][0]["content"]
            self.assertEqual(segments[0]["text"], "Describe this image.")
            self.assertTrue(segments[1]["image_url"]["url"].startswith("data:image/png;base64,"))
            status, data = self.request_json("POST", "/v1/vision", {
                "profile": "aria", "message": "Check",
                "image": {"mime_type": "image/png", "base64": "%%bad-base64"},
            })
            self.assertEqual(status, 400)
            self.assertEqual(data["error"]["code"], "invalid_image")
            status, _ = self.request_json("POST", "/v1/vision", {
                "profile": "aria", "message": "Check",
                "image": {"mime_type": "image/gif", "base64": photo},
            })
            self.assertEqual(status, 400)
        finally:
            provider.shutdown()
            provider.server_close()
            worker.join(timeout=3)

    def test_keyless_local_vision_uses_model_without_provider_authorization(self):
        provider = ThreadingHTTPServer(("127.0.0.1", 0), ProviderHandler)
        worker = threading.Thread(target=provider.serve_forever, daemon=True)
        worker.start()
        try:
            self.server.config.update({
                "model_chat_url": f"http://127.0.0.1:{provider.server_port}/v1/chat/completions",
                "model_api_key": "",
                "model_name": "local-text",
                "vision_model_name": "llama3.2-vision:11b",
            })
            photo = base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"test-image-bytes").decode()
            status, result = self.request_json("POST", "/v1/vision", {
                "profile": "aria",
                "message": "Describe the picture.",
                "image": {"mime_type": "image/png", "base64": photo},
            })
            self.assertEqual(status, 200)
            self.assertEqual(result["model"], "llama3.2-vision:11b")
            self.assertEqual(provider.authorization, None)
            self.assertEqual(provider.body["model"], "llama3.2-vision:11b")
            self.assertEqual(provider.body["messages"][0]["content"][0]["text"], "Describe the picture.")
        finally:
            provider.shutdown()
            provider.server_close()
            worker.join(timeout=3)

    def test_provider_url_rejects_untrusted_schemes_and_non_loopback_http(self):
        unsafe_endpoints = (
            "http://example.com/v1/chat/completions",
            "http://192.168.1.10/v1/chat/completions",
            "http://10.0.0.1/v1/chat/completions",
            "http://127.0.0.2:11434/v1/chat/completions",
            "ftp://localhost/v1/chat/completions",
            "file:///v1/chat/completions",
        )
        for endpoint in unsafe_endpoints:
            with self.subTest(endpoint=endpoint), self.assertRaises(ValueError):
                validate_provider_url(endpoint)

    def test_loopback_ollama_endpoint_works_without_internal_network_opt_in(self):
        from unittest.mock import patch

        endpoints = (
            "http://127.0.0.1:11434/v1/chat/completions",
            "http://localhost:11434/v1/chat/completions",
            "http://[::1]:11434/v1/chat/completions",
        )
        with patch.dict(os.environ, {"DEBIAN13_ALLOW_INTERNAL_OLLAMA": "0"}):
            for endpoint in endpoints:
                with self.subTest(endpoint=endpoint):
                    self.assertEqual(validate_provider_url(endpoint), endpoint)

    def test_loopback_ollama_url_rejects_embedded_credentials_and_query(self):
        from unittest.mock import patch

        unsafe_endpoints = (
            "http://user:password@127.0.0.1:11434/v1/chat/completions",
            "http://localhost:11434/v1/chat/completions?api_key=secret",
            "http://[::1]:11434/v1/chat/completions#fragment",
        )
        with patch.dict(os.environ, {"DEBIAN13_ALLOW_INTERNAL_OLLAMA": "0"}):
            for endpoint in unsafe_endpoints:
                with self.subTest(endpoint=endpoint), self.assertRaises(ValueError):
                    validate_provider_url(endpoint)

    def test_internal_ollama_url_requires_explicit_opt_in_and_exact_port(self):
        from unittest.mock import patch

        endpoint = "http://ollama:11434/v1/chat/completions"
        with patch.dict(os.environ, {"DEBIAN13_ALLOW_INTERNAL_OLLAMA": "0"}):
            with self.assertRaises(ValueError):
                validate_provider_url(endpoint)
        with patch.dict(os.environ, {"DEBIAN13_ALLOW_INTERNAL_OLLAMA": "1"}):
            self.assertEqual(validate_provider_url(endpoint), endpoint)
            for unsafe in (
                "http://ollama:11435/v1/chat/completions",
                "http://ollama:11434/v1/chat/completions?token=secret",
                "http://user:password@ollama:11434/v1/chat/completions",
            ):
                with self.subTest(url=unsafe), self.assertRaises(ValueError):
                    validate_provider_url(unsafe)

    def test_rate_limit_returns_429(self):
        self.server.config["rate_limit"] = 2
        self.assertEqual(self.request_json("GET", "/v1/profiles")[0], 200)
        self.assertEqual(self.request_json("GET", "/v1/profiles")[0], 200)
        status, _ = self.request_json("GET", "/v1/profiles")
        self.assertEqual(status, 429)
        self.assertTrue(self.last_retry_after.isdigit())


if __name__ == "__main__":
    unittest.main()
