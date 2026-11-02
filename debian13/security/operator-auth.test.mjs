import test from 'node:test';
import assert from 'node:assert/strict';
import { hasBearerCredential, isInternalInferenceAuthorized } from './operator-auth.mjs';

const token = 'example-operator-token-is-long-enough-for-test';

test('operator bearer credential accepts only the exact token', () => {
  assert.equal(hasBearerCredential('Bearer ' + token, token), true);
  for (const credential of ['', 'Bearer wrong', 'Basic ' + token, 'bearer ' + token,
    'Bearer ' + token + 'x', 'Bearer  ' + token, null, 'x'.repeat(1025)]) {
    assert.equal(hasBearerCredential(credential, token), false);
  }
  assert.equal(hasBearerCredential('Bearer short', 'short'), false);
});

test('internal model bridge requires both a distinct token and loopback socket', () => {
  assert.equal(isInternalInferenceAuthorized('Bearer ' + token, token, '127.0.0.1'), true);
  assert.equal(isInternalInferenceAuthorized('Bearer ' + token, token, '::1'), true);
  assert.equal(isInternalInferenceAuthorized('Bearer ' + token, token, '::ffff:127.0.0.1'), true);
  assert.equal(isInternalInferenceAuthorized('Bearer ' + token, token, '198.51.100.7'), false);
  assert.equal(isInternalInferenceAuthorized('Bearer ' + token, token, undefined), false);
  assert.equal(isInternalInferenceAuthorized('Bearer wrong', token, '127.0.0.1'), false);
});
