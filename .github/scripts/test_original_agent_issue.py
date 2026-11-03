"""Offline contract checks; no connection or secrets required."""
import importlib.util
from pathlib import Path
import unittest

script = Path(__file__).with_name("original_agent_issue.py")
spec = importlib.util.spec_from_file_location("original_agent_issue", script)
bridge = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bridge)


class OriginalIssueBridgeTests(unittest.TestCase):
    def test_owner_command_and_selection(self):
        self.assertEqual(bridge.choose_agent("issue_comment", "/agent Aria"), "Aria")
        self.assertEqual(bridge.choose_agent("workflow_dispatch", "Alex"), "Alex")
        for bad in ("/agent unknown", "/agent Aria; rm -rf /", "/agent Aria\n/agent Aurelius"):
            with self.subTest(bad=bad), self.assertRaises(bridge.Blocked):
                bridge.choose_agent("issue_comment", bad)

    def test_endpoint_must_be_https_origin(self):
        self.assertEqual(bridge.origin_of("https://source.example/api"), "https://source.example")
        for bad in ("http://source.example", "https://127.0.0.1", "https://localhost",
                    "https://source.example/redirect", "https://user:pass@source.example"):
            with self.subTest(bad=bad), self.assertRaises(bridge.Blocked):
                bridge.origin_of(bad)

    def test_contract_never_authorizes_merge_or_purchase(self):
        issue = {"number": 22, "title": "Implement Issues", "body": "Work on this",
                 "html_url": "https://github.com/u6930438163-code/Debian_13_Trixie/issues/22"}
        payload = bridge.payload("Aurelius", issue)
        self.assertEqual(payload["capability"], "debian13_review_task_v1")
        self.assertEqual(payload["task"]["targetRepository"], bridge.TARGET)
        self.assertEqual(payload["mode"], "pull_request_proposal")
        self.assertIs(payload["autoMerge"], False)
        self.assertIs(payload["purchaseAuthorized"], False)


if __name__ == "__main__":
    unittest.main()
