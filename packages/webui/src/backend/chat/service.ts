import { createHmac, timingSafeEqual } from 'node:crypto';
import { issuePdfAccess } from '../source-files/access';
import type { Source } from '../../store/chat/types';
import copy from '../../content/fr.json';
import { formatText } from '../../content/text';
import { actorId, upstreamHeaders } from '../logger';
import { projectAthenaEvent, formatSseEvent } from './events';

const text = copy.agentChat;
const runId = /^[a-f0-9]{32}$/;
const documentId = /^d[a-f0-9]{32}$/;
const statuses = ['running', 'queued', 'complete', 'failed', 'interrupted'];
const accessLifetime = 8 * 60 * 60 * 1000;
export class ChatError extends Error {
  readonly status: number;
  readonly code?: string;
  constructor(status: number, message: string, code?: string) {
    super(message);
    this.status = status;
    this.code = code;
  }
}
interface Options {
  env: Record<string, string | undefined>;
  fetch?: typeof globalThis.fetch;
  now?: () => number;
  agentAvailable?: () => Promise<boolean>;
}
function origin(value: string | undefined): string {
  if (!value) throw new ChatError(503, text.unavailable);
  const url = new URL(value);
  if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password ||
      url.pathname !== '/' || url.search || url.hash) throw new ChatError(503, text.unavailable);
  return url.origin;
}
function object(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== 'object' || Array.isArray(value))
    throw new ChatError(502, text.invalidResponse);
  return value as Record<string, unknown>;
}
function bounded(value: unknown, maximum = 10000): string {
  return typeof value === 'string' ? value.slice(0, maximum) : '';
}
export function createChatService(options: Options) {
  const fetchUpstream = options.fetch ?? globalThis.fetch;
  const now = options.now ?? Date.now;
  const env = options.env;
  function signature(payload: string, principal: string) {
    if (!env.WEBUI_AUTH_SECRET || env.WEBUI_AUTH_SECRET.length < 32) throw new ChatError(503, text.unavailable);
    // The account identifier never appears in browser URLs; scope tokens to this service too.
    return createHmac('sha256', env.WEBUI_AUTH_SECRET)
      .update(JSON.stringify(['webui-athena-v1',env.WEBUI_SESSION_EPOCH ?? '0', principal, payload])).digest('hex');
  }
  function issue(id: string, principal: string) {
    const payload = `${id}.${now() + accessLifetime}`;
    return `${payload}.${signature(payload, principal)}`;
  }
  function authorize(token: string, principal: string, allowExpired = false) {
    const match = /^([a-f0-9]{32})\.(\d{13})\.([a-f0-9]{64})$/.exec(token);
    if (!match) throw new ChatError(404, text.notFound);
    const expected = signature(`${match[1]}.${match[2]}`, principal);
    if (!timingSafeEqual(Buffer.from(match[3]!, 'hex'), Buffer.from(expected, 'hex')))
      throw new ChatError(404, text.notFound);
    if (!allowExpired && Number(match[2]) <= now()) throw new ChatError(404, text.notFound, 'token_expired');
    return match[1]!;
  }
  async function json(url: string, signal: AbortSignal, init: RequestInit = {}) {
    let response: Response;
    const reqHeaders = upstreamHeaders(init.headers);
    if (url.startsWith(origin(env.WEBUI_AGENT_URL)+'/') && env.KM_AGENT_SERVICE_TOKEN) reqHeaders.set('Authorization','Bearer '+env.KM_AGENT_SERVICE_TOKEN);
    else if (env.KM_API_TOKEN) reqHeaders.set('Authorization','Bearer '+env.KM_API_TOKEN);
    if (!reqHeaders.has('X-Request-Id')) reqHeaders.set('X-Request-Id', crypto.randomUUID());
    try {
      response = await fetchUpstream(url, {
        ...init, headers: reqHeaders, redirect: 'manual', cache: 'no-store',
        signal: AbortSignal.any([signal, AbortSignal.timeout(15000)]),
      });
    } catch {
      signal.throwIfAborted();
      throw new ChatError(503, text.unavailable);
    }
    if (!response.ok) {
      await response.body?.cancel();
      throw new ChatError(response.status === 429 ? 429 : response.status === 404 ? 404 : 503,
        response.status === 429 ? text.busy : response.status === 404 ? text.notFound : text.unavailable);
    }
    // Bound actual bytes, not merely Content-Length from the upstream server.
    const reader = response.body?.getReader();
    if (!reader) throw new ChatError(502, text.invalidResponse);
    let size = 0;
    let body = '';
    const decoder = new TextDecoder();
    try {
      while (true) {
        const chunk = await reader.read();
        if (chunk.done) break;
        size += chunk.value.byteLength;
        if (size > 2 * 1024 * 1024) throw new ChatError(502, text.invalidResponse);
        body += decoder.decode(chunk.value, { stream: true });
      }
      return object(JSON.parse(body + decoder.decode()));
    } catch (error) {
      signal.throwIfAborted();
      if (error instanceof ChatError) throw error;
      throw new ChatError(502, text.invalidResponse);
    } finally {
      await reader.cancel().catch(() => {});
      reader.releaseLock();
    }
  }
  async function detail(token: string, principal: string, signal: AbortSignal, allowExpired = false) {
    const id = authorize(token, principal, allowExpired);
    const value = await json(`${origin(env.WEBUI_AGENT_URL)}/runs/${id}`, signal, {
      headers: { 'X-Authenticated-User': actorId(principal) },
    });
    if (value.id !== id || typeof value.status !== 'string' || !statuses.includes(value.status))
      throw new ChatError(502, text.invalidResponse);
    return value;
  }
  function citations(result: Record<string, unknown>) {
    if (!Array.isArray(result.sources) || result.sources.length > 100)
      throw new ChatError(502, text.invalidResponse);
    return result.sources.map(object);
  }
  function sourceDigest(locator: Record<string, unknown>) {
    const sha = locator.source_sha256;
    return typeof sha === 'string' && /^[a-f0-9]{64}$/.test(sha) ? sha : undefined;
  }
  function source(value: Record<string, unknown>, index: number, principal: string): Source {
    const locator = value.locator && typeof value.locator === 'object' ? object(value.locator) : {};
    const sha = sourceDigest(locator);
    const citationReference = sha ? `${value.document_id}.${sha}` : value.document_id;
    const citationToken = typeof value.document_id === 'string' && documentId.test(value.document_id)
      ? `${citationReference}.${signature(`citation.${citationReference}.${index + 1}`, principal)}` : '';
    const pages = Array.isArray(locator.pages)
      ? locator.pages.filter((p): p is number => Number.isSafeInteger(p) && Number(p) > 0) : [];
    const page = Number.isSafeInteger(locator.page) && Number(locator.page) > 0
      ? Number(locator.page) : pages[0];
    const typeDocumentLabel = bounded(value.type_document_label, 500);
    return {
      id: index + 1,
      title: bounded(value.original_name, 500) || text.sourceTitle,
      category: text.sourceCategory,
      ...(typeof value.type_document_id === 'number' && Number.isSafeInteger(value.type_document_id)
        ? { typeDocumentId: value.type_document_id } : {}),
      ...(typeDocumentLabel ? { typeDocumentLabel } : {}),
      excerpt: bounded(value.excerpt) || bounded(value.quote) || bounded(value.text),
      pages: page ? formatText(text.pageLabel, { page }) : '',
      ...(typeof value.document_id === 'string' && documentId.test(value.document_id) && page
        ? { pdf: { url: `/api/chat/runs/${citationToken}/sources/${index + 1}`, accessUrl: `/api/chat/runs/${citationToken}/sources/${index + 1}`, page } } : {}),
    };
  }
  async function excerpts(values: Record<string, unknown>[], sources: Source[], signal: AbortSignal) {
    // Only retrieve units cited by this completed run, never an arbitrary browser-supplied ID.
    const deadline = AbortSignal.any([signal, AbortSignal.timeout(15000)]);
    let next = 0;
    await Promise.all(Array.from({ length: Math.min(4, values.length) }, async () => {
      while (next < values.length && !deadline.aborted) {
        const index = next++;
        const value = values[index]!;
        const item = sources[index]!;
        if (item.excerpt) continue;
        let path: string | undefined;
        if (typeof value.page_id === 'string' && /^[cv][a-f0-9]{32}$/.test(value.page_id))
          path = `/passages/${value.page_id}`;
        else if (typeof value.document_id === 'string' && documentId.test(value.document_id) && item.pdf)
          path = `/documents/${value.document_id}/physical-pages/${item.pdf.page}`;
        if (!path) continue;
        try {
          const passage = await json(`${origin(env.WEBUI_KM_API_URL)}${path}`, deadline);
          if (passage.document_id !== value.document_id) continue;
          const blocks = Array.isArray(passage.blocks) ? passage.blocks : [];
          item.excerpt = bounded(passage.text) || blocks
            .filter((block) => block && typeof block === 'object' && block.type === 'text' && typeof block.text === 'string')
            .map((block) => bounded(block.text)).join('\n\n').slice(0, 10000);
        } catch {
          signal.throwIfAborted();
          // A missing excerpt must not discard a completed answer and its PDF citation.
        }
      }
    }));
    for (const item of sources) if (!item.excerpt) item.excerpt = text.excerptUnavailable;
  }
  // Active stream counter is enforced per-process (Node.js runtime instance).
  // In a multi-replica deployment (e.g. 2 replicas behind a load balancer without sticky sessions),
  // this per-process limit is not a distributed global cluster guarantee.
  const activeStreamsPerPrincipal = new Map<string, number>();

  async function readRun(token: string, principal: string, signal: AbortSignal) {
    const run = await detail(token, principal, signal);
    if (run.status === 'failed' || run.status === 'interrupted')
      return { status: 'failed' as const, error: text.failed };
    if (run.status !== 'complete') return { status: 'running' as const };
    const result = object(run.result);
    if (result.answer !== null && typeof result.answer !== 'string')
      throw new ChatError(502, text.invalidResponse);
    const values = citations(result);
    const sources = values.map((value, index) => source(value, index, principal));
    await excerpts(values, sources, signal);
    return {
      status: 'complete' as const,
      answer: bounded(result.answer, 1000000) || text.emptyAnswer,
      sources,
    };
  }

  return {
    async start(input: unknown, principal: string, signal: AbortSignal) {
      if (!input || typeof input !== 'object' || Array.isArray(input))
        throw new ChatError(400, text.invalidRequest);
      const data = input as Record<string, unknown>;
      const allowedKeys = new Set(['question', 'history', 'mode', 'fast_id', 'fast_answer', 'reason']);
      if (Object.keys(data).some((key) => !allowedKeys.has(key)) || typeof data.question !== 'string' ||
          !data.question.trim() || data.question.length > 4000) throw new ChatError(400, text.invalidRequest);
      const history = data.history === undefined ? [] : data.history;
      if (!Array.isArray(history) || history.length > 12 || history.some((turn) =>
        !turn || typeof turn !== 'object' || Array.isArray(turn) ||
        Object.keys(turn).some((key) => key !== 'role' && key !== 'content') ||
        !['user', 'assistant'].includes(turn.role) || typeof turn.content !== 'string' ||
        !turn.content.trim() || turn.content.length > 4000) ||
        history.reduce((size, turn) => size + turn.content.length, 0) > 12000)
        throw new ChatError(400, text.invalidRequest);
      const seconds = Number(env.WEBUI_CHAT_SECONDS ?? '180');
      const mode = env.WEBUI_SEARCH_MODE ?? 'hybrid';
      if (!Number.isFinite(seconds) || seconds < 1 || seconds > 900 ||
          !['lexical', 'semantic', 'hybrid'].includes(mode)) throw new ChatError(503, text.unavailable);
      // Validate signing configuration before creating an upstream execution.
      signature('', principal);
      if (options.agentAvailable && !await options.agentAvailable()) throw new ChatError(503, text.unavailable);
      const payload: Record<string, unknown> = {
        question: data.question.trim(),
        seconds,
        search_mode: mode,
        mode: 'deep',
        ...(history.length ? { history } : {}),
        ...(typeof data.fast_id === 'string' ? { fast_id: data.fast_id } : {}),
        ...(typeof data.fast_answer === 'string' ? { fast_answer: data.fast_answer } : {}),
        ...(typeof data.reason === 'string' ? { reason: data.reason } : {}),
      };
      const run = await json(`${origin(env.WEBUI_AGENT_URL)}/runs`, signal, {
        method: 'POST', headers: { 'Content-Type': 'application/json', 'X-Authenticated-User': actorId(principal) },
        body: JSON.stringify(payload),
      });
      if (typeof run.id !== 'string' || !runId.test(run.id)) throw new ChatError(502, text.invalidResponse);
      return { id: issue(run.id, principal), status: 'running' as const, timeoutMs: (seconds + 30) * 1000 };
    },
    async fastStream(input: unknown, principal: string, signal: AbortSignal) {
      if (!input || typeof input !== 'object' || Array.isArray(input))
        throw new ChatError(400, text.invalidRequest);
      const data = input as Record<string, unknown>;
      const allowedKeys = new Set(['question', 'history', 'mode']);
      if (Object.keys(data).some((key) => !allowedKeys.has(key)) || typeof data.question !== 'string' ||
          !data.question.trim() || data.question.length > 4000) throw new ChatError(400, text.invalidRequest);
      const history = data.history === undefined ? [] : data.history;
      if (!Array.isArray(history) || history.length > 12 || history.some((turn) =>
        !turn || typeof turn !== 'object' || Array.isArray(turn) ||
        Object.keys(turn).some((key) => key !== 'role' && key !== 'content') ||
        !['user', 'assistant'].includes(turn.role) || typeof turn.content !== 'string' ||
        !turn.content.trim() || turn.content.length > 4000) ||
        history.reduce((size, turn) => size + turn.content.length, 0) > 12000)
        throw new ChatError(400, text.invalidRequest);

      if (options.agentAvailable && !await options.agentAvailable()) throw new ChatError(503, text.unavailable);

      const agentUrl = origin(env.WEBUI_AGENT_URL);
      const reqHeaders = upstreamHeaders({
        'Content-Type': 'application/json',
        'Accept': 'text/event-stream',
        'X-Authenticated-User': actorId(principal),
      });
      if (env.KM_AGENT_SERVICE_TOKEN) reqHeaders.set('Authorization', 'Bearer ' + env.KM_AGENT_SERVICE_TOKEN);
      if (!reqHeaders.has('X-Request-Id')) reqHeaders.set('X-Request-Id', crypto.randomUUID());

      let upstreamRes: Response;
      try {
        upstreamRes = await fetchUpstream(`${agentUrl}/ask/fast`, {
          method: 'POST',
          headers: reqHeaders,
          body: JSON.stringify({ question: data.question.trim(), mode: 'fast', ...(history.length ? { history } : {}) }),
          signal: AbortSignal.any([signal, AbortSignal.timeout(30000)]),
          redirect: 'manual',
          cache: 'no-store',
        });
      } catch {
        signal.throwIfAborted();
        throw new ChatError(503, text.unavailable);
      }

      if (!upstreamRes.ok) {
        await upstreamRes.body?.cancel();
        throw new ChatError(
          upstreamRes.status === 429 ? 429 : upstreamRes.status === 404 ? 404 : 503,
          upstreamRes.status === 429 ? text.busy : upstreamRes.status === 404 ? text.notFound : text.unavailable,
        );
      }

      if (!upstreamRes.body) throw new ChatError(502, text.invalidResponse);

      const MAX_SSE_BUFFER_SIZE = 2 * 1024 * 1024;
      const encoder = new TextEncoder();
      const decoder = new TextDecoder();
      let buffer = '';
      let currentEvent = '';
      let currentData: string | undefined;
      let currentId = '';

      function processLine(line: string, controller: TransformStreamDefaultController<Uint8Array>) {
        if (line.length > MAX_SSE_BUFFER_SIZE) {
          throw new ChatError(502, text.invalidResponse);
        }
        if (line === '') {
          if (currentData !== undefined) {
            let dataStr = currentData;
            if ((currentEvent === 'init' || currentEvent === 'complete') && dataStr) {
              try {
                const parsed = JSON.parse(dataStr);
                if (Array.isArray(parsed.sources)) {
                  parsed.sources = parsed.sources.map((s: unknown, idx: number) => {
                    const raw = s && typeof s === 'object' ? (s as Record<string, unknown>) : {};
                    return source(raw, idx, principal);
                  });
                  dataStr = JSON.stringify(parsed);
                }
              } catch {}
            }
            const outLines: string[] = [];
            if (currentId) outLines.push(`id: ${currentId}`);
            if (currentEvent) outLines.push(`event: ${currentEvent}`);
            for (const line of dataStr.split('\n')) outLines.push(`data: ${line}`);
            controller.enqueue(encoder.encode(outLines.join('\n') + '\n\n'));
          }
          currentEvent = '';
          currentData = undefined;
          currentId = '';
          return;
        }
        if (line.startsWith(':')) return;
        if (line.startsWith('event:')) {
          currentEvent = line.startsWith('event: ') ? line.slice(7).trim() : line.slice(6).trim();
        } else if (line.startsWith('data:')) {
          const val = line.startsWith('data: ') ? line.slice(6) : line.slice(5);
          currentData = currentData !== undefined ? `${currentData}\n${val}` : val;
          if (currentData.length > MAX_SSE_BUFFER_SIZE) {
            throw new ChatError(502, text.invalidResponse);
          }
        } else if (line.startsWith('id:')) {
          currentId = line.startsWith('id: ') ? line.slice(4).trim() : line.slice(3).trim();
        }
      }

      function extractLines(buf: string, isEof: boolean): { lines: string[]; remaining: string } {
        let processText = buf;
        let remaining = '';
        if (!isEof && buf.endsWith('\r')) {
          processText = buf.slice(0, -1);
          remaining = '\r';
        }
        const lines: string[] = [];
        let start = 0;
        for (let i = 0; i < processText.length; i++) {
          const ch = processText[i];
          if (ch === '\r') {
            if (i + 1 < processText.length && processText[i + 1] === '\n') {
              lines.push(processText.slice(start, i));
              i++;
              start = i + 1;
            } else {
              lines.push(processText.slice(start, i));
              start = i + 1;
            }
          } else if (ch === '\n') {
            lines.push(processText.slice(start, i));
            start = i + 1;
          }
        }
        remaining = processText.slice(start) + remaining;
        return { lines, remaining };
      }

      const transform = new TransformStream<Uint8Array, Uint8Array>({
        transform(chunk, controller) {
          buffer += decoder.decode(chunk, { stream: true });
          if (buffer.length > MAX_SSE_BUFFER_SIZE) {
            throw new ChatError(502, text.invalidResponse);
          }
          const { lines, remaining } = extractLines(buffer, false);
          buffer = remaining;
          for (const line of lines) {
            processLine(line, controller);
          }
        },
        flush(controller) {
          buffer += decoder.decode();
          if (buffer.length > MAX_SSE_BUFFER_SIZE) {
            throw new ChatError(502, text.invalidResponse);
          }
          const { lines } = extractLines(buffer, true);
          for (const line of lines) {
            processLine(line, controller);
          }
        },
      });

      return upstreamRes.body.pipeThrough(transform);
    },
    authorizeToken(token: string, principal: string) {
      return authorize(token, principal);
    },
    async read(token: string, principal: string, signal: AbortSignal) {
      return readRun(token, principal, signal);
    },
    eventsStream(token: string, principal: string, signal: AbortSignal, lastEventId?: string) {
      signal.throwIfAborted();
      const id = authorize(token, principal);
      const activeCount = activeStreamsPerPrincipal.get(principal) ?? 0;
      if (activeCount >= 5) {
        throw new ChatError(429, text.busy);
      }
      activeStreamsPerPrincipal.set(principal, activeCount + 1);

      const encoder = new TextEncoder();
      const initialAfter = typeof lastEventId === 'string' && /^\d+$/.test(lastEventId) ? Number(lastEventId) : 0;
      const rawSeconds = Number(env.WEBUI_CHAT_SECONDS ?? '180');
      const seconds = Number.isSafeInteger(rawSeconds) && rawSeconds >= 1 && rawSeconds <= 900 ? rawSeconds : 180;

      const tokenMatch = /^([a-f0-9]{32})\.(\d{13})\.([a-f0-9]{64})$/.exec(token);
      const tokenIssuedAt = tokenMatch ? Number(tokenMatch[2]) - accessLifetime : now();
      const runDeadline = (Number.isFinite(tokenIssuedAt) && tokenIssuedAt > 0 ? tokenIssuedAt : now()) + (seconds + 30) * 1000;
      const remainingBudgetMs = Math.max(100, runDeadline - now());
      const timeoutSignal = AbortSignal.timeout(remainingBudgetMs);

      const streamAbort = new AbortController();
      const onSignalAbort = () => streamAbort.abort(signal.reason);
      signal.addEventListener('abort', onSignalAbort, { once: true });
      const combinedSignal = AbortSignal.any([signal, streamAbort.signal, timeoutSignal]);


      return new ReadableStream<Uint8Array>({
        cancel(reason) {
          streamAbort.abort(reason);
        },
        async start(controller) {
          combinedSignal.throwIfAborted();
          let currentAfter = initialAfter;
          let currentOffset = 0;
          let lastHeartbeat = Date.now();

          const heartbeatInterval = setInterval(() => {
            if (combinedSignal.aborted) return;
            if (Date.now() - lastHeartbeat >= 4500) {
              try {
                if (controller.desiredSize === null || controller.desiredSize > 0) {
                  controller.enqueue(encoder.encode(': ping\n\n'));
                  lastHeartbeat = Date.now();
                }
              } catch {}
            }
          }, 1000);

          const wait = (ms: number) => new Promise<void>((resolve, reject) => {
            const timer = setTimeout(() => {
              combinedSignal.removeEventListener('abort', onAbort);
              resolve();
            }, ms);
            const onAbort = () => {
              clearTimeout(timer);
              combinedSignal.removeEventListener('abort', onAbort);
              reject(combinedSignal.reason);
            };
            combinedSignal.addEventListener('abort', onAbort, { once: true });
            if (combinedSignal.aborted) onAbort();
          });

          const ensureCapacity = async (): Promise<boolean> => {
            if (controller.desiredSize === null || controller.desiredSize > 0) return true;
            const startWait = Date.now();
            while (!combinedSignal.aborted && controller.desiredSize !== null && controller.desiredSize <= 0) {
              if (controller.desiredSize < -250 || Date.now() - startWait > 5000) {
                return false;
              }
              await wait(25);
            }
            return !combinedSignal.aborted;
          };

          const drainOnePage = async (): Promise<{ hasMore: boolean; fatal: boolean; runStatus?: string }> => {
            let page: Record<string, unknown>;
            try {
              page = await json(
                `${origin(env.WEBUI_AGENT_URL)}/runs/${id}/events?after=${currentAfter}&offset=${currentOffset}`,
                combinedSignal,
                { headers: { 'X-Authenticated-User': actorId(principal) } },
              );
            } catch (error) {
              combinedSignal.throwIfAborted();
              if (error instanceof ChatError && error.status === 404) {
                if (await ensureCapacity()) {
                  controller.enqueue(encoder.encode(formatSseEvent(currentAfter, 'error', { error: text.notFound })));
                }
                return { hasMore: false, fatal: true };
              }
              return { hasMore: false, fatal: false };
            }

            const items = Array.isArray(page.items) ? page.items : [];
            if (items.length > 250) {
              if (await ensureCapacity()) {
                controller.enqueue(encoder.encode(formatSseEvent(currentAfter, 'error', { error: text.invalidResponse })));
              }
              return { hasMore: false, fatal: true };
            }

            const nextAfter = typeof page.next_after === 'number' && Number.isSafeInteger(page.next_after) && page.next_after >= 0
              ? page.next_after : currentAfter;
            const nextOffset = typeof page.next_offset === 'number' && Number.isSafeInteger(page.next_offset) && page.next_offset >= 0
              ? page.next_offset : currentOffset;

            // Validate monotonicity
            if (nextAfter < currentAfter || nextOffset < currentOffset) return { hasMore: false, fatal: false };
            if (Boolean(page.has_more) && nextAfter === currentAfter && nextOffset === currentOffset) return { hasMore: false, fatal: false };

            const hasMore = Boolean(page.has_more);

            for (const item of items) {
              const projected = projectAthenaEvent(item);
              if (projected) {
                if (!(await ensureCapacity())) return { hasMore: false, fatal: true };
                controller.enqueue(encoder.encode(formatSseEvent(projected.seq + 1, 'activity', projected)));
                lastHeartbeat = Date.now();
              }
            }

            // Emit checkpoint if cursor advanced
            if (nextAfter > currentAfter) {
              if (await ensureCapacity()) {
                controller.enqueue(encoder.encode(formatSseEvent(nextAfter, 'checkpoint', { cursor: nextAfter })));
                lastHeartbeat = Date.now();
              }
            }

            currentAfter = nextAfter;
            currentOffset = nextOffset;

            const runStatus = statuses.find((status) => status === page.run_status);
            return { hasMore, fatal: false, runStatus };
          };

          let lastProgress = Date.now();
          try {
            while (!combinedSignal.aborted) {
              if (now() >= runDeadline) {
                if (await ensureCapacity()) {
                  controller.enqueue(encoder.encode(formatSseEvent(currentAfter, 'error', { error: text.timeout })));
                }
                controller.close();
                return;
              }

              if (controller.desiredSize !== null && controller.desiredSize < -250) {
                controller.close();
                return;
              }

              let hasMore = true;
              let pagesDrained = 0;
              let runStatus: string | undefined;
              const previousOffset = currentOffset;
              const previousAfter = currentAfter;

              while (hasMore && pagesDrained < 10 && !combinedSignal.aborted) {
                pagesDrained++;
                const res = await drainOnePage();
                if (res.fatal) {
                  controller.close();
                  return;
                }
                hasMore = res.hasMore;
                runStatus = res.runStatus ?? runStatus;
              }

              // Support agents from before the event page carried the status.
              if (!runStatus) {
                try {
                  const runInfo = await detail(token, principal, combinedSignal);
                  if (typeof runInfo.status === 'string') runStatus = runInfo.status;
                  if (runStatus === 'complete') hasMore = true;
                } catch (error) {
                  combinedSignal.throwIfAborted();
                }
              }

              if (runStatus === 'complete') {
                let drainLimit = 50;
                while (hasMore && drainLimit > 0 && !combinedSignal.aborted) {
                  drainLimit--;
                  const res = await drainOnePage();
                  if (res.fatal) {
                    controller.close();
                    return;
                  }
                  hasMore = res.hasMore;
                }

                try {
                  const finalResult = await readRun(token, principal, combinedSignal);
                  if (finalResult.status === 'complete') {
                    if (await ensureCapacity()) {
                      controller.enqueue(encoder.encode(formatSseEvent(currentAfter, 'complete', {
                        answer: finalResult.answer,
                        sources: finalResult.sources,
                      })));
                    }
                    controller.close();
                    return;
                  } else if (finalResult.status === 'failed') {
                    if (await ensureCapacity()) {
                      controller.enqueue(encoder.encode(formatSseEvent(currentAfter, 'error', {
                        error: text.failed,
                      })));
                    }
                    controller.close();
                    return;
                  }
                } catch (error) {
                  combinedSignal.throwIfAborted();
                  if (await ensureCapacity()) {
                    controller.enqueue(encoder.encode(formatSseEvent(currentAfter, 'error', {
                      error: error instanceof ChatError ? error.message : text.failed,
                    })));
                  }
                  controller.close();
                  return;
                }
              }

              if (runStatus === 'failed' || runStatus === 'interrupted') {
                if (await ensureCapacity()) {
                  controller.enqueue(encoder.encode(formatSseEvent(currentAfter, 'error', {
                    error: text.failed,
                    interrupted: runStatus === 'interrupted',
                  })));
                }
                controller.close();
                return;
              }

              try {
                const currentTime = Date.now();
                if (currentOffset > previousOffset || currentAfter > previousAfter || hasMore) lastProgress = currentTime;
                const idle = currentTime - lastProgress;
                await wait(idle < 2000 ? 300 : idle < 5000 ? 800 : 1500);
              } catch {
                break;
              }
            }
          } catch {
            // Signal aborted or stream cancelled
          }
 finally {
            clearInterval(heartbeatInterval);
            signal.removeEventListener('abort', onSignalAbort);
            const current = activeStreamsPerPrincipal.get(principal) ?? 1;
            if (current <= 1) activeStreamsPerPrincipal.delete(principal);
            else activeStreamsPerPrincipal.set(principal, current - 1);
            try {
              controller.close();
            } catch {}
          }
        },
      });
    },
    async file(request: Request, token: string, sourceId: string, principal: string) {
      if (!/^[1-9]\d{0,2}$/.test(sourceId)) throw new ChatError(404, text.notFound);
      const citationMatch = /^(d[a-f0-9]{32})(?:\.([a-f0-9]{64}))?\.([a-f0-9]{64})$/.exec(token);
      if (citationMatch) {
        const [, id, sha, signed] = citationMatch;
        const reference = sha ? `${id}.${sha}` : id;
        const expected = signature(`citation.${reference}.${sourceId}`, principal);
        if (!timingSafeEqual(Buffer.from(signed!, 'hex'), Buffer.from(expected, 'hex')))
          throw new ChatError(404, text.notFound);
        try {
          return await issuePdfAccess({ documentId: id!, ...(sha ? { sourceSha256: sha } : {}), principal, signal: request.signal, env, fetch: fetchUpstream, now });
        } catch {
          request.signal.throwIfAborted();
          throw new ChatError(503, text.unavailable);
        }
      }
      // Legacy saved URLs remain bound to their original account and completed run.
      const run = await detail(token, principal, request.signal, true);
      if (run.status !== 'complete') throw new ChatError(404, text.notFound);
      const citation = citations(object(run.result))[Number(sourceId) - 1];
      if (!citation || typeof citation.document_id !== 'string' || !documentId.test(citation.document_id))
        throw new ChatError(404, text.notFound);
      // Legacy renewal still requires the signed reference and a completed run.
      try {
        const locator = citation.locator && typeof citation.locator === 'object' ? object(citation.locator) : {};
        const sha = sourceDigest(locator);
        return await issuePdfAccess({ documentId: citation.document_id, ...(sha ? { sourceSha256: sha } : {}), principal, signal: request.signal, env, fetch: fetchUpstream, now });
      } catch {
        request.signal.throwIfAborted();
        throw new ChatError(503, text.unavailable);
      }
    },
  };
}
