# Operator-approved root actions for ORIGINAL AI Studio agents

**Scope:** `u6930438163-code/Debian_13_Trixie`. The agents remain the
owner's original Alex, Aria, Astyria and Aurelius in Google AI Studio.

## What is — and is not — root

The API and model-facing services remain **UID 10001**, not root. Only the
optional Docker Compose profile `root-admin` runs a separate Python process
as **UID 0 inside an isolated container**. It has no network, no Docker
socket, no host root mount, read-only container filesystem, reduced CPU/RAM,
dropped Linux capabilities except `CHOWN`, and `no-new-privileges`.

This does **not** grant root privileges on the Codespaces host, physical
computer, GitHub, AI Studio, or OpenAI. No model-originated free-form
command, `sudo` string, shell invocation, package install, or arbitrary
filesystem path is executed.

An operator can create a narrowly scoped proposal on behalf of one of
the four original agents. It remains `pending_approval` until independently
approved for that **exact command ID**. It can then be executed once by
the container-root runner, if enabled.

Allowlisted command IDs:

- `identity` → `id`.
- `kernel` → `uname -a`.
- `package_audit` → `dpkg --audit`.
- `workspace_disk` → `df -h /workspace`.
- `workspace_permissions` → `stat` on `/workspace`.
- `repair_workspace_owner` → fixed `chown` to UID/GID 10001 for
  the exact workspace directory and the two known database files;
  symlinks are skipped. No recursive traversal.

Every operation has a persistent SQLite audit record. A heartbeat from
the root container is required before the UI claims it is alive.
An interrupted root action is marked `unknown_after_restart` rather
than automatically repeated.

## Activation (private deployment)

1. Review the PR diff and the runner's command allowlist.
2. Add `DEBIAN13_ROOT_APPROVAL_TOKEN` as a separate random 32+ character
   runtime secret **not shared with AI agents**.
3. Set `DEBIAN13_ROOT_ACTIONS_ENABLED=1` for the main Debian API service.
4. Run on the private container host from the repository root:

```bash
export DEBIAN13_API_TOKEN="$(python3 -c 'import secrets; print(secrets.token_urlsafe(48))')"
export DEBIAN13_ROOT_APPROVAL_TOKEN="$(python3 -c 'import secrets; print(secrets.token_urlsafe(48))')"
export DEBIAN13_ROOT_ACTIONS_ENABLED=1
docker compose -f debian13/system/compose.yaml --profile root-admin up -d --build
docker compose -f debian13/system/compose.yaml --profile root-admin ps
```

These commands create new operator tokens for a new deployment. If the
existing system already uses a token or persistent secret, **reuse the
existing protected token** instead of invalidating active clients. Do
not run this command with `set -x`, paste tokens into chats, or commit
them to GitHub. Apply secrets through the private host or Codespaces
secrets configuration.

In the Work tab create a command proposal with its agent and reason,
then enter the separate root approval secret and approve that exact
item. Approval does not mean the runner executed it; check the item's
status/output, and verify the `rootRunnerVerified` heartbeat.

## API, behind the existing private operator bearer

- `GET /api/v1/admin/commands` — allowlist + runner heartbeat.
- `GET /api/v1/admin/operations` — durable audit list.
- `POST /api/v1/admin/proposals` — `{"agent":"Alex","commandId":"package_audit","reason":"audit before maintenance"}`
- `POST /api/v1/admin/operations/<uuid>/approve` — JSON
  `{"confirmation":"ZATWIERDZAM ROOT W KONTENERZE"}`, plus separate
  request header `X-Root-Approval-Token`.

The existing authenticated MCP endpoint also advertises the narrowly typed
`propose_root_command(agent, command_id, reason)` tool. It only creates a
`pending_approval` proposal through the same API; it **cannot** approve,
execute or bypass the root runner's restrictions. The original AI Studio
agent must be connected as an MCP client or the operator may submit the
proposal on its behalf using Work. Merely listing this tool does not
mean the original AI can invoke it.

No API endpoint takes arbitrary shell commands. The operator bearer
is required for **all** of these endpoints. The root approval header
is additionally required for the consequential approval endpoint.

## Original worker prerequisite

The source implementation is independently proposed in
`u6930438163-code/AI_Studio_BastillaOS` PR #18. The Debian MCP bridge
in PR #5 / PR #26 can check real source status via authenticated
`GET /api/agent-tasks/workers` and inspect task results via
`GET /api/agent-tasks/jobs/:id`. That integration currently delivers
model-generated **reviews**, not autonomous GitHub commits or root jobs.

A profile in GitHub is not a running worker. An original model with
`idle` status is configured and waiting for model tasks but has no
active execution. `running` requires the source process to report
a current heartbeat, and `completed` requires a persisted real
model response. Running the app in Google AI Studio preview alone does
not provide a guaranteed 24/7 runtime.

## Validation

Run the tests before deployment:

```bash
python3 -m unittest discover -s debian13/system/tests -p 'test_*.py' -v
node --test debian13/ui/dev_server.test.mjs
docker compose -f debian13/system/compose.yaml config --quiet
```

The tests use local mocks and do not prove that the remote AI Studio
service was deployed or that an administrator approved any real command.
