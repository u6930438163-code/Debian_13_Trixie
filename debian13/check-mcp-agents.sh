#!/usr/bin/env bash
# Fast, non-destructive readiness check for MCP and the four original agents.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"
echo "== Debian 13: runtime prerequisites =="
bash start-local.sh --check
echo "== MCP JSON-RPC protocol =="
python3 - "$ROOT" <<'PY'
import json, subprocess, sys
root=sys.argv[1]
requests=[
 {"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-11-25","capabilities":{},"clientInfo":{"name":"debian13-check","version":"1"}}},
 {"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}}
]
p=subprocess.run([sys.executable,root+"/system/mcp_server.py"],input="".join(json.dumps(r)+"\n" for r in requests),text=True,capture_output=True,timeout=12)
if p.returncode: raise SystemExit("FAIL: MCP process did not start")
lines=[json.loads(line) for line in p.stdout.splitlines() if line.strip()]
if len(lines)!=2 or any("error" in x for x in lines): raise SystemExit("FAIL: MCP handshake")
tools=lines[1]["result"]["tools"]
print("PASS: MCP stdio initialize + tools/list ("+str(len(tools))+" tools)")
print("NOTE: MCP tools are currently read-only; this is NOT proof of agent connectivity.")
PY
echo "== Original agents =="
python3 - "$ROOT" <<'PY'
import sys
sys.path.insert(0,sys.argv[1]+"/system")
from original_agents import report
data=report()
print("Registry configured:",data["registryConfigured"])
print("Registry reachable:",data["registryReachable"])
for agent in data["agents"]:
    print(agent["name"]+": worker verified="+str(agent["workerVerified"]))
print("NOTE: Real agent chat requires independently running workers and provider credentials.")
PY
echo "== Start UI/API =="
echo "Run: bash debian13/start-local.sh (from repository root)"
