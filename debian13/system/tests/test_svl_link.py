"""Debian 13 <-> SVL_System chat link (mmitek515-netizen/SVL_System).

FakeSvl mirrors the /api/triad/message decision order of SVL_System
server.ts: approval commands, then /exec or "!" (bash), then kernel words,
then the Best-Fit router, then the model chain (optionally without_nlp).
"""
import http.client
import json
import os
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import svl_link
from server import Debian13Server

TOKEN = "test-token-with-at-least-thirty-two-characters"
KERNEL_WORDS = ['report', 'status', 'state', 'root', 'sudo', 'clear_chat', 'clear', 'sync', 'bump', 'set', 'infra']


class FakeSvl(BaseHTTPRequestHandler):
    def _send(self, status, data):
        body = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/api/ai/status":
            chain = ["Google Gemini (gemini-3.8-flash)"] if self.server.mode != "no_model" else []
            return self._send(200, {"provider": "Google Gemini", "chain": chain,
                                    "chatProtocol": "/api/triad/message"})
        self._send(404, {"error": "not_found"})

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        raw, agent = body["message"], body["agent"]
        self.server.received.append(body)
        trimmed = raw.strip()
        # 1) approvals, 2) bash, 3) kernel commands — all anchored at the raw message start.
        if trimmed.startswith(("/propose", "/approve", "/reject", "/proposals")) or \
                trimmed.startswith("/exec ") or trimmed.startswith("!") or \
                trimmed.split()[0].lower() in KERNEL_WORDS:
            self.server.executed.append(trimmed)
            return self._send(200, {"success": True, "executed": True,
                                    "replies": {agent: "[EXEC · ROOT UNLIMITED] wykonano"}})
        forced = trimmed.lower().startswith("@model")
        clean = trimmed[len("@model"):].strip() if forced else trimmed
        if not forced and len(clean.split()) <= 4:  # 4) Best-Fit router template
            return self._send(200, {"success": True, "replies": {agent: "[NATYCHMIAST] szablon"},
                                    "engines": {agent: "best-fit:status"}})
        if self.server.mode == "without_nlp":
            return self._send(200, {"success": True, "replies": {agent: "[API OFFLINE] odpowiedź szablonowa"},
                                    "engines": {agent: "without_nlp"}})
        self.server.model_inputs.append(clean)
        self._send(200, {"success": True, "agent": agent,
                         "replies": {agent: f"{agent} (model) odpowiada na: {clean}"},
                         "engines": {agent: "gemini:gemini-3.8-flash"}, "state": {"store": {}}})

    def log_message(self, *_):
        pass


class SvlLinkTests(unittest.TestCase):
    def setUp(self):
        self.svl = ThreadingHTTPServer(("127.0.0.1", 0), FakeSvl)
        self.svl.mode = "model"
        self.svl.received, self.svl.executed, self.svl.model_inputs = [], [], []
        threading.Thread(target=self.svl.serve_forever, daemon=True).start()
        self.env = mock.patch.dict(os.environ, {"SVL_SYSTEM_URL": f"http://127.0.0.1:{self.svl.server_port}"})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.svl.shutdown()
        self.svl.server_close()

    def test_reply_comes_from_the_model_of_the_chosen_agent(self):
        reply, engine = svl_link.chat("aria", "Opowiedz mi o swojej pracy nad systemem.")
        self.assertEqual(engine, "gemini:gemini-3.8-flash")
        self.assertEqual(self.svl.received[-1]["agent"], "si_1")  # Aria's id in SVL_System
        self.assertEqual(self.svl.model_inputs[-1], "Opowiedz mi o swojej pracy nad systemem.")
        self.assertIn("si_1 (model)", reply)

    def test_commands_are_never_executed_on_the_svl_host(self):
        for dangerous in ["/exec rm -rf /tmp/x", "!whoami", "root", "status", "sudo reboot",
                          "/propose github delete-repo", "/approve 123", "sync"]:
            reply, engine = svl_link.chat("alex", dangerous)
            self.assertEqual(engine, "gemini:gemini-3.8-flash")
            self.assertEqual(self.svl.model_inputs[-1], dangerous)  # reaches the agent as plain text
        self.assertEqual(self.svl.executed, [])

    def test_short_messages_skip_template_answers(self):
        _, engine = svl_link.chat("astyria", "jak stan?")
        self.assertEqual(engine, "gemini:gemini-3.8-flash")

    def test_scripted_without_nlp_answers_are_rejected(self):
        self.svl.mode = "without_nlp"
        with self.assertRaises(svl_link.SvlLinkError) as ctx:
            svl_link.chat("aurelius", "Sprawdź testy systemu proszę.")
        self.assertEqual(ctx.exception.code, "svl_reply_without_model")

    def test_url_must_be_https_or_loopback_origin(self):
        for bad in ["http://svl.example", "https://svl.example/api", "https://u:p@svl.example"]:
            with mock.patch.dict(os.environ, {"SVL_SYSTEM_URL": bad}):
                with self.assertRaises(ValueError):
                    svl_link.base_url()


