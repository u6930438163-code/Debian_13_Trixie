"""Root tasks: explicit approval before any real container-root command."""
import http.client
import json
import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import root_admin
from server import Debian13Server

OPERATOR = "test-operator-token-at-least-32-characters"
APPROVER = "separate-test-root-approval-token-42"


class RootStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.workspace = Path(self.tmp.name)
        self.store = root_admin.AdminStore(self.workspace)

    def tearDown(self):
        self.tmp.cleanup()

    def test_root_proposal_requires_operator_approval_and_is_persistent(self):
        job = self.store.propose("Alex", "package_audit", "Inspect packages before changing Debian.")
        self.assertEqual(job["status"], "pending_approval")
        self.assertFalse(job["executionStarted"])
        self.assertIsNone(self.store.claim())
        self.assertEqual(self.store.list()["operations"][0]["status"], "pending_approval")
        with self.assertRaisesRegex(root_admin.AdminError, "explicit_root_confirmation_required"):
            self.store.approve(job["id"], "yes")
        self.store.approve(job["id"], "ZATWIERDZAM ROOT W KONTENERZE")
        other = root_admin.AdminStore(self.workspace)
        claimed = other.claim()
        self.assertEqual(claimed, {"id": job["id"], "command": "package_audit"})
        self.assertIsNone(self.store.claim())
        other.finish(job["id"], True, 0, "No package issues.")
        self.assertEqual(self.store.list()["operations"][0]["status"], "completed")
        self.assertEqual(self.store.list()["operations"][0]["output"], "No package issues.")
        self.assertEqual(self.store.db_path.stat().st_mode & 0o777, 0o600)

    def test_root_rejects_arbitrary_shell_commands_paths_and_fake_agents(self):
        for cmd in ("sh -c whoami", "rm -rf /", "apt install attacker", "../../etc/passwd"):
            with self.assertRaises(root_admin.AdminError):
                self.store.propose("Alex", cmd, "Please execute")
        with self.assertRaisesRegex(root_admin.AdminError, "unknown_original_agent"):
            self.store.propose("FakeAlex", "identity", "Please execute")
        with self.assertRaisesRegex(root_admin.AdminError, "invalid_reason"):
            self.store.propose("Aria", "identity", "")

    def test_expired_proposal_cannot_execute(self):
        job = self.store.propose("Aria", "identity", "Inspect identity")
        with patch.object(root_admin.time, "time",
                          return_value=root_admin.time.time() + root_admin.APPROVAL_TTL + 30):
            with self.assertRaisesRegex(root_admin.AdminError, "approval_expired"):
                self.store.approve(job["id"], "ZATWIERDZAM ROOT W KONTENERZE")
        self.assertIsNone(self.store.claim())

    def test_nonroot_process_does_not_fake_execution(self):
        with patch.object(root_admin.os, "geteuid", return_value=10001):
            success, code, message = root_admin.execute_allowlisted("identity", Path("/workspace"))
        self.assertFalse(success)
        self.assertEqual(message, "root_runner_not_privileged")

    def test_exact_admin_command_is_invoked_without_shell_or_inherited_secrets(self):
        class Response:
            returncode = 0
            stdout = "uid=0(root)"
            stderr = ""
        with patch.object(root_admin.os, "geteuid", return_value=0), patch.object(
                root_admin.subprocess, "run", return_value=Response()) as run:
            done, code, output = root_admin.execute_allowlisted("identity", Path("/workspace"))
        self.assertTrue(done)
        self.assertEqual(code, 0)
        self.assertIn("uid=0", output)
        self.assertEqual(run.call_args.args[0], ("id",))
        self.assertFalse(run.call_args.kwargs["shell"])
        self.assertNotIn("OPENAI_API_KEY", run.call_args.kwargs["env"])


class RootApiTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.server = Debian13Server(("127.0.0.1", 0), {
            "api_token": OPERATOR, "root_actions_enabled": True,
            "root_approval_token": APPROVER,
            "model_chat_url": "", "model_api_key": "", "model_name": "",
            "workspace": self.tmp.name,
        })
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)
        self.tmp.cleanup()

    def request(self, path, method="GET", body=None, operator=OPERATOR, root=None):
        connection = http.client.HTTPConnection(*self.server.server_address, timeout=5)
        headers = {"Accept": "application/json"}
        if operator:
            headers["Authorization"] = "Bearer " + operator
        if root:
            headers["X-Root-Approval-Token"] = root
        payload = json.dumps(body).encode() if body is not None else None
        if payload is not None:
            headers["Content-Type"] = "application/json"
        try:
            connection.request(method, path, payload, headers)
            resp = connection.getresponse()
            data = json.loads(resp.read())
            return resp.status, data
        finally:
            connection.close()

    def test_admin_commands_and_history_require_bearer(self):
        self.assertEqual(self.request("/api/v1/admin/commands", operator=None)[0], 401)
        status, data = self.request("/api/v1/admin/commands")
        self.assertEqual(status, 200)
        self.assertTrue(data["enabled"])
        self.assertFalse(data["rootRunnerVerified"])
        self.assertIn("repair_workspace_owner", {x["id"] for x in data["commands"]})
        self.assertNotIn(APPROVER, json.dumps(data))

    def test_propose_then_reject_wrong_approval_then_accept(self):
        path = "/api/v1/admin/proposals"
        args = {"agent": "Astyria", "commandId": "package_audit", "reason": "Diagnostyka"}
        status, data = self.request(path, "POST", args)
        self.assertEqual(status, 201)
        operation = data["id"]
        url = f"/api/v1/admin/operations/{operation}/approve"
        approval = {"confirmation": "ZATWIERDZAM ROOT W KONTENERZE"}
        self.assertEqual(self.request(url, "POST", approval)[0], 403)
        self.assertEqual(self.request(url, "POST", approval, root="wrong")[0], 403)
        self.assertEqual(self.request(url, "POST", approval, root=APPROVER)[0], 202)
        status, data = self.request("/api/v1/admin/operations")
        self.assertEqual(status, 200)
        self.assertEqual(data["operations"][0]["status"], "approved")
        self.assertNotIn(APPROVER, json.dumps(data))
        # Proposal approval alone is not evidence of root execution.
        self.assertIsNone(data["operations"][0]["startedAt"])

    def test_shell_strings_fail_closed(self):
        status, data = self.request("/api/v1/admin/proposals", "POST", {
            "agent": "Alex", "commandId": "sudo sh -c 'whoami'", "reason": "Need root"})
        self.assertEqual(status, 403)
        self.assertEqual(data["error"]["code"], "root_command_not_allowlisted")

    def test_root_actions_disabled_refuse_execution_approval(self):
        self.server.config["root_actions_enabled"] = False
        status, data = self.request("/api/v1/admin/commands")
        self.assertFalse(data["enabled"])
        proposed = self.request("/api/v1/admin/proposals", "POST", {
            "agent": "Aurelius", "commandId": "identity", "reason": "Check UID"})
        uid = proposed[1]["id"]
        status, data = self.request(f"/api/v1/admin/operations/{uid}/approve",
                                    "POST", {"confirmation": "ZATWIERDZAM ROOT W KONTENERZE"}, root=APPROVER)
        self.assertEqual(status, 503)
        self.assertEqual(data["error"]["code"], "root_actions_disabled")


if __name__ == "__main__":
    unittest.main()
