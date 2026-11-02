# Debian 13 Trixie — remote connection readiness

This checklist applies to `u6930438163-code/Debian_13_Trixie` and is
**not** a claim that the deployment or original AI agents are connected.

## Verified facts and current limits

- The app and Python API are in `main`. CI runs against an ephemeral test
  container; this does not deploy a permanent server.
- The operator-provided AI Studio host
  `https://ais-pre-gsa33tltmvd67ewcnhan5u-873947482948.europe-west2.run.app`
  has not been confirmed to serve this repository revision.
- The API exposes authenticated `POST /mcp` (read-only tools). Its OpenAPI
  schema at `debian13/system/gpt-actions.openapi.json` is for GPT Actions,
  not the MCP endpoint.
- The `debian13/system/Dockerfile` runs the Python API on port 8080 without
  the full Node/GUI frontend. Docker Compose is configured for **loopback**
  local binding; do not publish an unprotected container port.

## Deployment prerequisites — not yet performed by this repository

1. Choose an operator-controlled HTTPS service (for example, Cloud Run
   in a selected Google Cloud project). Importing the app into AI Studio does
   not establish a deployment.
2. Verify access to the Google Cloud project, service and billing terms.
   Do **not** grant a third party universal admin credentials or commit
   service-account keys to GitHub.
3. Build and deploy the tested container with an HTTPS ingress/reverse proxy.
   The runtime requires a private `DEBIAN13_API_TOKEN` of 32+ random
   characters; load it from the hosting platform's secret store.
4. Protect traffic with TLS and application authentication. `/healthz`
   is intentionally public, but `/v1/diagnostics` and `/mcp` require
   the operator Bearer credential. Do not expose the container directly.
5. This app stores Work tasks in SQLite in `WORKSPACE_DIR`; Cloud Run's
   writable local filesystem is **ephemeral**. Before calling this a
   persistent production Work service, configure durable storage or a
   supported external database. An environment with multiple instances
   should not share ephemeral independent task ledgers.
6. A reachable MCP endpoint alone does not register it as a ChatGPT tool.
   Configure an approved MCP client with suitable auth and run its
   `initialize`, `tools/list` and a read-only `tools/call` test.
   A bearer-only HTTP MCP implementation may require additional OAuth
   support or an approved authenticated gateway for a specific client.
7. GPT Actions require their own HTTPS service URL and private API key
   configuration. Replace the example OpenAPI `servers[0].url` only
   after the actual deployment URL is confirmed.

## How to verify a deployed service without exposing secrets

Locally set (without posting the token to chats, Issues or CI logs):

```sh
export DEBIAN13_REMOTE_BASE_URL="https://YOUR-VERIFIED-HOST"
export DEBIAN13_REMOTE_API_TOKEN="<read from your secure secret store>"
python3 debian13/system/remote_mcp_probe.py
```

The probe sends the token only to the specified HTTPS origin, disallows
cross-origin redirects and checks:

1. `GET /healthz` returns healthy;
2. anonymous `GET /v1/diagnostics` returns `401`;
3. authenticated `GET /v1/diagnostics` returns Debian 13 diagnostics;
4. authenticated MCP `initialize`;
5. authenticated MCP `tools/list`;
6. read-only MCP `debian13_diagnostics` tool call.

A local `http://127.0.0.1:PORT` origin is permitted for CI tests.
Any other plain HTTP URL is refused. The probe does **not** create tasks,
open a shell, start agents, merge PRs or make purchases.

Alternatively, set GitHub repository variable
`DEBIAN13_REMOTE_BASE_URL` and Actions secret
`DEBIAN13_REMOTE_API_TOKEN`, then manually dispatch the
`Debian 13 remote MCP connection check` workflow from GitHub Actions.
This workflow will fail honestly until the remote service is reachable
and both values are configured. It does not deploy services and is not
scheduled.

## When connected

Record the verified HTTPS origin and GitHub run URL, then configure the
ChatGPT MCP client. Keep the original Alex handoff opt-in and
capability-gated; do not substitute a mock agent for a real worker.