class ServerSvlChatTests(SvlLinkTests):
    def setUp(self):
        super().setUp()
        self.temp = tempfile.TemporaryDirectory()
        workspace = Path(self.temp.name) / "workspace"
        workspace.mkdir()
        self.server = Debian13Server(("127.0.0.1", 0), {
            "api_token": TOKEN, "model_chat_url": "", "model_api_key": "", "model_name": "",
            "workspace": str(workspace), "agent_worker_enabled": True, "agent_worker_interval": 0.2,
        })
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.connection = http.client.HTTPConnection(*self.server.server_address, timeout=30)

    def tearDown(self):
        self.connection.close()
        self.server.shutdown()
        self.server.server_close()
        self.temp.cleanup()
        super().tearDown()

    def request(self, method, path, body=None):
        headers = {"Authorization": "Bearer " + TOKEN}
        if body is not None:
            headers["Content-Type"] = "application/json"
        self.connection.request(method, path, body=json.dumps(body) if body is not None else None,
                                headers=headers)
        reply = self.connection.getresponse()
        return reply.status, reply.read().decode()

    def frames(self, text):
        out = {}
        for frame in filter(None, text.split("\n\n")):
            name, data = frame.split("\n", 1)
            out[name.removeprefix("event: ")] = json.loads(data.removeprefix("data: "))
        return out

    def test_operator_chat_reaches_the_svl_agent(self):
        status, text = self.request("POST", "/api/v1/chat/stream",
                                    {"agent": "aurelius", "message": "Jak oceniasz testy?"})
        frames = self.frames(text)
        self.assertEqual(status, 200)
        self.assertEqual((frames["done"]["source"], frames["done"]["model"]),
                         ("svl_system", "gemini:gemini-3.8-flash"))
        self.assertIn("Aurelius (model)", frames["delta"]["text"])
        _, agents = self.request("GET", "/api/v1/agents")
        self.assertTrue(all(a["connection"] == "svl_system" for a in json.loads(agents)["agents"]))

    def test_without_model_the_error_is_shown_not_a_template(self):
        self.svl.mode = "without_nlp"
        status, text = self.request("POST", "/api/v1/chat/stream",
                                    {"agent": "alex", "message": "Co nowego w systemie?"})
        frames = self.frames(text)
        self.assertNotIn("delta", frames)
        self.assertEqual(frames["error"]["code"], "svl_reply_without_model")

    def test_work_tasks_are_never_sent_to_svl_system(self):
        status, _ = self.request("POST", "/api/v1/tasks", {
            "agent": "alex", "kind": "audit", "title": "Audyt", "description": "!rm -rf /"})
        self.assertEqual(status, 201)
        import time
        time.sleep(1)
        self.assertEqual(self.svl.received, [])

    def test_svl_core_reconnect_reports_svl_source(self):
        status, body = self.request("POST", "/api/v1/agents/reconnect", {})
        report = json.loads(body)
        self.assertEqual((status, report["Result"]), (200, "connected"))
        self.assertTrue(all("SVL_System" in a["via"] for a in report["Agents"].values()))
        self.assertEqual(report["Sources"]["SVL_System"]["result"], "connected")


if __name__ == "__main__":
    unittest.main()
