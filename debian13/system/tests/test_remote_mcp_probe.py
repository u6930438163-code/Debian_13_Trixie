"""Local, real-backend tests for the remote MCP readiness probe."""
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
from remote_mcp_probe import CheckFailure, canonical_base, verify

TOKEN = "mcp-remote-probe-ci-token-at-least-32-characters"


class RemoteMcpProbeTests(unittest.TestCase):
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
        self.base = "http://127.0.0.1:" + str(self.api.server_port)

    def tearDown(self):
        self.api.shutdown()
        self.api.server_close()
        self.thread.join(timeout=3)
        self.temp.cleanup()

    def test_actual_http_backend_completes_read_only_mcp_handshake(self):
        with patch.dict(os.environ, {
            "DEBIAN13_API_TOKEN": TOKEN,
            "DEBIAN13_SYSTEM_API_URL": self.base,
        }):
            result = verify(self.base, TOKEN)
        self.assertEqual(result, [
            "health", "unauthorized_rejected", "diagnostics",
            "mcp_initialize", "mcp_tools_list", "mcp_diagnostics_tool",
        ])

    def test_wrong_token_is_rejected_without_echoing_token(self):
        with self.assertRaises(CheckFailure) as ctx:
            verify(self.base, TOKEN + "wrong")
        self.assertEqual(str(ctx.exception), "authenticated_diagnostics_failed")
        self.assertNotIn(TOKEN, str(ctx.exception))

    def test_short_token_rejected_before_network(self):
        with self.assertRaises(CheckFailure) as ctx:
            verify(self.base, "too-short")
        self.assertEqual(str(ctx.exception), "invalid_operator_token")

    def test_insecure_remote_or_untrusted_url_rejected(self):
        for url in [
            "http://example.com", "http://203.0.113.1:8080",
            "https://user:secret@example.com", "https://example.com/path",
            "https://example.com?token=secret", "file:///etc/passwd",
        ]:
            with self.subTest(url=url), self.assertRaises(CheckFailure):
                canonical_base(url)


if __name__ == "__main__":
    unittest.main()
