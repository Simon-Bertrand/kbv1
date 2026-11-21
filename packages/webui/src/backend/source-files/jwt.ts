import { createHmac, timingSafeEqual } from 'node:crypto';

export interface FileJwtPayload {
  sub: string;
  path: string;
  request_id?: string;
  iss: 'webui';
  aud: 'fileserver';
  exp: number;
  iat: number;
}

export function filePath(value: unknown): string {
  if (typeof value !== 'string' || !value || value.length > 4096 || /[\\\x00-\x1f\x7f]/.test(value) ||
      value.split('/').some((part) => !part || part === '.' || part === '..' || part.startsWith('.') || part.includes('.pdfparsecache')))
    throw new Error('Invalid relative file path');
  return value;
}

export type FileJwtFailureReason =
  | 'missing'
  | 'malformed'
  | 'tampered'
  | 'expired'
  | 'forbidden_path';

export interface FileJwtVerificationResult {
  valid: boolean;
  reason?: FileJwtFailureReason;
  payload?: FileJwtPayload;
}

function toBase64Url(buf: Buffer | string): string {
  const b = typeof buf === 'string' ? Buffer.from(buf, 'utf8') : buf;
  return b.toString('base64').replace(/=/g, '').replace(/\+/g, '-').replace(/\//g, '_');
}

function fromBase64Url(str: string): string {
  let base64 = str.replace(/-/g, '+').replace(/_/g, '/');
  while (base64.length % 4) base64 += '=';
  return Buffer.from(base64, 'base64').toString('utf8');
}

function signHmac(data: string, secret: string): string {
  return toBase64Url(createHmac('sha256', secret).update(data).digest());
}

function safeEqual(a: string, b: string): boolean {
  const bufA = Buffer.from(a);
  const bufB = Buffer.from(b);
  if (bufA.length !== bufB.length) return false;
  return timingSafeEqual(bufA, bufB);
}

/**
 * Creates an HMAC-SHA256 signed JWT for accessing a specific file in fileserver.
 */
export function createFileJwt({
  path,
  principal,
  requestId,
  secret,
  expiresInSeconds = 1800,
  now = () => Math.floor(Date.now() / 1000),
}: {
  path: string;
  principal: string;
  requestId?: string;
  secret: string;
  expiresInSeconds?: number;
  now?: () => number;
}): string {
  const cleanPath = filePath(path);
  const currentTime = now();
  if (!principal.trim() || principal.length > 256 || secret.length < 32 ||
      !Number.isSafeInteger(currentTime) || !Number.isSafeInteger(expiresInSeconds) || expiresInSeconds < 1 || expiresInSeconds > 3600)
    throw new Error('Invalid file token configuration');
  const header = toBase64Url(JSON.stringify({ alg: 'HS256', typ: 'JWT' }));
  const payload = toBase64Url(
    JSON.stringify({
      sub: principal,
      ...(requestId && /^[A-Za-z0-9._-]{1,128}$/.test(requestId) ? { request_id: requestId } : {}),
      path: cleanPath,
      iss: 'webui',
      aud: 'fileserver',
      exp: currentTime + expiresInSeconds,
      iat: currentTime,
    }),
  );
  const signature = signHmac(`${header}.${payload}`, secret);
  return `${header}.${payload}.${signature}`;
}

/**
 * Verifies a file JWT and distinguishes expired tokens from tampered tokens.
 */
export function verifyFileJwt({
  token,
  secret,
  expectedPath,
  now = () => Math.floor(Date.now() / 1000),
}: {
  token: string | undefined | null;
  secret: string;
  expectedPath?: string;
  now?: () => number;
}): FileJwtVerificationResult {
  if (!token || typeof token !== 'string') {
    return { valid: false, reason: 'missing' };
  }

  const parts = token.trim().split('.');
  if (parts.length !== 3) {
    return { valid: false, reason: 'malformed' };
  }

  // 1. Signature must be verified first
  const expectedSig = signHmac(`${parts[0]}.${parts[1]}`, secret);
  if (!safeEqual(parts[2]!, expectedSig)) {
    return { valid: false, reason: 'tampered' };
  }

  // 2. Decode payload
  let payload: FileJwtPayload;
  try {
    const header = JSON.parse(fromBase64Url(parts[0]!));
    if (header.alg !== 'HS256' || header.typ !== 'JWT') return { valid: false, reason: 'malformed' };
    payload = JSON.parse(fromBase64Url(parts[1]!));
    if (!payload || typeof payload !== 'object' || Array.isArray(payload)) {
      return { valid: false, reason: 'malformed' };
    }
  } catch {
    return { valid: false, reason: 'malformed' };
  }

  try { filePath(payload.path); }
  catch { return { valid: false, reason: 'malformed' }; }
  if (typeof payload.sub !== 'string' || !payload.sub.trim() || payload.sub.length > 256 ||
      !Number.isSafeInteger(payload.exp) || !Number.isSafeInteger(payload.iat))
    return { valid: false, reason: 'malformed' };

  // 3. Path boundary check
  if (expectedPath) {
    if (payload.path !== expectedPath) {
      return { valid: false, reason: 'forbidden_path', payload };
    }
  }

  // 4. Issuer and Audience check (must be issued by webui for fileserver if present)
  if (payload.iss !== 'webui') {
    return { valid: false, reason: 'tampered', payload };
  }
  if (payload.aud !== 'fileserver') {
    return { valid: false, reason: 'tampered', payload };
  }

  // 5. Expiration check AFTER signature verification
  const current = now();
  if (payload.exp <= current) {
    return { valid: false, reason: 'expired', payload };
  }
  if (payload.iat < 0 || payload.iat > current + 60 || payload.exp <= payload.iat || payload.exp - payload.iat > 3600)
    return { valid: false, reason: 'malformed', payload };

  return { valid: true, payload };
}
