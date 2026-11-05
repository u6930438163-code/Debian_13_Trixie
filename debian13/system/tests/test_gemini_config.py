"""Tests for opt-in Gemini API configuration (no network or API key required)."""

import json
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from server import PROFILES, complete_chat, load_config, validate_provider_url


TOKEN = "unit-test-operator-token-at-least-thirty-two-characters"
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"


class _Response:
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self, _limit):
        return json.dumps({"choices": [{"message": {"content": "Gemini fixture response"}}]}).encode()


class _Opener:
    def __init__(self):
        self.request = None

    def open(self, request, timeout):
        self.request = request
        return _Response()


class GeminiConfigurationTests(unittest.TestCase):
    def test_google_compatibility_url_is_allowed_only_for_google_host(self):
        self.assertEqual(validate_provider_url(GEMINI_URL), GEMINI_URL)
        self.assertEqual(
            validate_provider_url("https://provider.example/v1/chat/completions"),
            "https://provider.example/v1/chat/completions",
        )
        with self.assertRaises(ValueError):
            validate_provider_url("https://attacker.example/v1beta/openai/chat/completions")
        with self.assertRaises(ValueError):
            validate_provider_url("http://generativelanguage.googleapis.com/v1beta/openai/chat/completions")

    def test_gemini_key_selects_provider_without_changing_agent_identities(self):
        with patch.dict(os.environ, {
            "DEBIAN13_API_TOKEN": TOKEN,
            "GEMINI_API_KEY": "test-gemini-key-not-a-real-secret",
        }, clear=True):
            config = load_config()
        self.assertEqual(config["model_chat_url"], GEMINI_URL)
        self.assertEqual(config["model_api_key"], "test-gemini-key-not-a-real-secret")
        self.assertEqual(config["model_name"], "gemini-3.8-flash")
        self.assertEqual(config["vision_model_name"], "gemini-3.8-flash")
        self.assertEqual(
            {agent: profile["name"] for agent, profile in PROFILES.items()},
            {"alex": "Alex", "aria": "Aria", "astyria": "Astyria", "aurelius": "Aurelius"},
        )

    def test_gemini_model_and_vision_can_be_selected_separately(self):
        with patch.dict(os.environ, {
            "DEBIAN13_API_TOKEN": TOKEN,
            "GEMINI_API_KEY": "test-gemini-key",
            "GEMINI_MODEL": "gemini-3-flash-preview",
            "GEMINI_VISION_MODEL": "gemini-3.8-flash",
            "VISION_MODEL_NAME": "old-openai-or-ollama-vision",
        }, clear=True):
            config = load_config()
        self.assertEqual(config["model_name"], "gemini-3-flash-preview")
        self.assertEqual(config["vision_model_name"], "gemini-3.8-flash")

    def test_explicit_openai_compatible_provider_keeps_its_own_credentials(self):
        with patch.dict(os.environ, {
            "DEBIAN13_API_TOKEN": TOKEN,
            "GEMINI_API_KEY": "must-not-be-sent-to-other-provider",
            "MODEL_CHAT_URL": "https://openai-compatible.example/v1/chat/completions",
            "MODEL_API_KEY": "own-provider-secret",
            "MODEL_NAME": "another-model",
            "VISION_MODEL_NAME": "another-vision-model",
        }, clear=True):
            config = load_config()
        self.assertEqual(config["model_chat_url"], "https://openai-compatible.example/v1/chat/completions")
        self.assertEqual(config["model_api_key"], "own-provider-secret")
        self.assertEqual(config["model_name"], "another-model")
        self.assertEqual(config["vision_model_name"], "another-vision-model")

    def test_never_forward_gemini_key_to_custom_provider(self):
        with patch.dict(os.environ, {
            "DEBIAN13_API_TOKEN": TOKEN,
            "GEMINI_API_KEY": "do-not-leak",
            "MODEL_CHAT_URL": "https://another-provider.example/v1/chat/completions",
            "MODEL_NAME": "another-model",
        }, clear=True):
            config = load_config()
        self.assertEqual(config["model_api_key"], "")

    def test_chat_and_image_use_gemini_compatibility_payload(self):
        with patch.dict(os.environ, {
            "DEBIAN13_API_TOKEN": TOKEN,
            "GEMINI_API_KEY": "test-gemini-key",
        }, clear=True):
            config = load_config()
        opener = _Opener()
        with patch("server.urllib.request.build_opener", return_value=opener):
            reply, model = complete_chat("aria", "Witaj", config)
            self.assertEqual((reply, model), ("Gemini fixture response", "gemini-3.8-flash"))
            self.assertEqual(opener.request.full_url, GEMINI_URL)
            self.assertEqual(opener.request.get_header("Authorization"), "Bearer test-gemini-key")
            payload = json.loads(opener.request.data)
            self.assertEqual(payload["messages"], [{"role": "user", "content": "Witaj"}])
            self.assertNotIn("system", [m["role"] for m in payload["messages"]])
            self.assertNotIn("temperature", payload)

            reply, model = complete_chat(
                "alex", "Opisz obraz", config, image="data:image/png;base64,aGVsbG8="
            )
            self.assertEqual(model, "gemini-3.8-flash")
            body = json.loads(opener.request.data)
            self.assertEqual(body["messages"][0]["content"][1]["image_url"],
                             {"url": "data:image/png;base64,aGVsbG8="})


if __name__ == "__main__":
    unittest.main()
