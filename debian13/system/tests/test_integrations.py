"""Focused MCP and GPT Actions contract tests (no external model or account)."""
import http.client
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

SYSTEM = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SYSTEM))
from server import Debian13Server
import mcp_server

TOKEN = "integration-tests-token-with-32-characters"


class IntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.api = Debian13Server(("127.0.0.1", 0), {
            "api_token": TOKEN,
            "model_chat_url": "",
            "model_api_key": "",
            "model_name": "",
            "workspace": self.temp.name,
        })
        self.thread = threading.Thread(target=self.api.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.api.shutdown()
        self.api.server_close()
        self.thread.join(timeout=3)
        self.temp.cleanup()

    def test_integration_route_requires_bearer_token(self):
        connection = http.client.HTTPConnection(*self.api.server_address, timeout=4)
        try:
            connection.request("GET", "/v1/integrations")
            unauthorized = connection.getresponse()
            self.assertEqual(unauthorized.status, 401)
            unauthorized.read()
            connection.request("GET", "/v1/integrations", headers={"Authorization": "Bearer " + TOKEN})
            reply = connection.getresponse()
            self.assertEqual(reply.status, 200)
            body = json.load(reply)
            self.assertEqual({item["id"] for item in body["integrations"]},
                             {"codex", "mcp", "gpt_actions"})
            self.assertTrue(all(item["connected"] is False for item in body["integrations"]))
        finally:
            connection.close()

    def test_mcp_stdio_initialize_tools_and_real_authenticated_read(self):
        initialized = mcp_server.response({
            "jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}
        })
        self.assertEqual(initialized["result"]["protocolVersion"], mcp_server.PROTOCOL_VERSION)
        listed = mcp_server.response({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        self.assertEqual({tool["name"] for tool in listed["result"]["tools"]},
                         {"debian13_profiles", "debian13_diagnostics", "debian13_integrations",
                          "alex_existing_status", "alex_existing_tasks", "debian13_work_tasks",
                          "debian13_original_agents", "debian13_queue_work_task",
                          "debian13_work_task_detail", "debian13_cancel_work_task",
                          "debian13_chat_agent"})
        with patch.dict(os.environ, {
            "DEBIAN13_SYSTEM_API_URL": f"http://127.0.0.1:{self.api.server_port}",
            "DEBIAN13_API_TOKEN": TOKEN,
        }):
            called = mcp_server.response({
                "jsonrpc": "2.0", "id": 3, "method": "tools/call",
                "params": {"name": "debian13_profiles", "arguments": {}},
            })
        self.assertFalse(called["result"]["isError"])
        payload = json.loads(called["result"]["content"][0]["text"])
        self.assertEqual(len(payload["profiles"]), 4)
        self.assertTrue(all(p["active"] is False for p in payload["profiles"]))

    def test_stdio_mcp_is_reachable_as_a_separate_process(self):
        commands = [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
            {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
             "params": {"name": "debian13_diagnostics", "arguments": {}}},
        ]
        env = dict(os.environ, DEBIAN13_API_TOKEN=TOKEN,
                   DEBIAN13_SYSTEM_API_URL=f"http://127.0.0.1:{self.api.server_port}")
        process = subprocess.run(
            [sys.executable, str(SYSTEM / "mcp_server.py")],
            input="".join(json.dumps(command) + "\n" for command in commands),
            capture_output=True, text=True, timeout=10, env=env, check=True)
        replies = [json.loads(line) for line in process.stdout.splitlines()]
        self.assertEqual([r["id"] for r in replies], [1, 2, 3])
        self.assertFalse(replies[2]["result"]["isError"])
        report = json.loads(replies[2]["result"]["content"][0]["text"])
        self.assertEqual(report["service"], "debian13-system")
        self.assertNotIn(TOKEN, process.stdout)

    def test_http_mcp_auth_transport_and_readonly_diagnostics(self):
        base_headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        init = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}

        def call(message, headers=None, method="POST"):
            connection = http.client.HTTPConnection(*self.api.server_address, timeout=5)
            try:
                connection.request(method, "/mcp",
                                   body=json.dumps(message) if method == "POST" else None,
                                   headers=headers or base_headers)
                reply = connection.getresponse()
                body = reply.read()
                return reply.status, json.loads(body) if body else None
            finally:
                connection.close()

        self.assertEqual(call(init)[0], 401)
        authed = dict(base_headers, Authorization="Bearer " + TOKEN)
        self.assertEqual(call(init, dict(authed, Origin="https://evil.example"))[0], 403)
        self.assertEqual(call(init, authed, method="GET")[0], 405)
        status, data = call(init, authed)
        self.assertEqual(status, 200)
        self.assertEqual(data["result"]["serverInfo"]["name"], "debian13-work-mcp")
        status, data = call({"jsonrpc": "2.0", "method": "notifications/initialized"}, authed)
        self.assertEqual((status, data), (202, None))

        with patch.dict(os.environ, {
            "DEBIAN13_SYSTEM_API_URL": f"http://127.0.0.1:{self.api.server_port}",
            "DEBIAN13_API_TOKEN": TOKEN,
        }):
            status, reply = call(
                {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                 "params": {"name": "debian13_diagnostics", "arguments": {}}}, authed)
        self.assertEqual(status, 200)
        self.assertFalse(reply["result"]["isError"])
        self.assertEqual(json.loads(reply["result"]["content"][0]["text"])["service"],
                         "debian13-system")
        self.assertNotIn(TOKEN, json.dumps(reply))

    def test_stdio_mcp_enqueues_review_only_task(self):
        task = {"agent": "aria", "kind": "development", "title": "Review GUI",
                "description": "Review Hyperspace layout without executing changes"}
        with patch.dict(os.environ, {
            "DEBIAN13_SYSTEM_API_URL": f"http://127.0.0.1:{self.api.server_port}",
            "DEBIAN13_API_TOKEN": TOKEN,
        }):
            reply = mcp_server.response({
                "jsonrpc": "2.0", "id": 8, "method": "tools/call",
                "params": {"name": "debian13_queue_work_task", "arguments": task},
            })
        self.assertFalse(reply["result"]["isError"])
        receipt = json.loads(reply["result"]["content"][0]["text"])
        self.assertEqual(receipt["task"]["agent"], "aria")
        self.assertEqual(receipt["task"]["status"], "queued")
        self.assertEqual(receipt["task"]["execution"], "not_started")
        self.assertEqual(receipt["task"]["requires_approval"], 1)
        self.assertFalse(receipt["executionStarted"])
        self.assertEqual(len(self.api.work_store.list_tasks()), 1)

    def test_http_mcp_queues_task_only_with_operator_bearer(self):
        task = {"agent": "alex", "kind": "bugfix", "title": "Review CI",
                "description": "Investigate test failures without running commands"}
        request = {"jsonrpc": "2.0", "id": 9, "method": "tools/call",
                   "params": {"name": "debian13_queue_work_task", "arguments": task}}
        headers = {"Content-Type": "application/json",
                   "Accept": "application/json, text/event-stream",
                   "Authorization": "Bearer " + TOKEN}

        def call(query, authorized=True):
            connection = http.client.HTTPConnection(*self.api.server_address, timeout=5)
            try:
                actual_headers = headers if authorized else {
                    key: value for key, value in headers.items() if key != "Authorization"
                }
                connection.request("POST", "/mcp", body=json.dumps(query), headers=actual_headers)
                response = connection.getresponse()
                return response.status, json.loads(response.read())
            finally:
                connection.close()

        self.assertEqual(call(request, authorized=False)[0], 401)
        bad = {"jsonrpc": "2.0", "id": 10, "method": "tools/call",
               "params": {"name": "debian13_queue_work_task",
                          "arguments": {**task, "command": "id"}}}
        status, error = call(bad)
        self.assertEqual(status, 200)
        self.assertEqual(error["error"]["code"], -32602)
        self.assertEqual(len(self.api.work_store.list_tasks()), 0)
        status, response = call(request)
        self.assertEqual(status, 200)
        self.assertFalse(response["result"]["isError"])
        receipt = json.loads(response["result"]["content"][0]["text"])
        self.assertFalse(receipt["executionStarted"])
        self.assertEqual(receipt["task"]["agent"], "alex")
        self.assertEqual(receipt["task"]["status"], "queued")
        self.assertEqual(len(self.api.work_store.list_tasks()), 1)
        self.assertNotIn(TOKEN, json.dumps(response))

    def test_mcp_task_detail_and_cancel_through_authenticated_stdio(self):
        task = self.api.work_store.create_task(
            "development", "aria", "Review CSS", "Review UI without executing")
        with patch.dict(os.environ, {
            "DEBIAN13_SYSTEM_API_URL": f"http://127.0.0.1:{self.api.server_port}",
            "DEBIAN13_API_TOKEN": TOKEN,
        }):
            detail = mcp_server.response({
                "jsonrpc": "2.0", "id": 30, "method": "tools/call",
                "params": {"name": "debian13_work_task_detail",
                           "arguments": {"task_id": task["id"]}},
            })
            invalid = mcp_server.response({
                "jsonrpc": "2.0", "id": 31, "method": "tools/call",
                "params": {"name": "debian13_cancel_work_task",
                           "arguments": {"task_id": "../../../etc/passwd"}},
            })
            canceled = mcp_server.response({
                "jsonrpc": "2.0", "id": 32, "method": "tools/call",
                "params": {"name": "debian13_cancel_work_task",
                           "arguments": {"task_id": task["id"]}},
            })
        self.assertFalse(detail["result"]["isError"])
        self.assertEqual(json.loads(detail["result"]["content"][0]["text"])["task"]["id"], task["id"])
        self.assertEqual(invalid["error"]["code"], -32602)
        self.assertFalse(canceled["result"]["isError"])
        receipt = json.loads(canceled["result"]["content"][0]["text"])
        self.assertEqual(receipt["task"]["status"], "cancelled")
        self.assertFalse(receipt["executionStarted"])
        self.assertEqual(self.api.work_store.get_task(task["id"])["status"], "cancelled")

    def test_mcp_http_chat_requires_bearer_and_real_model_reply(self):
        headers = {"Content-Type": "application/json",
                   "Accept": "application/json, text/event-stream",
                   "Authorization": "Bearer " + TOKEN}
        request = {"jsonrpc": "2.0", "id": 21, "method": "tools/call",
                   "params": {"name": "debian13_chat_agent",
                              "arguments": {"agent": "alex", "message": "Status?"}}}

        def call(body=request, authorized=True):
            conn = http.client.HTTPConnection(*self.api.server_address, timeout=5)
            try:
                h = headers if authorized else {k: v for k, v in headers.items()
                                                if k != "Authorization"}
                conn.request("POST", "/mcp", body=json.dumps(body), headers=h)
                response = conn.getresponse()
                return response.status, json.loads(response.read())
            finally:
                conn.close()

        self.assertEqual(call(authorized=False)[0], 401)
        bad = {"jsonrpc": "2.0", "id": 22, "method": "tools/call",
               "params": {"name": "debian13_chat_agent",
                          "arguments": {"agent": "alex", "message": "x", "command": "id"}}}
        self.assertEqual(call(body=bad)[1]["error"]["code"], -32602)
        with patch("server.complete_chat", return_value=("Real adapter reply", "fixture-model")) as adapter:
            status, result = call()
        self.assertEqual(status, 200)
        self.assertFalse(result["result"]["isError"])
        reply = json.loads(result["result"]["content"][0]["text"])
        self.assertEqual(reply["reply"], "Real adapter reply")
        self.assertEqual(reply["agent"], "alex")
        self.assertEqual(reply["model"], "fixture-model")
        self.assertFalse(reply["originalAgentExecutionVerified"])
        self.assertFalse(reply["sessionPersisted"])
        adapter.assert_called_once_with("alex", "Status?", self.api.config)
        self.assertNotIn(TOKEN, json.dumps(result))

    def test_mcp_http_chat_fails_closed_without_provider(self):
        query = {"jsonrpc": "2.0", "id": 25, "method": "tools/call",
                 "params": {"name": "debian13_chat_agent",
                            "arguments": {"agent": "aria", "message": "Hello"}}}
        headers = {"Content-Type": "application/json",
                   "Accept": "application/json, text/event-stream",
                   "Authorization": "Bearer " + TOKEN}
        conn = http.client.HTTPConnection(*self.api.server_address, timeout=5)
        try:
            conn.request("POST", "/mcp", body=json.dumps(query), headers=headers)
            response = conn.getresponse()
            self.assertEqual(response.status, 200)
            reply = json.loads(response.read())
            self.assertTrue(reply["result"]["isError"])
            self.assertNotIn("Hello, I am Aria", json.dumps(reply))
        finally:
            conn.close()

    def test_mcp_rejects_unknown_and_mutating_tools(self):
        result = mcp_server.response({
            "jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {"name": "run_shell", "arguments": {"command": "id"}},
        })
        self.assertEqual(result["error"]["code"], -32602)

    def test_mcp_rejects_untrusted_http_and_auth_failures_without_echoing_secret(self):
        with patch.dict(os.environ, {"DEBIAN13_SYSTEM_API_URL": "http://evil.example:8080",
                                     "DEBIAN13_API_TOKEN": TOKEN}):
            reply = mcp_server.response({
                "jsonrpc": "2.0", "id": 3, "method": "tools/call",
                "params": {"name": "debian13_integrations", "arguments": {}},
            })
        self.assertTrue(reply["result"]["isError"])
        self.assertNotIn(TOKEN, json.dumps(reply))

    def test_gpt_actions_schema_documents_existing_routes_only(self):
        schema = json.loads((SYSTEM / "gpt-actions.openapi.json").read_text(encoding="utf-8"))
        self.assertEqual(schema["openapi"], "3.0.3")
        for route in ("/v1/profiles", "/v1/diagnostics", "/v1/chat", "/v1/integrations",
                      "/api/v1/alex/capabilities", "/api/v1/original-agents"):
            self.assertIn(route, schema["paths"])
        self.assertEqual(schema["security"], [{"bearerAuth": []}])
        self.assertTrue(schema["paths"]["/v1/chat"]["post"]["x-openai-isConsequential"])


if __name__ == "__main__":
    unittest.main()
