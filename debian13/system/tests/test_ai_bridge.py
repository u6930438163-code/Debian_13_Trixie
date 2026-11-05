"""Live local HTTP bridge checks; provider calls mocked, never bill a model."""
import http.client
import json
import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

SYSTEM = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SYSTEM))
from server import Debian13Server
import ai_bridge

TOKEN = "bridge-integration-test-operator-token-32-chars"


class BridgeHttpTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.api = Debian13Server(("127.0.0.1", 0), {
            "api_token": TOKEN, "model_chat_url": "",
            "model_api_key": "", "model_name": "",
            "workspace": self.temp.name,
        })
        self.thread = threading.Thread(target=self.api.serve_forever, daemon=True)
        self.thread.start()
        ai_bridge.SESSIONS.clear()

    def tearDown(self):
        self.api.shutdown()
        self.api.server_close()
        self.thread.join(timeout=3)
        ai_bridge.SESSIONS.clear()
        self.temp.cleanup()

    def post(self, payload, route="/api/v1/bridge", token=TOKEN):
        connection = http.client.HTTPConnection(*self.api.server_address, timeout=5)
        try:
            headers = {"Content-Type": "application/json"}
            if token is not None:
                headers["Authorization"] = "Bearer " + token
            connection.request("POST", route, body=json.dumps(payload), headers=headers)
            response = connection.getresponse()
            return response.status, json.loads(response.read())
        finally:
            connection.close()

    def test_bridge_requires_operator_bearer(self):
        status, _ = self.post({"message": "test"}, token=None)
        self.assertEqual(status, 401)

    def test_bridge_never_claims_success_without_real_api_credentials(self):
        with patch.dict(os.environ, {"OPENAI_API_KEY": "", "GEMINI_API_KEY": ""}):
            for use_openai in (True, False):
                status, body = self.post({"message": "test", "useOpenai": use_openai})
                self.assertEqual(status, 503)
                self.assertEqual(body["error"]["code"], "provider_not_configured")
                self.assertNotIn("Simulated", json.dumps(body))
        self.assertFalse(ai_bridge.SESSIONS)

    def test_real_provider_adapter_output_is_returned_and_backend_termination_works(self):
        with patch.dict(os.environ, {"OPENAI_API_KEY": "test-key-not-used"}), patch(
            "ai_bridge.call_openai_api", return_value="Real adapter result under test"
        ) as caller:
            status, body = self.post({"sessionId": "", "message": "check", "useOpenai": True})
            self.assertEqual(status, 200)
            self.assertEqual(body["provider"], "openai")
            self.assertEqual(body["response"], "Real adapter result under test")
            caller.assert_called_once()
            called_messages = caller.call_args.args[0]
            self.assertEqual(called_messages, [{"role": "user", "content": "check"}])
            session_id = body["sessionId"]
            status, ended = self.post({"sessionId": session_id}, route="/api/v1/bridge/terminate")
            self.assertEqual(status, 200)
            self.assertTrue(ended["terminated"])
            self.assertNotIn(session_id, ai_bridge.SESSIONS)

    def test_gemini_adapter_selection_uses_gemini_key(self):
        with patch.dict(os.environ, {"GEMINI_API_KEY": "test-key-not-used"}), patch(
            "ai_bridge.call_gemini_api", return_value="Gemini adapter reply"
        ) as caller:
            status, body = self.post({"message": "hello", "useOpenai": False})
            self.assertEqual(status, 200)
            self.assertEqual(body["provider"], "gemini")
            self.assertEqual(body["response"], "Gemini adapter reply")
            caller.assert_called_once()

    def test_provider_exception_returns_502_not_fake_reply(self):
        with patch.dict(os.environ, {"OPENAI_API_KEY": "test-key-not-used"}), patch(
            "ai_bridge.call_openai_api", side_effect=RuntimeError("do-not-log-key")
        ):
            status, body = self.post({"message": "test"})
        self.assertEqual(status, 502)
        self.assertEqual(body["error"]["code"], "provider_unavailable")
        self.assertNotIn("do-not-log-key", json.dumps(body))


if __name__ == "__main__":
    unittest.main()
