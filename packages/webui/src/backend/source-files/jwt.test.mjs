import assert from 'node:assert/strict';
import test from 'node:test';
import crypto from 'node:crypto';
import { createFileJwt, verifyFileJwt } from './jwt.ts';

const secret = 'super-secret-key-that-is-at-least-32-bytes-long';

test('createFileJwt generates standard 3-part HS256 token', () => {
  const token = createFileJwt({
    path: 'reports/nasa.pdf',
    principal: 'user-42',
    secret,
    expiresInSeconds: 600,
    now: () => 1000,
  });
  assert.equal(token.split('.').length, 3);
  const result = verifyFileJwt({
    token,
    secret,
    expectedPath: 'reports/nasa.pdf',
    now: () => 1000,
  });
  assert.equal(result.valid, true);
  assert.equal(result.payload?.sub, 'user-42');
  assert.equal(result.payload?.path, 'reports/nasa.pdf');
  assert.equal(result.payload?.iss, 'webui');
  assert.equal(result.payload?.aud, 'fileserver');
  assert.equal(result.payload?.exp, 1600);
});

test('verifyFileJwt rejects invalid audience or issuer', () => {
  const customPayload = Buffer.from(JSON.stringify({
    sub: 'user-42',
    path: 'reports/nasa.pdf',
    iss: 'rogue-issuer',
    aud: 'fileserver',
    exp: 2000, iat: 1000,
  })).toString('base64url');
  const header = Buffer.from(JSON.stringify({ alg: 'HS256', typ: 'JWT' })).toString('base64url');
  const sig = crypto.createHmac('sha256', secret).update(`${header}.${customPayload}`).digest('base64url');
  const badIss = `${header}.${customPayload}.${sig}`;

  assert.equal(verifyFileJwt({ token: badIss, secret, now: () => 1000 }).reason, 'tampered');

  const badAudPayload = Buffer.from(JSON.stringify({
    sub: 'user-42',
    path: 'reports/nasa.pdf',
    iss: 'webui',
    aud: 'other-service',
    exp: 2000, iat: 1000,
  })).toString('base64url');
  const sigAud = crypto.createHmac('sha256', secret).update(`${header}.${badAudPayload}`).digest('base64url');
  const badAud = `${header}.${badAudPayload}.${sigAud}`;

  assert.equal(verifyFileJwt({ token: badAud, secret, now: () => 1000 }).reason, 'tampered');
});

test('verifyFileJwt accurately identifies expired token with valid signature', () => {
  const token = createFileJwt({
    path: 'reports/nasa.pdf',
    principal: 'user-42',
    secret,
    expiresInSeconds: 300,
    now: () => 1000, // exp is 1300
  });

  // Valid at 1200
  assert.equal(
    verifyFileJwt({ token, secret, expectedPath: 'reports/nasa.pdf', now: () => 1200 }).valid,
    true,
  );

  // Expired at 1301
  const expired = verifyFileJwt({
    token,
    secret,
    expectedPath: 'reports/nasa.pdf',
    now: () => 1301,
  });
  assert.equal(expired.valid, false);
  assert.equal(expired.reason, 'expired');
  assert.equal(expired.payload?.sub, 'user-42');
});

test('verifyFileJwt rejects tampered signature as tampered (not expired)', () => {
  const token = createFileJwt({
    path: 'reports/nasa.pdf',
    principal: 'user-42',
    secret,
    expiresInSeconds: 100, // already expired, but tampered
    now: () => 1000,
  });
  const tampered = token.slice(0, -6) + 'xxxxxx';
  const result = verifyFileJwt({
    token: tampered,
    secret,
    expectedPath: 'reports/nasa.pdf',
    now: () => 1000,
  });
  assert.equal(result.valid, false);
  assert.equal(result.reason, 'tampered');
});

test('verifyFileJwt rejects unauthorized path', () => {
  const token = createFileJwt({
    path: 'reports/nasa.pdf',
    principal: 'user-42',
    secret,
    expiresInSeconds: 600,
    now: () => 1000,
  });
  const wrongPath = verifyFileJwt({
    token,
    secret,
    expectedPath: 'confidential/secret.pdf',
    now: () => 1000,
  });
  assert.equal(wrongPath.valid, false);
  assert.equal(wrongPath.reason, 'forbidden_path');
});

test('JWT signing rejects directory capabilities, malformed paths and unbounded lifetimes', () => {
  for (const path of ['dir/', '../secret.pdf', '/file.pdf', 'a//b.pdf', 'a/.cache/b.pdf', 'a/file.pdf.pdfparsecache/x'])
    assert.throws(() => createFileJwt({path, principal:'u', secret}));
  for (const expiresInSeconds of [0, -1, 3601, NaN]) assert.throws(() => createFileJwt({path:'a.pdf', principal:'u',secret,expiresInSeconds}));
  const token = createFileJwt({path:'a.pdf',principal:'u',secret,now:()=>1000,expiresInSeconds:100});
  assert.equal(verifyFileJwt({token,secret,now:()=>1100}).reason, 'expired');
});
