# Debian 13 Trixie — MCP, Codex, original Alex, GPT Actions

This is an independent Debian 13 application prototype. Working source files and
mock tests are **not** evidence of a running remote model, hosted agent or live
ChatGPT connection. The integrations API deliberately returns `connected: false`
until an external client or worker is verified.

## 1. MCP: stdio and authenticated Streamable HTTP

The MCP implementation at `debian13/system/mcp_server.py` supports these
read-only tools: `debian13_profiles`, `debian13_diagnostics`,
`debian13_integrations`, `debian13_work_tasks`,
`debian13_original_agents`, `alex_existing_status`, and
`alex_existing_tasks`. An additional authenticated write tool, `debian13_queue_work_task`, accepts
`agent` (alex/aria/astyria/aurelius), `kind` (development/bugfix/audit/
research), `title` (1–120 chars) and `description` (1–2000 chars).
It creates a review-only task in the SQLite Work queue, returning
`status: queued`, `executionStarted: false` and `requires_approval: 1`.
Both stdio MCP (via authorized HTTP API) and HTTP MCP (via the authenticated
backend Work store) support queueing. Queueing is **not autonomous execution**: it does not run a model or
worker, execute shell commands, edit files, grant root, execute GitHub
writes or make purchases. The separate model-chat tool calls a real configured
provider but does not execute commands or run background agents.
The MCP server also exposes three additional tools:

- `debian13_work_task_detail` (read-only): retrieve a queued Work task by UUID.
- `debian13_cancel_work_task` (write): cancel a queued task before external handoff.
  It cannot retract a task already handed to Alex.
- `debian13_chat_agent` (model action): send a message to the configured
  text-model provider under one selected profile (`alex`, `aria`,
  `astyria`, `aurelius`). No arbitrary system instruction or specialty is
  injected; the reply is returned only on successful model inference.
  The response explicitly states `originalAgentExecutionVerified: false` and
  `sessionPersisted: false`; this is **not** a conversation with a verified
  original autonomous worker. A model provider may charge for API usage.

There are now **11 MCP tools** (eight reads, task queue, cancellation, model
chat). Every tool requires authentication and rejects invalid arguments.
No arbitrary shell or filesystem write is exposed through MCP.

A deployed, authorized client and verified original agent workers are
needed before treating this as a live bidirectional AI integration.

**Local stdio:** start the application first:

```sh
bash debian13/start-local.sh
```

Then, in another terminal in the repository root:

```sh
export DEBIAN13_API_TOKEN="$(cat ~/.local/state/debian13/operator-token)"
export DEBIAN13_SYSTEM_API_URL=http://127.0.0.1:8765
python3 debian13/system/mcp_server.py
```

The MCP process listens on standard input for newline-delimited JSON-RPC.
Do not type the bearer secret in a prompt or log. With Docker Compose,
the internal API lives on port 8080 and the Compose environment sets
`DEBIAN13_SYSTEM_API_URL=http://127.0.0.1:8080` automatically.

**HTTP MCP (new):** the Debian 13 API offers stateless Streamable HTTP at
`POST /mcp`, with JSON replies and `202` for notifications. It requires
`Authorization: Bearer <operator-token>`, `Content-Type: application/json`
and `Accept: application/json, text/event-stream`. Unauthorized requests
get 401, browser Origin headers are rejected with 403, and `GET /mcp`
gets 405 because this implementation has no SSE stream. There is no
anonymous access, token exchange or OAuth implementation.

Local test (do not paste output containing secrets):

```sh
TOKEN="$(cat ~/.local/state/debian13/operator-token)"
curl -fsS http://127.0.0.1:8765/mcp \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' \
  --data '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}'
```

The Node/AI Studio server `server.ts` also proxies `/mcp` to Python but
**only** with a valid external Bearer header; it does not authorize a
remote MCP request using the private UI proxy's implicit token.

To connect ChatGPT, deploy via a verified, secured HTTPS endpoint or
use an approved Secure MCP Tunnel, then add and test a custom MCP
server in ChatGPT Plugins if allowed by the account/workspace. Do not
publish a Codespaces port or the private API directly. A configured
MCP client is still required. Local stdio by itself is not a ChatGPT
cloud connector.

### Hosted Node gateway and private console

The Node/AI Studio entrypoint `server.ts` requires the operator Bearer credential
for **all** `/api/v1/*` routes as well as `/v1/*` and `/mcp`. The hosted
browser UI asks the operator for this token after HTTP 401; it keeps the
credential only in memory, never in the URL, localStorage, or logs. Load the
panel only from the trusted HTTPS origin before providing any credential.
When running `bash debian13/start-local.sh` in a private Codespace, the
Python UI proxy uses server-side credentials and requires no browser prompt.

