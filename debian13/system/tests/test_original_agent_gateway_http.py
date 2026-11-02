"""Exercise real local HTTP authorization for the new original-agent MCP tools."""
import http.client
import json
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

SYSTEM = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SYSTEM))
from server import Debian13Server

TOKEN = "http-gateway-operator-token-at-least-32-characters"


class OriginalAgentsHttpTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.api = Debian13Server(("127.0.0.1", 0), {
            "api_token": TOKEN, "model_chat_url": "",
            "model_api_key": "", "model_name": "",
            "workspace": self.temp.name,
        })
        self.thread = threading.Thread(target=self.api.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.api.shutdown()
        self.api.server_close()
        self.thread.join(timeout=3)
        self.temp.cleanup()

    def post(self, body, token=TOKEN, origin=None):
        connection = http.client.HTTPConnection(*self.api.server_address, timeout=5)
        try:
            headers = {
                "Content-Type": "application/json",
                "Accept": "application/json, text/event-stream",
            }
            if token:
                headers["Authorization"] = "Bearer " + token
            if origin:
                headers["Origin"] = origin
            connection.request("POST", "/mcp", json.dumps(body), headers)
            response = connection.getresponse()
            raw = response.read()
            return response.status, json.loads(raw) if raw else {}
        finally:
            connection.close()

    def test_auth_before_tools_and_reject_browser_origin(self):
        request = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
        self.assertEqual(self.post(request, token=None)[0], 401)
        self.assertEqual(self.post(request, token=TOKEN, origin="https://evil.example")[0], 403)
        status, response = self.post(request)
        self.assertEqual(status, 200)
        actual = {tool["name"] for tool in response["result"]["tools"]}
        self.assertTrue({"list_agents", "send_message", "create_task", "get_task_status"} <= actual)

    def test_authenticated_list_agents_uses_real_source_registry_adapter(self):
        response = {
            "sourceRepository": "u6930438163-code/AI_Studio_BastillaOS",
            "protocolVersion": 1, "enabled": True, "providerConfigured": True,
            "agents": [
                {"name": "Alex", "state": "idle", "workerVerified": False},
                {"name": "Aria", "state": "idle", "workerVerified": False},
                {"name": "Astyria", "state": "idle", "workerVerified": False},
                {"name": "Aurelius", "state": "idle", "workerVerified": False},
            ],
        }
        with patch("original_agent_gateway._request", return_value=response) as mocked:
            status, reply = self.post({
                "jsonrpc": "2.0", "id": 3, "method": "tools/call",
                "params": {"name": "list_agents", "arguments": {}},
            })
        self.assertEqual(status, 200)
        self.assertFalse(reply["result"]["isError"])
        info = json.loads(reply["result"]["content"][0]["text"])
        self.assertTrue(info["registryReachable"])
        self.assertFalse(info["originalAgentExecutionVerified"])
        self.assertEqual(len(info["agents"]), 4)
        mocked.assert_called_once_with("GET", "/agent-tasks/workers")

    def test_write_tools_never_bypass_operator_config(self):
        request = {
            "jsonrpc": "2.0", "id": 4, "method": "tools/call",
            "params": {"name": "create_task", "arguments": {
                "agent": "Alex", "title": "Audit", "description": "Review Debian 13",
            }},
        }
        with patch("original_agent_gateway._request", side_effect=AssertionError("must not call remote")):
            status, reply = self.post(request)
        self.assertEqual(status, 200)
        self.assertTrue(reply["result"]["isError"])
        self.assertEqual(reply["result"]["content"][0]["text"], "agent_writes_disabled")


if __name__ == "__main__":
    unittest.main()
