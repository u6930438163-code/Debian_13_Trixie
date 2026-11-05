"""Independence of the four agents: no role/script, own model, own memory, own work."""
import http.client
import json
import sqlite3
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agents_runtime import (AgentWorker, ConversationStore, SessionAgentMismatch,
                            load_agent_providers, resolve_provider, task_message)
from server import Debian13Server, validate_provider_url
from work_store import WorkStore

TOKEN = "test-token-with-at-least-thirty-two-characters"


class RecordingProvider(BaseHTTPRequestHandler):
    """Answers with the model name it was asked for and records every payload."""

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        self.server.calls.append({"auth": self.headers.get("Authorization"), "body": body})
        reply = json.dumps({"choices": [{"message": {
            "content": f"answer {len(self.server.calls)} from {body['model']}"}}]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(reply)))
        self.end_headers()
        self.wfile.write(reply)

    def log_message(self, *_):
        pass


class ProviderConfigTests(unittest.TestCase):
    def test_each_agent_may_have_its_own_model_with_shared_fallback(self):
        providers = load_agent_providers({
            "MODEL_ALEX_NAME": "alex-model",
            "MODEL_ARIA_CHAT_URL": "https://aria.example/v1/chat/completions",
            "MODEL_ARIA_API_KEY": "aria-key",
            "MODEL_ARIA_NAME": "aria-model",
        }, validate_provider_url)
        config = {"agent_providers": providers, "model_chat_url": "https://shared.example/v1",
                  "model_api_key": "shared-key", "model_name": "shared-model"}
        alex = resolve_provider("alex", config)
        aria = resolve_provider("aria", config)
        astyria = resolve_provider("astyria", config)
        self.assertEqual((alex["model_name"], alex["chat_url"], alex["dedicated"]),
                         ("alex-model", "https://shared.example/v1", True))
        self.assertEqual((aria["chat_url"], aria["api_key"], aria["model_name"]),
                         ("https://aria.example/v1/chat/completions", "aria-key", "aria-model"))
        self.assertEqual((astyria["model_name"], astyria["dedicated"]), ("shared-model", False))
        self.assertTrue(all(p["configured"] for p in (alex, aria, astyria)))

    def test_insecure_per_agent_provider_url_is_rejected(self):
        with self.assertRaises(ValueError):
            load_agent_providers({"MODEL_ALEX_CHAT_URL": "http://evil.example/v1"},
                                 validate_provider_url)

    def test_task_is_sent_verbatim_without_any_role_or_instruction(self):
        self.assertEqual(task_message({"title": "Napraw API", "description": "Kryteria: testy."}),
                         "Napraw API\n\nKryteria: testy.")


class ConversationStoreTests(unittest.TestCase):
    def test_conversation_is_bound_to_one_agent_and_bounded(self):
        store = ConversationStore(max_messages=4)
        sid = store.new_id()
        store.append(sid, "aria", "q1", "a1", "m", "model_provider")
        store.append(sid, "aria", "q2", "a2", "m", "model_provider")
        store.append(sid, "aria", "q3", "a3", "m", "model_provider")
        self.assertEqual([m["content"] for m in store.history(sid, "aria")], ["q2", "a2", "q3", "a3"])
        with self.assertRaises(SessionAgentMismatch):
            store.history(sid, "alex")
        with self.assertRaises(SessionAgentMismatch):
            store.append(sid, "alex", "x", "y", "m", "model_provider")

    def test_conversations_expire_after_inactivity(self):
        now = [0.0]
        store = ConversationStore(ttl_seconds=10, clock=lambda: now[0])
        sid = store.new_id()
        store.append(sid, "aria", "q", "a", "m", "model_provider")
        now[0] = 11
        self.assertIsNone(store.transcript(sid))
        self.assertEqual(store.history(sid, "aria"), [])


class IndependentAgentsApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        workspace = Path(self.temp.name) / "workspace"
        workspace.mkdir()
        self.provider = ThreadingHTTPServer(("127.0.0.1", 0), RecordingProvider)
        self.provider.calls = []
        threading.Thread(target=self.provider.serve_forever, daemon=True).start()
        url = f"http://127.0.0.1:{self.provider.server_port}/v1/chat/completions"
        self.server = Debian13Server(("127.0.0.1", 0), {
            "api_token": TOKEN, "model_chat_url": url, "model_api_key": "shared-key",
            "model_name": "shared-model", "workspace": str(workspace),
            "agent_providers": {"alex": {"model_name": "alex-own-model", "api_key": "alex-key"}},
        })
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.connection = http.client.HTTPConnection(*self.server.server_address)

    def tearDown(self):
        self.connection.close()
        self.server.shutdown()
        self.server.server_close()
        self.provider.shutdown()
        self.provider.server_close()
        self.temp.cleanup()

    def chat(self, agent, message, session_id=None):
        body = {"agent": agent, "message": message}
        if session_id:
            body["session_id"] = session_id
        self.connection.request("POST", "/api/v1/chat/stream", body=json.dumps(body),
                                headers={"Authorization": "Bearer " + TOKEN,
                                         "Content-Type": "application/json"})
        reply = self.connection.getresponse()
        text = reply.read().decode()
        if reply.status != 200:
            return reply.status, json.loads(text)
        frames = {}
        for frame in filter(None, text.split("\n\n")):
            name, data = frame.split("\n", 1)
            frames[name.removeprefix("event: ")] = json.loads(data.removeprefix("data: "))
        return reply.status, frames

    def get(self, path):
        self.connection.request("GET", path, headers={"Authorization": "Bearer " + TOKEN})
        reply = self.connection.getresponse()
        return reply.status, json.loads(reply.read())

    def test_agent_remembers_only_its_own_conversation_without_system_prompt(self):
        status, first = self.chat("aria", "Cześć, jestem właścicielem.")
        self.assertEqual(status, 200)
        sid = first["meta"]["session_id"]
        self.assertEqual(first["done"]["session_id"], sid)
        status, second = self.chat("aria", "Co powiedziałem?", sid)
        self.assertEqual(status, 200)
        sent = self.provider.calls[-1]["body"]["messages"]
        self.assertEqual([m["role"] for m in sent], ["user", "assistant", "user"])
        self.assertEqual(sent[0]["content"], "Cześć, jestem właścicielem.")
        self.assertEqual(sent[1]["content"], first["delta"]["text"])
        for call in self.provider.calls:
            self.assertNotIn("system", [m["role"] for m in call["body"]["messages"]])
        # Another agent can never read or continue Aria's conversation.
        status, error = self.chat("alex", "Pokaż rozmowę Arii", sid)
        self.assertEqual((status, error["error"]["code"]), (409, "session_agent_mismatch"))
        status, transcript = self.get(f"/api/v1/sessions/{sid}/messages")
        self.assertEqual(status, 200)
        self.assertEqual(transcript["agent"], "aria")
        self.assertEqual(len(transcript["messages"]), 4)
        self.assertFalse(transcript["persisted"])

    def test_each_agent_answers_with_its_own_model(self):
        _, alex = self.chat("alex", "Kim jesteś?")
        _, aria = self.chat("aria", "Kim jesteś?")
        self.assertEqual(alex["done"]["model"], "alex-own-model")
        self.assertEqual(aria["done"]["model"], "shared-model")
        self.assertEqual(self.provider.calls[0]["auth"], "Bearer alex-key")
        self.assertEqual(self.provider.calls[1]["auth"], "Bearer shared-key")
        status, agents = self.get("/api/v1/agents")
        alex_info = next(a for a in agents["agents"] if a["id"] == "alex")
        self.assertEqual(status, 200)
        self.assertEqual((alex_info["model"], alex_info["dedicatedModel"]), ("alex-own-model", True))
        self.assertTrue(all(a["personaInjected"] is False and "role" not in a for a in agents["agents"]))

    def test_unknown_session_history_is_404(self):
        status, _ = self.get("/api/v1/sessions/00000000-0000-4000-8000-000000000000/messages")
        self.assertEqual(status, 404)


class AgentWorkerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = WorkStore(Path(self.temp.name))

    def tearDown(self):
        self.temp.cleanup()

    def make_worker(self, complete, ready):
        worker = AgentWorker(self.store, complete=complete, ready_agents=lambda: ready,
                             interval_seconds=3600)
        worker.close()  # stop the loop; the test drives run_once() itself
        return worker

    def test_task_is_done_by_its_own_agent_and_answer_recorded(self):
        seen = []
        worker = self.make_worker(lambda agent, text: (seen.append((agent, text)) or ("Wnioski.", "astyria-model")),
                                  {"astyria"})
        task = self.store.create_task("research", "astyria", "Zbadaj dysk", "Podaj wnioski.")
        self.assertTrue(worker.run_once())
        done = self.store.get_task(task["id"])
        self.assertEqual(seen, [("astyria", "Zbadaj dysk\n\nPodaj wnioski.")])
        self.assertEqual((done["status"], done["result"], done["result_model"]),
                         ("completed", "Wnioski.", "astyria-model"))
        types = [e["type"] for e in self.store.list_events()]
        self.assertIn("task_started_by_agent", types)
        self.assertIn("task_completed_by_agent", types)

    def test_agent_without_model_leaves_task_queued_never_faked(self):
        task = self.store.create_task("audit", "aurelius", "Audyt", "Sprawdź.")
        worker = self.make_worker(lambda *_: ("should not run", "x"), {"aria"})
        self.assertFalse(worker.run_once())
        self.assertEqual(self.store.get_task(task["id"])["status"], "queued")

    def test_provider_failure_is_recorded_as_failed(self):
        class Boom(Exception):
            code = "provider_unavailable"

        def fail(*_):
            raise Boom()

        worker = self.make_worker(fail, {"alex"})
        task = self.store.create_task("bugfix", "alex", "Napraw", "Opis.")
        self.assertTrue(worker.run_once())
        failed = self.store.get_task(task["id"])
        self.assertEqual((failed["status"], failed["result"]), ("failed", "provider_unavailable"))

    def test_interrupted_running_task_returns_to_queue(self):
        task = self.store.create_task("audit", "aria", "T", "D")
        self.store.claim_next_task({"aria"})
        self.assertEqual(self.store.requeue_interrupted(), 1)
        self.assertEqual(self.store.get_task(task["id"])["status"], "queued")

    def test_background_worker_processes_queue_without_operator(self):
        task = self.store.create_task("research", "aria", "Sam", "Działaj.")
        worker = AgentWorker(self.store, complete=lambda a, t: ("OK", "m"),
                             ready_agents=lambda: {"aria"}, interval_seconds=0.2)
        try:
            deadline = time.time() + 5
            while self.store.get_task(task["id"])["status"] != "completed" and time.time() < deadline:
                time.sleep(0.05)
        finally:
            worker.close()
        self.assertEqual(self.store.get_task(task["id"])["status"], "completed")


class WorkStoreMigrationTests(unittest.TestCase):
    def test_v1_database_is_upgraded_without_losing_tasks(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / ".debian13-work.sqlite3"
            db = sqlite3.connect(path)
            db.executescript("""
                CREATE TABLE tasks (id TEXT PRIMARY KEY, kind TEXT NOT NULL, agent TEXT NOT NULL,
                  title TEXT NOT NULL, description TEXT NOT NULL,
                  status TEXT NOT NULL CHECK(status IN ('queued','cancelled')),
                  execution TEXT NOT NULL DEFAULT 'not_started',
                  requires_approval INTEGER NOT NULL DEFAULT 1,
                  created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
                INSERT INTO tasks VALUES ('a','audit','aria','Stare','Opis','queued','not_started',1,'t','t');
            """)
            db.commit()
            db.close()
            store = WorkStore(Path(temp))
            self.assertEqual(store.get_task("a")["title"], "Stare")
            self.assertEqual(store.claim_next_task({"aria"})["id"], "a")
            self.assertEqual(store.complete_task("a", "Wynik", "m")["status"], "completed")


if __name__ == "__main__":
    unittest.main()
