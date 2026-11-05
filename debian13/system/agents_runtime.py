"""Independent runtime for the owner's four agents: Aria, Astyria, Aurelius, Alex.

Independence here means, concretely and verifiably:

* **No role, persona or script.** Nothing is ever prepended to a conversation:
  no system prompt, specialty, hierarchy, style instruction or canned reply.
  The model receives only the operator's own messages (and its own previous
  answers in the same conversation).
* **Own model per agent.** ``MODEL_<AGENT>_CHAT_URL``, ``MODEL_<AGENT>_API_KEY``
  and ``MODEL_<AGENT>_NAME`` let every agent run on a different provider or
  model. Each field falls back to the shared ``MODEL_*`` value when unset.
* **Own memory per agent.** A conversation is bound to exactly one agent; its
  context is never shown to another agent. Memory lives in this process only
  (not on disk) and expires after inactivity.
* **Own work.** The optional :class:`AgentWorker` hands each queued Work task to
  the agent it is assigned to — the real agent in AI_Studio_BastillaOS when that
  link is configured, otherwise the agent's own local model — and records the
  model's actual answer. It never executes shell commands, edits files, grants
  root, calls GitHub or makes purchases: the agent's output is text that the
  operator reviews.
"""
from __future__ import annotations

import threading
import time
import uuid
from collections import OrderedDict
from typing import Callable, Mapping

AGENT_IDS = ("aria", "astyria", "aurelius", "alex")
PROVIDER_FIELDS = (("chat_url", "CHAT_URL"), ("api_key", "API_KEY"), ("model_name", "NAME"))

MAX_SESSIONS = 200
MAX_SESSION_MESSAGES = 40
SESSION_TTL_SECONDS = 2 * 60 * 60


def load_agent_providers(environ: Mapping[str, str],
                         validate_url: Callable[[str], str]) -> dict[str, dict[str, str]]:
    """Read per-agent provider overrides; only explicitly set fields are kept."""
    providers: dict[str, dict[str, str]] = {}
    for agent in AGENT_IDS:
        prefix = "MODEL_" + agent.upper() + "_"
        override = {}
        for key, suffix in PROVIDER_FIELDS:
            value = environ.get(prefix + suffix, "").strip()
            if value:
                override[key] = validate_url(value) if key == "chat_url" else value
        if override:
            providers[agent] = override
    return providers


def resolve_provider(agent: str, config: Mapping) -> dict:
    """Return the effective provider for one agent (override, else shared)."""
    override = (config.get("agent_providers") or {}).get(agent, {})
    resolved = {
        "chat_url": override.get("chat_url") or config.get("model_chat_url", ""),
        "api_key": override.get("api_key") or config.get("model_api_key", ""),
        "model_name": override.get("model_name") or config.get("model_name", ""),
    }
    resolved["configured"] = all(resolved[key] for key, _ in PROVIDER_FIELDS)
    resolved["dedicated"] = bool(override)
    return resolved


class SessionAgentMismatch(Exception):
    """A conversation belongs to a different agent; contexts never mix."""


class ConversationStore:
    """Bounded, thread-safe, in-process memory: one conversation = one agent."""

    def __init__(self, max_sessions: int = MAX_SESSIONS,
                 max_messages: int = MAX_SESSION_MESSAGES,
                 ttl_seconds: int = SESSION_TTL_SECONDS,
                 clock: Callable[[], float] = time.monotonic):
        self._sessions: OrderedDict[str, dict] = OrderedDict()
        self._lock = threading.Lock()
        self._max_sessions = max_sessions
        self._max_messages = max_messages
        self._ttl = ttl_seconds
        self._clock = clock

    def _expire(self) -> None:
        now = self._clock()
        for key in [k for k, v in self._sessions.items() if now - v["touched"] > self._ttl]:
            del self._sessions[key]

    def history(self, session_id: str, agent: str) -> list[dict]:
        """Model-facing history (role/content only) for this agent's conversation."""
        with self._lock:
            self._expire()
            session = self._sessions.get(session_id)
            if session is None:
                return []
            if session["agent"] != agent:
                raise SessionAgentMismatch(session_id)
            return [{"role": m["role"], "content": m["content"]} for m in session["messages"]]

    def append(self, session_id: str, agent: str, user_text: str,
               reply: str, model: str, source: str) -> None:
        with self._lock:
            self._expire()
            session = self._sessions.get(session_id)
            if session is None:
                while len(self._sessions) >= self._max_sessions:
                    self._sessions.popitem(last=False)
                session = {"agent": agent, "messages": [], "touched": self._clock()}
                self._sessions[session_id] = session
            elif session["agent"] != agent:
                raise SessionAgentMismatch(session_id)
            session["messages"].extend([
                {"role": "user", "content": user_text, "agent": agent},
                {"role": "assistant", "content": reply, "agent": agent,
                 "model": model, "source": source},
            ])
            del session["messages"][:-self._max_messages]
            session["touched"] = self._clock()
            self._sessions.move_to_end(session_id)

    def transcript(self, session_id: str) -> dict | None:
        with self._lock:
            self._expire()
            session = self._sessions.get(session_id)
            if session is None:
                return None
            return {"agent": session["agent"],
                    "messages": [dict(m) for m in session["messages"]]}

    @staticmethod
    def new_id() -> str:
        return str(uuid.uuid4())


def task_message(task: Mapping) -> str:
    """The task itself is the only input: no role, persona or instructions added."""
    return f"{task['title']}\n\n{task['description']}"


class AgentWorker:
    """Background loop: each queued task goes to its own agent's model.

    Tasks for an agent without a configured provider are left untouched in the
    queue, so nothing is ever reported as done without a real model answer.
    """

    def __init__(self, store, complete: Callable[[dict], tuple[str, str]],
                 ready_agents: Callable[[], set[str]],
                 interval_seconds: float = 5.0):
        self._store = store
        self._complete = complete
        self._ready_agents = ready_agents
        self._interval = max(0.2, float(interval_seconds))
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._state = {"enabled": True, "processed": 0, "failed": 0,
                       "currentTask": None, "lastActivityAt": None}
        store.requeue_interrupted()
        self._thread = threading.Thread(target=self._run, daemon=True, name="debian13-agent-worker")
        self._thread.start()

    def run_once(self) -> bool:
        agents = self._ready_agents()
        if not agents:
            return False
        task = self._store.claim_next_task(agents)
        if task is None:
            return False
        with self._lock:
            self._state["currentTask"] = task["id"]
        try:
            # The callable decides where the agent lives: the real BastillaOS
            # agent when linked, otherwise the agent's own local model.
            reply, model = self._complete(task)
        except Exception as exc:  # provider failures are recorded, never faked
            code = getattr(exc, "code", "agent_model_failed")
            self._store.fail_task(task["id"], str(code)[:64])
            with self._lock:
                self._state["failed"] += 1
        else:
            self._store.complete_task(task["id"], reply, model)
            with self._lock:
                self._state["processed"] += 1
        finally:
            with self._lock:
                self._state["currentTask"] = None
                self._state["lastActivityAt"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        return True

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                worked = self.run_once()
            except Exception:
                worked = False
            if not worked and self._stop.wait(self._interval):
                break

    def snapshot(self) -> dict:
        with self._lock:
            return dict(self._state)

    def close(self) -> None:
        self._stop.set()
        self._thread.join(timeout=5)
