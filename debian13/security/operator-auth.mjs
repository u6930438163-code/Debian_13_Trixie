import { timingSafeEqual } from 'node:crypto';

/** A fixed-size operator token is never compared with ordinary string equality. */
export function hasBearerCredential(authorization, token) {
  if (typeof authorization !== 'string' || typeof token !== 'string'
      || token.length < 32 || authorization.length > 1024) return false;
  const received = Buffer.from(authorization, 'utf8');
  const expected = Buffer.from('Bearer ' + token, 'utf8');
  return received.length === expected.length && timingSafeEqual(received, expected);
}

/** Do not trust X-Forwarded-For for the internal model provider bridge. */
export function isInternalInferenceAuthorized(authorization, token, remoteAddress) {
  const loopback = remoteAddress === '127.0.0.1'
    || remoteAddress === '::1'
    || remoteAddress === '::ffff:127.0.0.1';
  return loopback && hasBearerCredential(authorization, token);
}