The Gemini inference bridge `/v1/chat/completions` is **not** a public model
API: only the local Python backend can access it using a separate random,
process-only Bearer token plus a loopback socket. The normal operator token
cannot authorize model-bridge calls. Keep the hosting ingress private or
behind HTTPS with access controls; do not expose the service as an open proxy.

## 2. GPT Actions

The schema is `debian13/system/gpt-actions.openapi.json`. It exposes
read-only profiles, diagnostics, integration status, original agent status,
Alex capability status, and the consequential model chat operation.
`server.ts` now forwards the matching `/v1/*` routes to the Python
API with incoming Bearer-token verification.

**Before connecting a custom GPT**, deploy a secure HTTPS origin with
access controls, and replace the placeholder `servers[0].url` value
`https://debian13.example.com` with the actual HTTPS origin.
Configure the operator token privately in the hosting secret manager
as `DEBIAN13_API_TOKEN` (at least 32 characters), and enter exactly
that token in the GPT Actions editor under **API key → Bearer**.
Never put secrets in the OpenAPI file. Test a read-only action first.
Do not use `/v1/terminal` as a GPT Action.

If the deployment isn't online or a model is not configured,
GPT Actions will not be live. Importing files into AI Studio is not
itself a backend deployment or a custom GPT registration.

## 3. Original Alex: capability-gated, never simulated

Original profiles originate in `u6930438163-code/AI_Studio_BastillaOS`.
The existing source exposes `GET /api/agent-tasks`, which is only a
task registry. It does **not** currently demonstrate an autonomous
coding agent or the endpoints this adapter requires for task handoff:
`GET /api/agent-tasks/capabilities` and
`POST /api/agent-tasks/submit`. The bridge fails closed rather than
calling the incompatible `/agent-tasks/run`.

To configure a real source server **after deployment and verification**:
```sh
export ALEX_EXISTING_API_URL=https://VERIFIED-ALEX-HOST.example/api
# set ALEX_EXISTING_API_TOKEN privately; never commit or paste it
export ALEX_ALLOW_TASK_DISPATCH=1
```

Dispatch is optional and requires explicit operator action; it targets
`u6930438163-code/Debian_13_Trixie` and can only request a PR proposal,
never a purchase or automatic merge. The legacy target
`mmitek515-netizen/Debian_13` is no longer used. The remote API must
advertise the `debian13_issue_to_pr` capability; without it the handoff
remains disabled and honest status is reported.

## 4. Codex / GitHub Actions

The Codex workflow `.github/workflows/debian13-codex-issue.yml`
now checks out and opens pull requests against the actual default
branch `main`; the nonexistent `migration/debian13-codespaces`
branch has been removed from its references. The scheduled and agent
task workflows also use `main`.

For a real run, enable GitHub Actions and add `OPENAI_API_KEY` as a
**repository Actions secret**. An ordinary ChatGPT subscription does
not provide this API credential. Open the Codex workflow in GitHub
Actions, choose **Run workflow**, and enter an existing issue number
**in this new repository**. Codex proposes source changes only within
`debian13/`, tests them in a separate job and opens a PR for review.
It never merges automatically or proves that the original Alex is active.

Other GitHub agent-planning workflows additionally require secrets
`DEBIAN13_MODEL_CHAT_URL`, `DEBIAN13_MODEL_API_KEY`, and variable
`DEBIAN13_MODEL_NAME`.

## 5. Tests and live-state proof

```sh
python3 -m unittest discover -s debian13/system/tests -p 'test_*.py' -v
node --test debian13/ui/dev_server.test.mjs
bash debian13/start-local.sh --check
```

Check GitHub Actions runs on the current commit. A passing CI run proves
only the code and temporary test environment. A real **connected** state
requires a verified client handshake, reachable deployment, valid
permissions and (for AI tasks) evidence of an actual worker action.


### ChatGPT connection limitations and next step

Source code updates and passing CI are not equivalent to a live ChatGPT MCP
connection. ChatGPT needs a reachable, authenticated HTTPS MCP endpoint
or a Secure MCP Tunnel, a verified client handshake, and a plugin/app that
has permission to use each tool. The current HTTP server requires a private
operator Bearer token; do not publish that token or disable authentication.

As documented by OpenAI for November 2026, full custom MCP with write
actions is offered to ChatGPT Business and Enterprise/Edu on web, subject to
workspace permissions. A personal Plus account does not get those full write
permissions merely by synchronizing code. The existing GitHub connection
inside ChatGPT is a distinct path for reviewing and editing the repository.

Minimal functional verification:
1. Confirm the external HTTPS endpoint is reachable, private and secure.
2. Authenticate and call `initialize`, then `tools/list`.
3. Exercise `debian13_work_task_detail` and queue/cancel with a disposable
   task, verifying that the Work store changes.
4. Test `debian13_chat_agent` only once a provider is available, and
   do not mistake a generic model response for the owner's original AI.
5. Independently verify a worker receiving a real task and reporting its
   result before claiming autonomous or bidirectional execution.
