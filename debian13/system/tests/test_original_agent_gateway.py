"""Security and contract tests for original agents: no models are simulated."""
import json
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

SYSTEM = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SYSTEM))

import original_agent_gateway as gateway
import mcp_server


CAPABILITIES = {
    "sourceRepository": "u6930438163-code/AI_Studio_BastillaOS",
    "protocolVersion": 1,
    "capabilities": ["debian13_agent_message_v1", "debian13_review_task_v1"],
    "agents": ["Alex", "Aria", "Astyria", "Aurelius"],
}


class OriginalAgentGatewayTests(unittest.TestCase):
    def tearDown(self):
        self.assertNotIn("secret", str(self.__dict__))

    def test_four_explicit_tools_and_annotations(self):
        answer = mcp_server.response({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        tools = {item["name"]: item for item in answer["result"]["tools"]}
        self.assertEqual(set(gateway.ORIGINAL_AGENTS), {"Alex", "Aria", "Astyria", "Aurelius"})
        for name in ("list_agents", "send_message", "create_task", "get_task_status"):
            self.assertIn(name, tools)
        self.assertTrue(tools["list_agents"]["annotations"]["readOnlyHint"])
        self.assertTrue(tools["get_task_status"]["annotations"]["readOnlyHint"])
        self.assertFalse(tools["send_message"]["annotations"]["readOnlyHint"])
        self.assertFalse(tools["create_task"]["annotations"]["readOnlyHint"])
        self.assertEqual(tools["create_task"]["inputSchema"]["additionalProperties"], False)

    def test_original_source_registry_used_without_claiming_workers(self):
        tasks = [{"id": "abc-1", "agent": "Alex", "status": "running",
                  "title": "Existing source task"},
                 {"id": "abc-2", "agent": "Aria", "status": "queued"}]
        def source(method, path, **kwargs):
            if path == "/agent-tasks/workers" or path.startswith("/agent-tasks/jobs/"):
                raise gateway.GatewayError("source_capability_not_implemented")
            if path == "/agent-tasks":
                return {"success": True, "tasks": tasks}
            raise AssertionError(path)
        with patch.object(gateway, "_request", side_effect=source):
            data = gateway.list_agents()
            alex = next(x for x in data["agents"] if x["name"] == "Alex")
            self.assertEqual(alex["tasksInRegistry"], 1)
            self.assertEqual(alex["runningEntriesInRegistry"], 1)
            self.assertFalse(alex["workerVerified"])
            self.assertFalse(alex["connected"])
            self.assertFalse(data["originalAgentExecutionVerified"])
            self.assertFalse(data["workerStatusEndpointReachable"])
            item = gateway.get_task_status("Alex", "abc-1")
            self.assertEqual(item["status"], "running")
            self.assertFalse(item["workerExecutionVerified"])
            self.assertEqual(item["sourceRepository"], gateway.SOURCE_REPO)

    def test_authenticated_source_worker_status_is_not_fabricated(self):
        state = {
            "sourceRepository": gateway.SOURCE_REPO,
            "protocolVersion": gateway.PROTOCOL_VERSION,
            "enabled": True, "providerConfigured": True,
            "agents": [
                {"name": "Alex", "state": "running", "workerVerified": True,
                 "activeTaskId": "some-task-id", "heartbeatAt": "2026-11-02T20:00:00Z"},
                {"name": "Aria", "state": "idle", "workerVerified": False},
                {"name": "Astyria", "state": "idle", "workerVerified": False},
                {"name": "Aurelius", "state": "disabled", "workerVerified": False},
            ],
        }
        with patch.object(gateway, "_request", return_value=state) as remote:
            data = gateway.list_agents()
            self.assertTrue(data["workerStatusEndpointReachable"])
            self.assertTrue(data["originalAgentExecutionVerified"])
            self.assertEqual([x["name"] for x in data["agents"]],
                             ["Alex", "Aria", "Astyria", "Aurelius"])
            self.assertEqual(data["agents"][0]["state"], "running")
            self.assertTrue(data["agents"][0]["workerVerified"])
            self.assertFalse(data["agents"][1]["workerVerified"])
            remote.assert_called_once_with("GET", "/agent-tasks/workers")

    def test_completed_source_job_requires_provider_response_before_verified(self):
        result = {
            "id": "task-1", "agent": "Alex", "status": "completed",
            "workerExecutionVerified": True, "provider": "gemini",
            "result": "Real saved code review", "pullRequestVerified": False,
        }
        with patch.object(gateway, "_request", return_value=result):
            outcome = gateway.get_task_status("Alex", "task-1")
        self.assertTrue(outcome["workerExecutionVerified"])
        self.assertFalse(outcome["pullRequestVerified"])
        self.assertEqual(outcome["result"], "Real saved code review")
        with patch.object(gateway, "_request", return_value={**result, "result": ""}):
            outcome = gateway.get_task_status("Alex", "task-1")
        self.assertFalse(outcome["workerExecutionVerified"])

    def test_read_rejects_unknown_agent_missing_task_and_untrusted_ids(self):
        with self.assertRaisesRegex(gateway.GatewayError, "unknown_original_agent"):
            gateway.get_task_status("RandomAgent", "test")
        with self.assertRaisesRegex(gateway.GatewayError, "invalid_task_id"):
            gateway.get_task_status("Alex", "../../etc/passwd")
        def source(method, path, **kwargs):
            if path.startswith("/agent-tasks/jobs/"):
                raise gateway.GatewayError("source_capability_not_implemented")
            return {"success": True, "tasks": []}
        with patch.object(gateway, "_request", side_effect=source):
            with self.assertRaisesRegex(gateway.GatewayError, "task_not_in_original_registry"):
                gateway.get_task_status("Alex", "non-existent")

    def test_write_disabled_by_default_before_remote_call(self):
        with patch.dict(os.environ, {"ORIGINAL_AGENTS_ALLOW_WRITES": "0"}), patch.object(
            gateway, "_request", side_effect=AssertionError("remote must not be called")):
            for call in [
                lambda: gateway.send_message("Aria", "Please review the UI"),
                lambda: gateway.create_task("Alex", "Review", "Propose a PR"),
            ]:
                with self.assertRaisesRegex(gateway.GatewayError, "agent_writes_disabled"):
                    call()

    def test_missing_capability_fails_closed(self):
        with patch.dict(os.environ, {"ORIGINAL_AGENTS_ALLOW_WRITES": "1"}), patch.object(
            gateway, "_request", return_value={
                **CAPABILITIES, "capabilities": ["some_other_capability"]
            }) as remote:
            with self.assertRaisesRegex(gateway.GatewayError, "source_capability_not_verified"):
                gateway.send_message("Aurelius", "Audit")
            remote.assert_called_once_with("GET", "/agent-tasks/capabilities")

    def test_source_not_implemented_refuses_writes(self):
        with patch.dict(os.environ, {"ORIGINAL_AGENTS_ALLOW_WRITES": "1"}), patch.object(
            gateway, "_request", side_effect=gateway.GatewayError("source_capability_not_implemented")
        ):
            with self.assertRaisesRegex(gateway.GatewayError, "source_capability_not_implemented"):
                gateway.create_task("Alex", "Fix", "Propose PR")

    def test_valid_capability_gates_message_and_verifies_receipt(self):
        calls = []
        def mock_request(method, path, payload=None, idempotency_key=""):
            calls.append((method, path, payload, idempotency_key))
            if method == "GET":
                return CAPABILITIES
            return {"accepted": True, "agent": "Aria", "messageId": "message-123"}
        with patch.dict(os.environ, {"ORIGINAL_AGENTS_ALLOW_WRITES": "1"}), patch.object(
            gateway, "_request", side_effect=mock_request):
            result = gateway.send_message("Aria", "Please review Debian GUI")
        self.assertTrue(result["acceptedByRemoteAPI"])
        self.assertFalse(result["agentReplyVerified"])
        self.assertEqual(calls[1][1], "/agent-tasks/messages")
        self.assertFalse(calls[1][2]["autoMerge"])
        self.assertFalse(calls[1][2]["purchaseAuthorized"])
        self.assertEqual(calls[1][2]["targetRepository"], gateway.TARGET_REPO)
        self.assertTrue(calls[1][3])

    def test_task_is_pr_proposal_only_and_receipt_verified(self):
        calls = []
        def mock_request(method, path, payload=None, idempotency_key=""):
            calls.append((method, path, payload, idempotency_key))
            if method == "GET":
                return CAPABILITIES
            return {"accepted": True, "agent": "Astyria",
                    "capability": gateway.TASK_CAPABILITY, "taskId": "task-xyz"}
        with patch.dict(os.environ, {"ORIGINAL_AGENTS_ALLOW_WRITES": "1"}), patch.object(
            gateway, "_request", side_effect=mock_request):
            result = gateway.create_task("Astyria", "GUI", "Fix mobile navigation", "bugfix")
        self.assertTrue(result["acceptedByRemoteAPI"])
        self.assertFalse(result["workerExecutionVerified"])
        self.assertEqual(calls[1][1], "/agent-tasks/submit")
        self.assertEqual(calls[1][2]["mode"], "pull_request_proposal")
        self.assertEqual(calls[1][2]["task"]["targetRepository"], gateway.TARGET_REPO)
        self.assertFalse(calls[1][2]["autoMerge"])
        self.assertFalse(calls[1][2]["purchaseAuthorized"])

    def test_invalid_receipts_are_never_reported_as_accepted(self):
        with patch.dict(os.environ, {"ORIGINAL_AGENTS_ALLOW_WRITES": "1"}), patch.object(
            gateway, "_request", side_effect=[CAPABILITIES, {"accepted": True, "agent": "Alex"}]):
            with self.assertRaisesRegex(gateway.GatewayError, "task_receipt_not_verified"):
                gateway.create_task("Alex", "Fix", "Test")

    def test_mcp_handles_absent_capability_and_does_not_claim_success(self):
        with patch.dict(os.environ, {"ORIGINAL_AGENTS_ALLOW_WRITES": "0"}):
            result = mcp_server.response({
                "jsonrpc": "2.0", "id": 7, "method": "tools/call",
                "params": {"name": "send_message",
                           "arguments": {"agent": "Aria", "message": "hello"}},
            })
        self.assertTrue(result["result"]["isError"])
        self.assertEqual(result["result"]["content"][0]["text"], "agent_writes_disabled")
        invalid = mcp_server.response({
            "jsonrpc": "2.0", "id": 8, "method": "tools/call",
            "params": {"name": "create_task",
                       "arguments": {"agent": "Alex", "title": "X",
                                     "description": "Y", "root": True}},
        })
        self.assertEqual(invalid["error"]["code"], -32602)

    def test_source_token_and_address_validated_before_network(self):
        with patch.dict(os.environ, {"ORIGINAL_AGENTS_API_TOKEN": "short"}):
            with self.assertRaisesRegex(gateway.GatewayError, "original_agents_token_not_configured"):
                gateway._source_token()
        with patch.dict(os.environ, {
            "ALEX_EXISTING_API_URL": "http://remote.example/api",
            "ORIGINAL_AGENTS_API_TOKEN": "A" * 40
        }):
            with self.assertRaisesRegex(gateway.GatewayError, "invalid_original_agents_api_url"):
                gateway._request("GET", "/agent-tasks")


if __name__ == "__main__":
    unittest.main()
