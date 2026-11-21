import {readJson} from '../http/json';
import { ChatError, createChatService } from './service';
import copy from '../../content/fr.json';
import type { RateLimiter } from '../http/rate-limiter';
import { setRequestUser, withRequestContext } from '../logger';

interface Options {
  publicOrigin?: string;
  authenticate: () => Promise<{ id: string; name?: string | null; email?: string | null } | null>;
  service: ReturnType<typeof createChatService>;
  rateLimiter?: RateLimiter | null;
}
import { createRateLimiter } from '../http/rate-limiter';
const accessLimiter = createRateLimiter({scope:'chat-access',rps:10,burst:30});
const headers = { 'Cache-Control': 'private, no-store', 'X-Content-Type-Options': 'nosniff' };
export function createChatHandlers({ authenticate, service, publicOrigin, rateLimiter }: Options) {
  async function handle(request: Request, action: (principal: string) => Promise<Response>) {
    return withRequestContext(request, null, async () => {
      try {
        const principal = await authenticate();
        if (!principal) throw new ChatError(401, copy.agentChat.unauthorized);
        setRequestUser(principal);
        const budget=await (accessLimiter.consumeAsync?.(principal.id) ?? accessLimiter.consume(principal.id));
        if (!budget.allowed) return new Response(null,{status:429,headers:{...headers,'Retry-After':String(budget.retryAfter)}});
        return await action(principal.id);
      } catch (error) {
        if (error instanceof ChatError)
          return Response.json({ error: error.message }, { status: error.status, headers });
        return Response.json({ error: copy.agentChat.unavailable }, { status: 503, headers });
      }
    });
  }
  return {
    start: (request: Request) => handle(request, async (principal) => {
      const requestOrigin = request.headers.get('origin');
      if ((requestOrigin && requestOrigin !== new URL(publicOrigin ?? request.url).origin) ||
          request.headers.get('sec-fetch-site') === 'cross-site')
        return new Response(null, { status: 403, headers });
      if (request.headers.get('content-type')?.split(';')[0]?.trim() !== 'application/json')
        return new Response(null, { status: 415, headers });
      const data=await readJson(request.body,100000,()=>new ChatError(400,copy.agentChat.invalidRequest),()=>new ChatError(413,copy.agentChat.invalidRequest));

      if (rateLimiter) {
        const check = await (rateLimiter.consumeAsync?.(principal) ?? rateLimiter.consume(principal));
        if (!check.allowed) {
          return Response.json(
            { error: copy.agentChat.rateLimited },
            {
              status: 429,
              headers: {
                ...headers,
                'Retry-After': String(check.retryAfter),
                'X-RateLimit-Limit': '1',
                'X-RateLimit-Remaining': '0',
              },
            },
          );
        }
      }

      return Response.json(await service.start(data, principal, request.signal), { status: 202, headers });
    }),
    fast: (request: Request) => handle(request, async (principal) => {
      const requestOrigin = request.headers.get('origin');
      if ((requestOrigin && requestOrigin !== new URL(publicOrigin ?? request.url).origin) ||
          request.headers.get('sec-fetch-site') === 'cross-site')
        return new Response(null, { status: 403, headers });
      if (request.headers.get('content-type')?.split(';')[0]?.trim() !== 'application/json')
        return new Response(null, { status: 415, headers });
      const data = await readJson(request.body, 100000, () => new ChatError(400, copy.agentChat.invalidRequest), () => new ChatError(413, copy.agentChat.invalidRequest));

      if (rateLimiter) {
        const check = await (rateLimiter.consumeAsync?.(principal) ?? rateLimiter.consume(principal));
        if (!check.allowed) {
          return Response.json(
            { error: copy.agentChat.rateLimited },
            {
              status: 429,
              headers: {
                ...headers,
                'Retry-After': String(check.retryAfter),
                'X-RateLimit-Limit': '1',
                'X-RateLimit-Remaining': '0',
              },
            },
          );
        }
      }

      const stream = await service.fastStream(data, principal, request.signal);
      return new Response(stream, {
        headers: {
          'Content-Type': 'text/event-stream; charset=utf-8',
          'Cache-Control': 'private, no-cache, no-store, no-transform',
          'Connection': 'keep-alive',
          'X-Accel-Buffering': 'no',
          'X-Content-Type-Options': 'nosniff',
        },
      });
    }),
    read: (request: Request, token: string) => handle(request, async (principal) =>
      Response.json(await service.read(token, principal, request.signal), { headers })),
    events: (request: Request, token: string) => handle(request, async (principal) => {
      const requestOrigin = request.headers.get('origin');
      if ((requestOrigin && requestOrigin !== new URL(publicOrigin ?? request.url).origin) ||
          request.headers.get('sec-fetch-site') === 'cross-site')
        return new Response(null, { status: 403, headers });

      service.authorizeToken(token, principal);

      const url = new URL(request.url);
      const rawCursor = request.headers.get('last-event-id') || url.searchParams.get('after') || undefined;
      if (rawCursor !== undefined) {
        if (!/^\d+$/.test(rawCursor)) {
          return Response.json({ error: copy.agentChat.invalidRequest }, { status: 400, headers });
        }
        const parsed = Number(rawCursor);
        if (!Number.isSafeInteger(parsed) || parsed < 0 || parsed > Number.MAX_SAFE_INTEGER) {
          return Response.json({ error: copy.agentChat.invalidRequest }, { status: 400, headers });
        }
      }

      const stream = service.eventsStream(token, principal, request.signal, rawCursor);
      return new Response(stream, {
        headers: {
          'Content-Type': 'text/event-stream; charset=utf-8',
          'Cache-Control': 'private, no-cache, no-store, no-transform',
          'Connection': 'keep-alive',
          'X-Accel-Buffering': 'no',
          'X-Content-Type-Options': 'nosniff',
        },
      });
    }),
    file: (request: Request, token: string, sourceId: string) => handle(request, (principal) =>
      service.file(request, token, sourceId, principal)),
  };
}
