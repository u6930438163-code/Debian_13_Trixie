"""Debian 13 <-> AI_Studio_BastillaOS protocol v1 (the owner's real agents).

The fake below mirrors src/kernel/debian13OriginalTaskService.mjs and the
/api/agent-tasks/* routes of BastillaOS server.ts (same fields and checks).
"""
import http.client
import json
import os
import sys
import tempfile
import threading
import time
import unittest
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import original_link
from server import Debian13Server

TOKEN = "test-token-with-at-least-thirty-two-characters"
SOURCE_TOKEN = "bastilla-source-token-with-at-least-32-chars"
TARGET = "u6930438163-code/Debian_13_Trixie"


class FakeBastilla(BaseHTTPRequestHandler):
    def _send(self, status, data):
        body = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _authorized(self):
        if self.headers.get("Authorization") != "Bearer " + SOURCE_TOKEN:
            self._send(401, {"error": "unauthorized"})
            return False
        return True

    def do_GET(self):
        if not self._authorized():
            return
        state = self.server.state
        if self.path == "/api/agent-tasks/capabilities":
            self._send(200, {"sourceRepository": "u6930438163-code/AI_Studio_BastillaOS",
                             "protocolVersion": 1, "agents": ["Alex", "Aria", "Astyria", "Aurelius"],
                             "agent": "Alex", "capabilities": state["caps"], "mode": "model_review_only"})
        elif self.path == "/api/agent-tasks/workers":
            self._send(200, {"enabled": True, "providerConfigured": True, "agents": [
                {"name": n, "state": "idle", "workerVerified": False, "completed": 0, "queued": 0}
                for n in ("Alex", "Aria", "Astyria", "Aurelius")]})
        elif self.path.startswith("/api/agent-tasks/jobs/"):
            job = state["jobs"].get(self.path.rsplit("/", 1)[1])
            if job is None:
                return self._send(404, {"error": "job_not_found"})
            job["polls"] += 1
            if job["polls"] >= 2:  # answered by the "model" on the second poll
                job.update(status="failed", errorCode="model_request_failed") if state["fail"] else \
                    job.update(status="completed", provider="gemini", model="gemini-3.8-flash",
                               result=f"{job['agent']} odpowiada: {job['text']}")
            self._send(200, {k: v for k, v in job.items() if k != "polls"})
        elif self.path == "/api/agent-tasks":
            self._send(200, {"success": True, "tasks": []})
        else:
            self._send(404, {"error": "not_found"})

    def do_POST(self):
        if not self._authorized():
            return
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        state = self.server.state
        state["posts"].append((self.path, body, self.headers.get("Idempotency-Key")))
        job_id = str(uuid.uuid4())
        if self.path == "/api/agent-tasks/messages":
            assert body["mode"] == "review_only" and body["targetRepository"] == TARGET
            assert body["autoMerge"] is False and body["purchaseAuthorized"] is False
            state["jobs"][job_id] = {"id": job_id, "agent": body["agent"], "text": body["message"],
                                     "status": "queued", "polls": 0}
            self._send(202, {"accepted": True, "agent": body["agent"], "messageId": job_id, "status": "queued"})
        elif self.path == "/api/agent-tasks/submit":
            assert body["capability"] == "debian13_review_task_v1" and body["mode"] == "pull_request_proposal"
            assert body["task"]["targetRepository"] == TARGET
            state["jobs"][job_id] = {"id": job_id, "agent": body["agent"], "text": body["task"]["description"],
                                     "status": "queued", "polls": 0}
            self._send(202, {"accepted": True, "agent": body["agent"], "capability": body["capability"],
                             "taskId": job_id, "status": "queued"})
        else:
            self._send(404, {"error": "not_found"})

    def log_message(self, *_):
        pass


