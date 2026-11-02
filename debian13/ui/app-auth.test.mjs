import test from 'node:test';
import assert from 'node:assert/strict';
import { createApiClient, ApiError } from './app.mjs';

const token = 'correct-operator-token-32-characters-minimum';
const reply = (status, payload) => ({ status, ok: status >= 200 && status < 300,
  json: async () => payload });

test('hosted UI prompts once after 401 and keeps credential in memory only', async () => {
  const sent = [];
  let prompts = 0;
  const api = createApiClient({
    fetchImpl: async (url, options) => {
      sent.push({ url, auth: options.headers.Authorization });
      return options.headers.Authorization === 'Bearer ' + token
        ? reply(200, { application: { status: 'running' } })
        : reply(401, { error: { code: 'unauthorized' } });
    },
    requestOperatorToken: () => { prompts++; return token; },
  });
  assert.equal((await api.getStatus()).application.status, 'running');
  assert.equal((await api.getStatus()).application.status, 'running');
  assert.equal(prompts, 1);
  assert.deepEqual(sent.map(x => x.auth), [undefined, 'Bearer ' + token, 'Bearer ' + token]);
  assert.deepEqual(sent.map(x => x.url), ['/api/v1/status', '/api/v1/status', '/api/v1/status']);
});

test('cancelling prompt does not send a guessed token or retry', async () => {
  let calls = 0;
  const api = createApiClient({
    fetchImpl: async () => { calls++; return reply(401, { error: 'auth' }); },
    requestOperatorToken: () => null,
  });
  await assert.rejects(api.getStatus(), (err) => err instanceof ApiError && err.status === 401);
  assert.equal(calls, 1);
});
