# MCP gateway to the ORIGINAL AI Studio agents

Project: `u6930438163-code/Debian_13_Trixie`
Source repository: `u6930438163-code/AI_Studio_BastillaOS`

This gateway **does not create, impersonate, or simulate** Alex, Aria,
Astyria, or Aurelius. It is a narrow bridge to the explicitly configured
owner-controlled AI Studio HTTP service and its original registry.

## Tools (available from the existing authenticated `POST /mcp`)

| MCP tool | Purpose | Effect |
| --- | --- | --- |
| `list_agents` | Read the real source `GET /api/agent-tasks` registry and report four known profiles | Read-only |
| `get_task_status(agent, task_id)` | Read an exact agent/task record from the source registry | Read-only |
| `send_message(agent, message)` | Offer a message to the ORIGINAL agent through an explicitly advertised remote protocol | Write, **disabled by default** |
| `create_task(agent, title, description, kind)` | Submit a *proposal for a PR*, never auto-merge or root execution | Write, **disabled by default** |

Reading a task whose registry status is `running` does **not** verify live
inference or execution by the original worker.

## Real upstream API vs. compatibility contract

Inspected in `AI_Studio_BastillaOS/server.ts`:

- **Existing**: `GET /api/agent-tasks`, returning `success` and `tasks`.
- **Existing, unsafe for this goal**: `POST /api/agent-tasks/run`,
  which can default to application scaffolding for an unrelated Bastilla
  project. The gateway **never invokes** it.
- **Existing, wrong direction**: `POST /api/agents/inbox/send` accepts
  inbound notifications from agents to the user; it does **not** dispatch
  owner messages to an agent. The gateway never invokes it.
- **Not yet verified in the source**: a typed capability handshake and
  safe submission endpoints for all four agents.

Therefore reads may be tested against the real registry now; writes will
**refuse with `source_capability_not_implemented`** until the original
source implementation is separately updated and verified.

### Required source contract for enabling writes later

Under the configured source base URL ending in `/api`:

```text
GET  /api/agent-tasks/capabilities
POST /api/agent-tasks/messages
POST /api/agent-tasks/submit
```

The capabilities response must identify the **real** source repository,
`protocolVersion: 1`, `agents` containing the target agent, and explicitly
advertise `debian13_agent_message_v1` or `debian13_review_task_v1`.
The write endpoints must validate an authenticated operator, reject
arbitrary shell commands, block unapproved purchases, and avoid auto-merge.
The gateway requires acknowledgements matching the requested agent and
valid `messageId` or `taskId`. An acceptance receipt does not prove that
the agent actually executed a task.

No source endpoint is invented or presumed live by a successful local test.

## Secrets and permission gates

1. Configure `ALEX_EXISTING_API_URL` as a trusted HTTPS URL ending in
   `/api` for the genuine original source deployment. Loopback HTTP
   is allowed for a local integration test only.
2. Configure `ORIGINAL_AGENTS_API_TOKEN` (32–512 printable characters)
   as a private runtime secret. The existing
   `ALEX_EXISTING_API_TOKEN` is a fallback for deployments already using it.
3. Keep `ORIGINAL_AGENTS_ALLOW_WRITES=0` until a real compatible
   remote API has passed capability tests **and** you explicitly choose
   to enable submissions. This setting must not be exposed to browsers.
4. The public MCP entrypoint also requires `DEBIAN13_API_TOKEN` and TLS.
   It rejects requests with a browser Origin and only accepts JSON-RPC.
5. Treat every model-initiated `send_message` and `create_task` call as
   a consequential action requiring human review by the client. Do not
   assume that the MCP protocol itself prompts for approval.
6. GitHub repository administrator rights belong to real GitHub
   accounts or installed GitHub Apps, **not** to an AI profile name.
   No admin permissions are granted by this PR. Prefer narrow
   `Issues: write`, `Contents: write`, and `Pull requests: write`
   for reviewed branch/PR workflows. Do not grant `Administration: write`,
   secrets administration, bypass of protection rules, or automatic merge
   to autonomous agents.

Example local read-only verification (no cloud/API credentials shown):

```sh
python3 -m unittest discover -s debian13/system/tests -p 'test_*.py' -v
```

After enabling a real source endpoint, validate `tools/list` and
`tools/call` through the actual authenticated public MCP HTTPS endpoint,
then verify the response and source-agent task receipt independently.
GitHub Actions tests use stubs for the remote AI Studio endpoint; they
cannot prove live source connectivity, model identity, or admin permissions.
