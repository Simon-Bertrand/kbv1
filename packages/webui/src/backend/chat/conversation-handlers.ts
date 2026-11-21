import { ConversationRepository } from './conversation-repository';
import { isConversation } from '../../store/chat/repository';
import { readJson } from '../http/json';
import { setRequestUser, withRequestContext } from '../logger';
import copy from '../../content/fr.json';

import { createRateLimiter } from '../http/rate-limiter';
const persistenceLimiter=createRateLimiter({scope:'conversation-access',rps:5,burst:20});
const text = copy.conversations;
const headers = { 'Cache-Control': 'private, no-store', 'X-Content-Type-Options': 'nosniff' };
export function createConversationHandlers(authenticate: () => Promise<{ id: string; name?: string | null; email?: string | null } | null>, repo = new ConversationRepository()) {
  return async (request: Request, id?: string): Promise<Response> => {
    return withRequestContext(request, null, async () => {
      const user = await authenticate();
      if (!user) return Response.json({ error: text.unauthorized }, { status: 401, headers });
      setRequestUser(user);
      let budget;
      try {budget=await (persistenceLimiter.consumeAsync?.(user.id) ?? persistenceLimiter.consume(user.id));}
      catch {return Response.json({error:text.unavailable},{status:503,headers});}
      if (!budget.allowed) return new Response(null,{status:429,headers:{...headers,'Retry-After':String(budget.retryAfter)}});
      if (id !== undefined && !/^[a-zA-Z0-9_-]{1,128}$/.test(id)) return new Response(null, { status: 404, headers });
      if (request.method !== 'GET') {
        const origin = request.headers.get('origin');
        if ((origin && origin !== new URL(process.env.WEBUI_AUTH_URL ?? request.url).origin) ||
            request.headers.get('sec-fetch-site') === 'cross-site') return new Response(null, { status: 403, headers });
      }
      try {
        if (request.method === 'GET') {
          const url=new URL(request.url);
          if (!id && url.searchParams.get('view')==='summary') {
            const after=url.searchParams.get('after')??'';
            if (after && !/^[a-zA-Z0-9_-]{1,128}$/.test(after)) return new Response(null,{status:400,headers});
            return Response.json(await repo.page(user.id,after,false),{headers});
          }
          if (!id) return Response.json({ conversations: await repo.list(user.id) }, { headers });
          const conversation = await repo.get(id, user.id);
          return Response.json(conversation ? { conversation } : { error: text.notFound }, { status: conversation ? 200 : 404, headers });
        }
        if (request.method === 'POST' && !id) {
          if (request.headers.get('content-type')?.split(';')[0]?.trim() !== 'application/json') return new Response(null, { status: 415, headers });
          const body = await readJson(request.body, 4 * 1024 * 1024, () => new Error(text.invalid));
          if (!isConversation(body) || body.remoteStub) return Response.json({ error: text.invalid }, { status: 400, headers });
          const revision = await repo.save(body, user.id);
          return Response.json({ ok: true, revision }, { headers });
        }
        if (request.method === 'DELETE' && id) {
          const emptyOnly = new URL(request.url).searchParams.get('empty') === 'true';
          if (await repo.delete(id, user.id, emptyOnly)) return new Response(null, { status: 204, headers });
          const stillExists = emptyOnly && await repo.get(id, user.id);
          return new Response(null, { status: stillExists ? 409 : 404, headers });
        }
        return new Response(null, { status: 405, headers });
      } catch (error) {
        if (error && typeof error === 'object' && 'status' in error && error.status === 409)
          return Response.json({error:text.conflict},{status:409,headers});
        if(error && typeof error==='object' && 'status' in error && error.status===413)
          return Response.json({error:text.quota},{status:413,headers});
        const invalid = error instanceof Error && error.message === text.invalid;
        return Response.json({ error: invalid ? text.invalid : text.unavailable }, { status: invalid ? 400 : 503, headers });
      }
    });
  };
}