class OriginalLinkTests(unittest.TestCase):
    def setUp(self):
        self.source = ThreadingHTTPServer(("127.0.0.1", 0), FakeBastilla)
        self.source.state = {"caps": ["debian13_review_task_v1", "debian13_agent_message_v1"],
                             "jobs": {}, "posts": [], "fail": False}
        threading.Thread(target=self.source.serve_forever, daemon=True).start()
        self.env = mock.patch.dict(os.environ, {
            "ALEX_EXISTING_API_URL": f"http://127.0.0.1:{self.source.server_port}/api",
            "ALEX_EXISTING_API_TOKEN": SOURCE_TOKEN,
        })
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.source.shutdown()
        self.source.server_close()

    def test_capabilities_list_all_four_real_agents(self):
        caps = original_link.capabilities()
        self.assertEqual((caps["chat"], caps["tasks"]), (True, True))
        self.assertEqual(sorted(caps["agents"]), ["alex", "aria", "astyria", "aurelius"])

    def test_message_reply_comes_only_from_the_source_job(self):
        job = original_link.send_message("aria", "Cześć")
        reply, model = original_link.wait_for_reply(job, 5, poll_seconds=0.01)
        self.assertEqual(reply, "Aria odpowiada: Cześć")
        self.assertEqual(model, "gemini/gemini-3.8-flash")
        path, body, _ = self.source.state["posts"][0]
        self.assertEqual((path, body["agent"], body["message"]), ("/api/agent-tasks/messages", "Aria", "Cześć"))

    def test_failed_source_job_is_reported_not_faked(self):
        self.source.state["fail"] = True
        job = original_link.send_message("alex", "Hej")
        with self.assertRaises(original_link.OriginalLinkError) as ctx:
            original_link.wait_for_reply(job, 5, poll_seconds=0.01)
        self.assertEqual(ctx.exception.code, "model_request_failed")

    def test_wrong_token_is_rejected(self):
        with mock.patch.dict(os.environ, {"ALEX_EXISTING_API_TOKEN": "x" * 40}):
            with self.assertRaises(original_link.OriginalLinkError) as ctx:
                original_link.capabilities()
        self.assertEqual(ctx.exception.code, "source_rejected_token")

    def test_unconfigured_link_is_inactive(self):
        with mock.patch.dict(os.environ, {"ALEX_EXISTING_API_TOKEN": ""}):
            self.assertFalse(original_link.capabilities()["configured"])


class ServerUsesRealAgentsTests(OriginalLinkTests):
    def setUp(self):
        super().setUp()
        self.temp = tempfile.TemporaryDirectory()
        workspace = Path(self.temp.name) / "workspace"
        workspace.mkdir()
        # No local model at all: every reply must come from BastillaOS.
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

    def test_chat_is_answered_by_the_real_agent(self):
        status, text = self.request("POST", "/api/v1/chat/stream", {"agent": "astyria", "message": "Status?"})
        self.assertEqual(status, 200)
        frames = {}
        for frame in filter(None, text.split("\n\n")):
            name, data = frame.split("\n", 1)
            frames[name.removeprefix("event: ")] = json.loads(data.removeprefix("data: "))
        self.assertEqual(frames["delta"]["text"], "Astyria odpowiada: Status?")
        self.assertEqual((frames["done"]["source"], frames["done"]["model"]),
                         ("original_agent", "gemini/gemini-3.8-flash"))
        status, agents = self.request("GET", "/api/v1/agents")
        self.assertTrue(all(a["connection"] == "original_bastillaos" for a in json.loads(agents)["agents"]))
        status, report = self.request("GET", "/api/v1/original-agents")
        report = json.loads(report)
        self.assertTrue(all(a["connected"] and a["chatLinked"] for a in report["agents"]))

    def test_work_task_is_done_by_the_real_agent(self):
        status, created = self.request("POST", "/api/v1/tasks", {
            "agent": "aurelius", "kind": "audit", "title": "Audyt", "description": "Sprawdź spójność."})
        self.assertEqual(status, 201)
        task_id = json.loads(created)["task"]["id"]
        deadline = time.time() + 15
        task = {}
        while time.time() < deadline:
            task = self.server.work_store.get_task(task_id)
            if task["status"] == "completed":
                break
            time.sleep(0.1)
        self.assertEqual(task["status"], "completed")
        self.assertEqual(task["result"], "Aurelius odpowiada: Sprawdź spójność.")
        self.assertEqual(task["result_model"], "gemini/gemini-3.8-flash")
        submitted = [p for p in self.source.state["posts"] if p[0] == "/api/agent-tasks/submit"]
        self.assertEqual(submitted[0][2], task_id)  # idempotent per Debian task


if __name__ == "__main__":
    unittest.main()
