import {actorId} from './logger.ts';
import test from 'node:test';
import assert from 'node:assert/strict';
import { getClientIp, createUpstreamHeaders } from './logger.js';

test('anti-usurpation: X-Authenticated-User is strictly derived from session and ignores forged client headers', () => {
  // Attacker attempts to forge X-Authenticated-User: admin
  const forgedRequest = new Request('http://localhost:3000/api/chat', {
    headers: {
      'x-authenticated-user': 'forged-admin@evil.com',
      'x-real-ip': '8.8.8.8',
      'x-request-id': 'client-req-1234',
    },
  });

  // Legitimate session is user "alice@corp.local"
  const legitimateUser = { id: 'usr-1', email: 'alice@corp.local', name: 'Alice' };

  const upstreamHeaders = createUpstreamHeaders(forgedRequest, legitimateUser);

  // MUST be alice, NOT the forged-admin
  assert.equal(upstreamHeaders.get('X-Authenticated-User'), actorId('usr-1'));
  assert.equal(upstreamHeaders.get('X-Request-Id'), 'client-req-1234');
  assert.equal(upstreamHeaders.get('X-KB-Log-User'), 'Alice');
});

test('anti-usurpation: anonymous session sets anonymous even if client forged an authenticated user header', () => {
  const forgedRequest = new Request('http://localhost:3000/api/chat', {
    headers: {
      'x-authenticated-user': 'root@system',
    },
  });

  const upstreamHeaders = createUpstreamHeaders(forgedRequest, null);

  // MUST be anonymous
  assert.equal(upstreamHeaders.get('X-Authenticated-User'), actorId('anonymous'));
});

test('client IP resolution requires a trusted proxy', () => {
  const req = new Request('http://localhost:3000/api/chat', {
    headers: {
      'x-forwarded-for': '203.0.113.195, 70.41.3.18, 150.172.238.178',
      'x-real-ip': '10.0.0.1',
    },
  });

  // Without trustHost -> does not trust X-Forwarded-For
  assert.equal(getClientIp(req, false), 'unknown');

  // The proxy overwrites X-Real-IP with the validated client address.
  assert.equal(getClientIp(req, true), '10.0.0.1');
});


test('long Unicode identities are representable without truncation in service headers',()=>{
 const identity='https://issuer/:'+'用户'.repeat(1000);
 const headers=createUpstreamHeaders(new Request('http://localhost'),identity);
 assert.equal(headers.get('X-Authenticated-User'),actorId(identity));
 assert.match(headers.get('X-Authenticated-User'),/^[a-f0-9]{64}$/);
});
