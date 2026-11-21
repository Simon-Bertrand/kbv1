import copy from '../../content/fr.json';
import { readJson } from '../http/json';
import { createSearchService, SearchError } from './service';
import type { RateLimiter } from '../http/rate-limiter';
import { setRequestUser, withRequestContext } from '../logger';

import { createRateLimiter } from '../http/rate-limiter';
const accessLimiter = createRateLimiter({scope:'search-access',rps:10,burst:30});
const headers = { 'Cache-Control': 'private, no-store', 'X-Content-Type-Options': 'nosniff' };

export function createSearchHandlers(options: {
  authenticate: () => Promise<{ id: string; name?: string | null; email?: string | null } | null>;
  publicOrigin?: string;
  service: ReturnType<typeof createSearchService>;
  rateLimiter?: RateLimiter | null;
}) {
  async function handle(request: Request, action: (principal: string) => Promise<Response>) {
    return withRequestContext(request, null, async () => {
      try {
        const user = await options.authenticate();
        if (!user) throw new SearchError(401, copy.documentSearch.unauthorized);
        setRequestUser(user);
        const budget=await (accessLimiter.consumeAsync?.(user.id) ?? accessLimiter.consume(user.id));
        if (!budget.allowed) return new Response(null,{status:429,headers:{...headers,'Retry-After':String(budget.retryAfter)}});
        return await action(user.id);
      } catch (error) {
        return Response.json({ error: error instanceof SearchError ? error.message : copy.documentSearch.unavailable },
          { status: error instanceof SearchError ? error.status : 503, headers });
      }
    });
  }
  return {
    search: (request: Request) => handle(request, async (principal) => {
      const origin = request.headers.get('origin');
      if ((origin && origin !== new URL(options.publicOrigin ?? request.url).origin) ||
          request.headers.get('sec-fetch-site') === 'cross-site') return new Response(null, { status: 403, headers });
      if (request.headers.get('content-type')?.split(';')[0]?.trim() !== 'application/json')
        return new Response(null, { status: 415, headers });
      const body = await readJson(request.body, 20000,
        () => new SearchError(400, copy.documentSearch.invalidRequest),
        () => new SearchError(413, copy.documentSearch.invalidRequest));

      if (options.rateLimiter) {
        const check = await (options.rateLimiter.consumeAsync?.(principal) ?? options.rateLimiter.consume(principal));
        if (!check.allowed) {
          return Response.json(
            { error: copy.documentSearch.rateLimited },
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

      return Response.json(await options.service.search(body, principal, request.signal), { headers });
    }),
    file: (request: Request, token: string) => handle(request, (principal) => options.service.file(request, token, principal)),
  };
}
