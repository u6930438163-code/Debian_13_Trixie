import test from 'node:test';
import assert from 'node:assert/strict';
import { createServer } from 'node:net';
import { spawn } from 'node:child_process';
import { once } from 'node:events';
import { mkdtemp, rm } from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';

const token = 'integration-test-operator-token-32-characters';
const root = path.resolve(import.meta.dirname, '../..');

async function unusedPort() {
  const server = createServer();
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const port = server.address().port;
  await new Promise(resolve => server.close(resolve));
  return port;
}

test('hosted Express gateway protects API, GPT Actions, MCP and model inference', { timeout: 35000 }, async t => {
  const tmp = await mkdtemp(path.join(os.tmpdir(), 'debian13-gateway-'));
  const port = await unusedPort();
  const apiPort = await unusedPort();
  const proc = spawn(process.execPath, ['--import', 'tsx', 'server.ts'], {
    cwd: root,
    env: {
      ...process.env, PORT: String(port), DEBIAN13_API_PORT: String(apiPort),
      DEBIAN13_API_TOKEN: token, DEBIAN13_STATE_DIR: path.join(tmp, 'state'),
      WORKSPACE_DIR: path.join(tmp, 'workspace'), GEMINI_API_KEY: '',
      MODEL_CHAT_URL: '', MODEL_API_KEY: '', MODEL_NAME: '',
    },
    stdio: 'ignore',
  });
  t.after(async () => {
    if (proc.exitCode === null) {
      const exited = once(proc, 'exit');
      proc.kill('SIGTERM');
      await Promise.race([exited, new Promise(resolve => setTimeout(resolve, 2000))]);
    }
    await rm(tmp, { recursive: true, force: true });
  });
  const url = 'http://127.0.0.1:' + port;
  let ready = false;
  for (let i = 0; i < 100; i++) {
    if (proc.exitCode !== null) break;
    try {
      const response = await fetch(url + '/healthz', { signal: AbortSignal.timeout(400) });
      if (response.status === 200) { ready = true; break; }
    } catch {}
    await new Promise(resolve => setTimeout(resolve, 150));
  }
  assert.equal(ready, true, 'Express and Python backend must become ready');

  const noAuth = await fetch(url + '/api/v1/status');
  assert.equal(noAuth.status, 401);
  const noTask = await fetch(url + '/api/v1/tasks', {
    method: 'POST', headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ agent: 'alex', kind: 'development', title: 'fail', description: 'reject' }),
  });
  assert.equal(noTask.status, 401);
  const valid = await fetch(url + '/api/v1/status', { headers: { authorization: 'Bearer ' + token } });
  assert.equal(valid.status, 200);

  assert.equal((await fetch(url + '/v1/diagnostics')).status, 401);
  assert.equal((await fetch(url + '/v1/diagnostics', { headers: { authorization: 'Bearer ' + token } })).status, 200);
  assert.equal((await fetch(url + '/mcp', { method: 'POST' })).status, 401);
  const mcp = await fetch(url + '/mcp', {
    method: 'POST',
    headers: { authorization: 'Bearer ' + token, 'content-type': 'application/json',
      accept: 'application/json, text/event-stream' },
    body: JSON.stringify({ jsonrpc: '2.0', id: 1, method: 'initialize', params: {} }),
  });
  assert.equal(mcp.status, 200);
  assert.equal((await mcp.json()).result.serverInfo.name, 'debian13-work-mcp');

  for (const auth of [undefined, 'Bearer ' + token, 'Bearer internal-gemini-token']) {
    const inference = await fetch(url + '/v1/chat/completions', {
      method: 'POST',
      headers: { ...(auth ? { authorization: auth } : {}), 'content-type': 'application/json' },
      body: JSON.stringify({ messages: [{ role: 'user', content: 'test' }] }),
    });
    assert.equal(inference.status, 401, 'operator token must not access private model bridge');
  }
});
