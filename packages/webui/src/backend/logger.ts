import { AsyncLocalStorage } from 'node:async_hooks';
import { createHash, randomUUID } from 'node:crypto';
import { isIP } from 'node:net';

export const actorId = (value: string) => createHash('sha256').update(value).digest('hex');
type LogUser = string | { id?: string; name?: string | null; email?: string | null } | null;
type Context = { request_id: string; client_ip: string | null; user: string | null; principal: string | null };
const context = new AsyncLocalStorage<Context>();
const ids = new WeakMap<Request, string>();
const validId = /^[A-Za-z0-9._-]{1,128}$/;

export function requestId(request: Request): string {
  const cached = ids.get(request);
  if (cached) return cached;
  const incoming = request.headers.get('x-request-id') ?? '';
  const id = validId.test(incoming) ? incoming : randomUUID();
  ids.set(request, id);
  return id;
}

function account(user: LogUser) {
  const principal = (typeof user === 'string' ? user : user?.id || user?.email || user?.name)?.trim() || null;
  const display = (typeof user === 'string' ? user : user?.name || user?.email || user?.id)?.trim();
  const name = display && display.length <= 256 && !/\p{C}/u.test(display) ? display : principal ? actorId(principal) : null;
  return { principal, user: name };
}

export function setRequestUser(user: LogUser) {
  const value = context.getStore();
  if (value) Object.assign(value, account(user));
}

export function currentRequestId() {
  return context.getStore()?.request_id;
}

export async function withRequestContext(request: Request, user: LogUser, action: () => Promise<Response>): Promise<Response> {
  const value = { request_id: requestId(request), client_ip: trustedClientIp(request), ...account(user) };
  return context.run(value, async () => {
    const start = performance.now();
    let status = 500;
    try {
      const response = await action();
      status = response.status;
      const headers = new Headers(response.headers);
      headers.set('X-Request-Id', value.request_id);
      return new Response(response.body, { status, statusText: response.statusText, headers });
    } catch (error) {
      if (request.signal.aborted) status = 499;
      if (error && typeof error === 'object' && 'status' in error && typeof error.status === 'number') status = error.status;
      throw error;
    } finally {
      const path = new URL(request.url).pathname
        .replace(/\/runs\/[^/]+/, '/runs/[run]')
        .replace(/\/conversations\/[^/]+/, '/conversations/[id]')
        .replace(/\/sources\/[^/]+/, '/sources/[source]');
      log(status >= 500 ? 'ERROR' : 'INFO', `${request.method} ${path}`,
        { method: request.method, path, status_code: status, duration_ms: performance.now() - start });
    }
  });
}

function applyContext(headers: Headers, value: Context) {
  headers.set('X-Request-Id', value.request_id);
  if (value.client_ip) headers.set('X-Real-IP', value.client_ip);
  else headers.delete('X-Real-IP');
  if (value.user) headers.set('X-KB-Log-User', encodeURIComponent(value.user));
  else headers.delete('X-KB-Log-User');
  headers.set('X-Authenticated-User', actorId(value.principal ?? 'anonymous'));
  return headers;
}

export function upstreamHeaders(extra?: HeadersInit): Headers {
  const headers = new Headers(extra);
  const value = context.getStore();
  if (value) return applyContext(headers, value);
  const id = headers.get('x-request-id') ?? '';
  headers.set('X-Request-Id', validId.test(id) ? id : randomUUID());
  return headers;
}

export type LogLevel = 'INFO' | 'WARNING' | 'ERROR' | 'DEBUG' | 'WARN';
const pending: string[] = [];
let flushScheduled = false;
let dropped = 0;
const maxQueueSize = 10_000;

function flush() {
  while (pending.length) {
    const line = pending.shift()!;
    if (!process.stdout.write(line)) {
      process.stdout.once('drain', flush);
      return;
    }
  }
  flushScheduled = false;
  if (dropped) {
    const count = dropped;
    dropped = 0;
    log('WARNING', `Log queue full: ${count} records dropped`, { logger: 'logging' });
  }
}

export function log(level: LogLevel, message: string, extra?: {
  request_id?: string; client_ip?: string; user?: string; duration_ms?: number; status_code?: number;
  logger?: string; [key: string]: unknown;
}) {
  const value = context.getStore();
  const payload = {
    timestamp: new Date().toISOString(),
    level: level === 'WARN' ? 'WARNING' : level,
    service: 'webui',
    logger: extra?.logger ?? 'backend',
    message,
    request_id: value?.request_id ?? null,
    client_ip: value?.client_ip ?? null,
    user: value?.user ?? null,
    ...extra,
  };
  if (pending.length >= maxQueueSize) {
    dropped += 1;
    return;
  }
  pending.push(JSON.stringify(payload) + '\n');
  if (!flushScheduled) {
    flushScheduled = true;
    setImmediate(flush);
  }
}

/** The trusted HTTP ingress must overwrite X-Real-IP and X-Forwarded-For. */
export function getClientIp(request: Request, trustHost = process.env.WEBUI_TRUST_PROXY === 'true'): string {
  if (trustHost) {
    const real = request.headers.get('x-real-ip')?.trim();
    if (real && isIP(real)) return real;
    const forwarded = request.headers.get('x-forwarded-for')?.split(',').at(-1)?.trim();
    if (forwarded && isIP(forwarded)) return forwarded;
  }
  return 'unknown';
}

function trustedClientIp(request: Request) {
  const ip = getClientIp(request);
  return isIP(ip) ? ip : null;
}

export function createUpstreamHeaders(request: Request, user: LogUser = null, extra?: Record<string, string>): Headers {
  return applyContext(new Headers(extra), {
    request_id: requestId(request), client_ip: trustedClientIp(request), ...account(user),
  });
}
