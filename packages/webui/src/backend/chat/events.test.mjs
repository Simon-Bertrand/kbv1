import { test } from 'node:test';
import assert from 'node:assert/strict';
import { projectAthenaEvent, formatSseEvent } from './events.ts';
import { createChatService, ChatError } from './service.ts';
import { createChatHandlers } from './handlers.ts';
import { createRateLimiter } from '../http/rate-limiter.ts';
import copy from '../../content/fr.json' with { type: 'json' };

const text = copy.agentChat;
const activities = text.activities;

const id = 'a'.repeat(32);
const doc = 'd' + 'b'.repeat(32);
const env = {
  FILESERVER_PUBLIC_URL: 'https://files.example',
  FILESERVER_JWT_SECRET: 'test-secret-at-least-32-characters-long',
  WEBUI_AUTH_SECRET: 'secret-for-tests-with-at-least-32-characters',
  WEBUI_AGENT_URL: 'http://agent:8086',
  WEBUI_KM_API_URL: 'http://km:8000',
  WEBUI_CHAT_SECONDS: '10',
  WEBUI_SEARCH_MODE: 'hybrid',
};
const citation = {
  id: 'S1',
  document_id: doc,
  page_id: 'c' + 'e'.repeat(32),
  original_name: 'Notice.pdf',
  locator: { pages: [1] },
};
const result = {
  answer: '**Réponse de test**',
  sources: [citation],
};
const signal = () => new AbortController().signal;
const response = (value, status = 200) => Response.json(value, { status });

test('projectAthenaEvent maps known nodes and tools to French labels and strict phases', () => {
  // Nodes
  const nodeStart = projectAthenaEvent({
    seq: 1,
    kind: 'span_start',
    category: 'node',
    name: 'decompose',
    span_id: 'node-1',
    ts: 1700000000,
  });
  assert.deepEqual(nodeStart, {
    seq: 1,
    summary: activities.phaseAnalysis,
    step: {
      id: 'node-1',
      type: 'phase',
      phase: 'analysis',
      label: activities.phaseAnalysis,
      status: 'running',
      timestamp: 1700000000000,
    },
  });

  const nodeEnd = projectAthenaEvent({
    seq: 2,
    kind: 'span_end',
    category: 'node',
    name: 'synthesize',
    span_id: 'node-2',
    duration_ms: 123.4,
    status: 'ok',
    ts: 1700000001,
  });
  assert.deepEqual(nodeEnd, {
    seq: 2,
    summary: activities.phaseSynthesis,
    step: {
      id: 'node-2',
      type: 'phase',
      phase: 'synthesis',
      label: activities.phaseSynthesis,
      status: 'complete',
      durationMs: 123,
      timestamp: 1700000001000,
    },
  });

  // Tools
  const toolSearch = projectAthenaEvent({
    seq: 3,
    kind: 'span_start',
    category: 'tool',
    name: 'search_text',
    span_id: 'tool-1',
    inputs: { query: 'pression maximale' },
    ts: 1700000002,
  });
  assert.equal(toolSearch?.step.phase, 'search');
  assert.equal(toolSearch?.step.label, activities.toolSearchText);
  assert.equal(toolSearch?.step.detail, 'Recherche : « pression maximale »');

  // Retrieval result
  const retrieval = projectAthenaEvent({
    seq: 4,
    kind: 'retrieval_result',
    returned: 5,
    span_id: 'retrieval-1',
    ts: 1700000003,
  });
  assert.deepEqual(retrieval, {
    seq: 4,
    summary: activities.phaseSearch,
    step: {
      id: 'retrieval-1',
      type: 'tool',
      phase: 'search',
      label: activities.toolSearchText,
      status: 'complete',
      detail: '5 passage(s) trouvé(s)',
      count: 5,
      timestamp: 1700000003000,
    },
  });
});

test('projectAthenaEvent and formatSseEvent strictly enforce canary filtering and block private internals', () => {
  const CANARY = 'CANARY_SECRET_LEAK_TOKEN_98765';

  // 1. LLM category is completely dropped
  const llmEvent = projectAthenaEvent({
    seq: 5,
    kind: 'span_start',
    category: 'llm',
    name: 'generate',
    thinking: CANARY,
    prompt: CANARY,
  });
  assert.equal(llmEvent, null);

  // 2. Unknown node/tool names are dropped
  const unknownNode = projectAthenaEvent({
    seq: 6,
    kind: 'span_start',
    category: 'node',
    name: 'private_internal_reasoning',
    reasoning: CANARY,
  });
  assert.equal(unknownNode, null);

  // 3. Dangerous queries containing canary/forbidden tokens are dropped from detail
  const dangerousQuery = projectAthenaEvent({
    seq: 7,
    kind: 'span_start',
    category: 'tool',
    name: 'search_text',
    span_id: 'tool-danger',
    inputs: { query: `SELECT * FROM users; -- ${CANARY}` },
  });
  assert.equal(dangerousQuery?.step.detail, undefined);

  // 4. Injected internal payloads, draft answers, file paths in inputs/outputs are never propagated
  const injectedTool = projectAthenaEvent({
    seq: 8,
    kind: 'span_end',
    category: 'tool',
    name: 'read_selected_pages',
    span_id: 'tool-injected',
    inputs: { path: '/home/dev/knowledgebase/secret.key', canary: CANARY },
    outputs: { draft_answer: CANARY, thinking_process: CANARY, raw_doc: CANARY },
    status: 'ok',
  });
  const serialized = JSON.stringify(injectedTool);
  assert.equal(serialized.includes(CANARY), false);
  assert.equal(serialized.includes('/home/dev'), false);

  // 5. formatSseEvent produces standard compliant SSE
  const formatted = formatSseEvent(1, 'activity', { ok: true });
  assert.equal(formatted, 'id: 1\nevent: activity\ndata: {"ok":true}\n\n');
});

test('eventsStream drains pages, advances cursors on filtered pages, and emits complete event', async () => {
  let clock = 1800000000000;
  const agentCalls = [];

  const service = createChatService({
    env,
    now: () => clock,
    fetch: async (url, init) => {
      agentCalls.push({ url, init });
      if (url.includes('/events?')) {
        const u = new URL(url);
        const after = Number(u.searchParams.get('after') || '0');
        if (after === 0) {
          // Page 1: returns raw items that are ALL filtered (llm category)
          return response({
            items: [
              { seq: 1, kind: 'span_start', category: 'llm', name: 'reasoning' },
              { seq: 2, kind: 'span_end', category: 'llm', name: 'reasoning' },
            ],
            next_after: 2,
            next_offset: 120,
            has_more: true,
            run_status: 'running',
          });
        }
        if (after === 2) {
          // Page 2: returns a public activity event and finishes
          return response({
            items: [
              { seq: 3, kind: 'span_start', category: 'node', name: 'decompose', span_id: 'n3', ts: 1700000000 },
            ],
            next_after: 3,
            next_offset: 250,
            has_more: false,
            run_status: 'complete',
          });
        }
        return response({ items: [], next_after: 3, next_offset: 250, has_more: false });
      }
      if (url.endsWith('/source-reference')) {
        return response({ document_id: doc, relative_path: '_versions/doc.pdf' });
      }
      if (url.includes('/passages/')) {
        return response({ document_id: doc, blocks: [{ type: 'text', text: 'Passage' }] });
      }
      // Status check / detail check
      const isComplete = agentCalls.filter(c => c.url.includes('/events?')).length >= 2;
      return response({
        id,
        status: isComplete ? 'complete' : 'running',
        result: isComplete ? result : undefined,
      });
    },
  });

  const run = await service.start({ question: 'Test SSE' }, 'alice', signal());
  const stream = service.eventsStream(run.id, 'alice', signal());

  const reader = stream.getReader();
  const decoder = new TextDecoder();
  let received = '';

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    received += decoder.decode(value, { stream: true });
  }

  assert.ok(received.includes('event: activity'));
  assert.ok(received.includes(activities.phaseAnalysis));
  assert.ok(received.includes('event: complete'));
  assert.ok(received.includes(result.answer));
  assert.ok(received.includes('Notice.pdf'));

  // Ensure cursors advanced past the filtered items
  const eventUrls = agentCalls.filter(c => c.url.includes('/events?')).map(c => c.url);
  assert.ok(eventUrls.some(u => u.includes('after=0')));
  assert.ok(eventUrls.some(u => u.includes('after=2')));
  assert.equal(agentCalls.filter(call => call.url.endsWith('/runs/' + id)).length, 1);
});

test('eventsStream handles upstream failure and abort signal cleanly', async () => {
  let clock = 1800000000000;
  const service = createChatService({
    env,
    now: () => clock,
    fetch: async (url) => {
      if (url.includes('/events?')) {
        return response({ items: [], next_after: 0, next_offset: 0, has_more: false });
      }
      return response({ id, status: 'failed', error: 'Athena failure' });
    },
  });

  const run = await service.start({ question: 'Test failure' }, 'alice', signal());
  const stream = service.eventsStream(run.id, 'alice', signal());

  const reader = stream.getReader();
  const decoder = new TextDecoder();
  let received = '';
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    received += decoder.decode(value, { stream: true });
  }

  assert.ok(received.includes('event: error'));
  assert.ok(received.includes(text.failed));

  // Abort test: stream terminates when signal aborted
  const hangingService = createChatService({
    env,
    now: () => clock,
    fetch: async (_url, init) => {
      return new Promise((resolve, reject) => {
        if (init?.signal?.aborted) {
          reject(new DOMException('Aborted', 'AbortError'));
          return;
        }
        init?.signal?.addEventListener('abort', () => {
          reject(new DOMException('Aborted', 'AbortError'));
        });
      });
    },
  });

  const abortController = new AbortController();
  const abortStream = hangingService.eventsStream(run.id, 'alice', abortController.signal);
  const abortReader = abortStream.getReader();
  const readPromise = abortReader.read();
  abortController.abort();
  const res = await readPromise;
  assert.equal(res.done, true);
});

test('events handler enforces authentication, origin, rate limits, and returns SSE headers', async () => {
  let clock = 1800000000000;
  const service = createChatService({
    env,
    now: () => clock,
    fetch: async (url) => {
      if (url.endsWith('/runs')) return response({ id, status: 'running' });
      if (url.includes('/events?')) return response({ items: [], next_after: 0, next_offset: 0, has_more: false });
      return response({ id, status: 'complete', result });
    },
  });

  let currentUser = null;
  const rateLimiter = createRateLimiter({ rps: 10, burst: 10 });
  const handlers = createChatHandlers({
    service,
    authenticate: async () => (currentUser ? { id: currentUser } : null),
    rateLimiter,
  });

  const run = await service.start({ question: 'Test handlers' }, 'alice', signal());

  // 1. Unauthenticated -> 401
  currentUser = null;
  const unauthReq = new Request(`http://localhost/api/chat/runs/${run.id}/events`, {
    headers: { 'Sec-Fetch-Site': 'same-origin' },
  });
  const unauthRes = await handlers.events(unauthReq, run.id);
  assert.equal(unauthRes.status, 401);

  // 2. Cross-site -> 403
  currentUser = 'alice';
  const crossReq = new Request(`http://localhost/api/chat/runs/${run.id}/events`, {
    headers: {
      'Sec-Fetch-Site': 'cross-site',
    },
  });
  const crossRes = await handlers.events(crossReq, run.id);
  assert.equal(crossRes.status, 403);

  // 3. Cross-user token (Bob accessing Alice's run) -> 404
  currentUser = 'bob';
  const bobReq = new Request(`http://localhost/api/chat/runs/${run.id}/events`, {
    headers: {
      'Sec-Fetch-Site': 'same-origin',
    },
  });
  const bobRes = await handlers.events(bobReq, run.id);
  assert.equal(bobRes.status, 404);

  // 4. Forged token -> 404
  currentUser = 'alice';
  const forgedReq = new Request(`http://localhost/api/chat/runs/invalid-token/events`, {
    headers: {
      'Sec-Fetch-Site': 'same-origin',
    },
  });
  const forgedRes = await handlers.events(forgedReq, 'invalid-token');
  assert.equal(forgedRes.status, 404);

  // 5. Valid request -> 200 with correct SSE headers
  currentUser = 'alice';
  const validReq = new Request(`http://localhost/api/chat/runs/${run.id}/events`, {
    headers: {
      'Sec-Fetch-Site': 'same-origin',
      Accept: 'text/event-stream',
    },
  });
  const validRes = await handlers.events(validReq, run.id);
  assert.equal(validRes.status, 200);
  assert.equal(validRes.headers.get('Content-Type'), 'text/event-stream; charset=utf-8');
  assert.equal(validRes.headers.get('Cache-Control'), 'private, no-cache, no-store, no-transform');
  assert.equal(validRes.headers.get('X-Accel-Buffering'), 'no');

  // Cancel stream to clean up
  await validRes.body?.cancel();
});

test('eventsStream underlying ReadableStream cancel() hook halts stream without request.signal abort', async () => {
  let clock = 1800000000000;
  let upstreamFetchCount = 0;
  let upstreamAborted = false;

  const service = createChatService({
    env,
    now: () => clock,
    fetch: async (url, init) => {
      upstreamFetchCount++;
      if (url.includes('/events?')) {
        return new Promise((resolve, reject) => {
          init?.signal?.addEventListener('abort', () => {
            upstreamAborted = true;
            reject(new DOMException('Aborted', 'AbortError'));
          });
        });
      }
      return response({ id, status: 'running' });
    },
  });

  const requestAbortController = new AbortController(); // does NOT abort
  const run = await service.start({ question: 'Test cancel hook' }, 'alice', signal());
  const stream = service.eventsStream(run.id, 'alice', requestAbortController.signal);

  // Client cancels the stream directly (e.g. browser disconnected / closed response body)
  await stream.cancel('client_disconnected');
  await new Promise((r) => setTimeout(r, 20));

  assert.equal(upstreamAborted, true);
  assert.equal(requestAbortController.signal.aborted, false); // request.signal remained untouched
});

test('eventsStream avoids run_end race condition, drains backlog beyond 10 pages, and emits complete event', async () => {
  let clock = 1800000000000;
  let statusPolls = 0;
  let pagesRequested = 0;

  const service = createChatService({
    env,
    now: () => clock,
    fetch: async (url) => {
      if (url.includes('/events?')) {
        pagesRequested++;
        const u = new URL(url);
        const after = Number(u.searchParams.get('after') || '0');
        // Simulate 12 pages of backlog: pages 0 to 11 have more, page 12 finishes
        if (after < 12) {
          const item = after === 0
            ? { seq: 0, kind: 'run_end' } // run_end arrives right away in page 0!
            : { seq: after, kind: 'span_start', category: 'node', name: 'decompose', span_id: `node-${after}`, ts: 1700000000 };
          return response({
            items: [item],
            next_after: after + 1,
            next_offset: (after + 1) * 10,
            has_more: after < 12,
          });
        }
        return response({ items: [], next_after: 13, next_offset: 130, has_more: false });
      }
      if (url.endsWith('/source-reference')) {
        return response({ document_id: doc, relative_path: '_versions/doc.pdf' });
      }
      if (url.includes('/passages/')) {
        return response({ document_id: doc, blocks: [{ type: 'text', text: 'Passage' }] });
      }
      // Status poll
      statusPolls++;
      // On first poll, archive.update is still running despite run_end having been seen!
      // Only on second poll does status become 'complete'
      return response({
        id,
        status: statusPolls >= 2 ? 'complete' : 'running',
        result: statusPolls >= 2 ? result : undefined,
      });
    },
  });

  const run = await service.start({ question: 'Test race condition' }, 'alice', signal());
  const stream = service.eventsStream(run.id, 'alice', signal());
  const reader = stream.getReader();
  const decoder = new TextDecoder();
  let received = '';

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    received += decoder.decode(value, { stream: true });
  }

  // Must NOT emit fake error despite sawRunEnd arriving while status was still running
  assert.equal(received.includes('event: error'), false);
  assert.ok(received.includes('event: complete'));
  assert.ok(received.includes(result.answer));
  // Backlog (>10 pages) was drained
  assert.ok(pagesRequested >= 12);
});

test('handlers.events validates cursor safe integer headers/query before returning 200', async () => {
  let clock = 1800000000000;
  const service = createChatService({
    env,
    now: () => clock,
    fetch: async () => response({ id, status: 'running' }),
  });
  const handlers = createChatHandlers({
    service,
    authenticate: async () => ({ id: 'alice' }),
  });

  const run = await service.start({ question: 'Test cursor validation' }, 'alice', signal());

  // 1. Negative after -> 400
  const negReq = new Request(`http://localhost/api/chat/runs/${run.id}/events?after=-1`, {
    headers: { 'Sec-Fetch-Site': 'same-origin' },
  });
  const negRes = await handlers.events(negReq, run.id);
  assert.equal(negRes.status, 400);

  // 2. Non-numeric after -> 400
  const strReq = new Request(`http://localhost/api/chat/runs/${run.id}/events?after=invalid_cursor`, {
    headers: { 'Sec-Fetch-Site': 'same-origin' },
  });
  const strRes = await handlers.events(strReq, run.id);
  assert.equal(strRes.status, 400);

  // 3. Float after -> 400
  const floatReq = new Request(`http://localhost/api/chat/runs/${run.id}/events?after=12.34`, {
    headers: { 'Sec-Fetch-Site': 'same-origin' },
  });
  const floatRes = await handlers.events(floatReq, run.id);
  assert.equal(floatRes.status, 400);

  // 4. Invalid Last-Event-ID header -> 400
  const badHdrReq = new Request(`http://localhost/api/chat/runs/${run.id}/events`, {
    headers: { 'Sec-Fetch-Site': 'same-origin', 'Last-Event-ID': 'bad-id' },
  });
  const badHdrRes = await handlers.events(badHdrReq, run.id);
  assert.equal(badHdrRes.status, 400);
});

test('eventsStream enforces active stream limit per principal and rejects 6th stream', async () => {
  let clock = 1800000000000;
  const service = createChatService({
    env,
    now: () => clock,
    fetch: async () => response({ id, status: 'running' }),
  });

  const run = await service.start({ question: 'Test stream limit' }, 'alice', signal());
  const streams = [];

  // Open 5 concurrent streams
  for (let i = 0; i < 5; i++) {
    streams.push(service.eventsStream(run.id, 'alice', signal()));
  }

  // 6th stream for alice should throw ChatError 429
  assert.throws(() => service.eventsStream(run.id, 'alice', signal()), (err) => {
    assert.equal(err.status, 429);
    return true;
  });

  // Clean up opened streams
  for (const s of streams) await s.cancel();
});

test('projectAthenaEvent handles real Athena search_text question arguments and allows business tokens', () => {
  // Real Athena passes arguments.question
  const realAthenaSearch = projectAthenaEvent({
    seq: 1,
    kind: 'span_start',
    category: 'tool',
    name: 'search_text',
    span_id: 'tool-real',
    inputs: { question: 'quelle est la pression maximale ?' },
    ts: 1700000000,
  });
  assert.equal(realAthenaSearch?.step.detail, 'Recherche : « quelle est la pression maximale ? »');

  // Business query with 'token', 'key', 'secret' words is preserved (not aggressively blacklisted)
  const businessQuery = projectAthenaEvent({
    seq: 2,
    kind: 'span_start',
    category: 'tool',
    name: 'search_text',
    span_id: 'tool-biz',
    inputs: { question: 'authentification par token ou secret key' },
    ts: 1700000001,
  });
  assert.equal(businessQuery?.step.detail, 'Recherche : « authentification par token ou secret key »');

  // Query with internal URL is blocked
  const urlQuery = projectAthenaEvent({
    seq: 3,
    kind: 'span_start',
    category: 'tool',
    name: 'search_text',
    span_id: 'tool-url',
    inputs: { question: 'visiter http://127.0.0.1:8085/v1' },
    ts: 1700000002,
  });
  assert.equal(urlQuery?.step.detail, undefined);

  // Query with filesystem path is blocked
  const pathQuery = projectAthenaEvent({
    seq: 4,
    kind: 'span_start',
    category: 'tool',
    name: 'search_text',
    span_id: 'tool-path',
    inputs: { question: 'lire /home/dev/conf.json' },
    ts: 1700000003,
  });
  assert.equal(pathQuery?.step.detail, undefined);

  // Invalid seq (negative or string or float) returns null
  assert.equal(projectAthenaEvent({ seq: -1, kind: 'span_start', category: 'node', name: 'decompose', ts: 1700000000 }), null);
  assert.equal(projectAthenaEvent({ seq: 'invalid', kind: 'span_start', category: 'node', name: 'decompose', ts: 1700000000 }), null);
  assert.equal(projectAthenaEvent({ seq: 1.5, kind: 'span_start', category: 'node', name: 'decompose', ts: 1700000000 }), null);
});

test('eventsStream with pre-aborted signal throws immediately without consuming active counter or calling upstream', async () => {
  let clock = 1800000000000;
  let fetchCount = 0;
  const service = createChatService({
    env,
    now: () => clock,
    fetch: async () => {
      fetchCount++;
      return response({ id, status: 'running' });
    },
  });

  const run = await service.start({ question: 'Test pre-abort' }, 'alice', signal());
  const initialFetchCount = fetchCount;

  const preAborted = new AbortController();
  preAborted.abort(new Error('Pre-aborted'));

  assert.throws(() => service.eventsStream(run.id, 'alice', preAborted.signal), (err) => {
    assert.equal(err.message, 'Pre-aborted');
    return true;
  });

  // No upstream fetch occurred for eventsStream
  assert.equal(fetchCount, initialFetchCount);

  // Counter was not consumed: can still open 5 full streams
  const streams = [];
  for (let i = 0; i < 5; i++) {
    streams.push(service.eventsStream(run.id, 'alice', signal()));
  }
  for (const s of streams) await s.cancel();
});

test('eventsStream emits checkpoint event with safe cursor when events are filtered', async () => {
  let clock = 1800000000000;
  let eventCalls = 0;
  const service = createChatService({
    env,
    now: () => clock,
    fetch: async (url) => {
      if (url.includes('/events?')) {
        eventCalls++;
        if (eventCalls === 1) {
          return response({
            items: [
              { seq: 1, kind: 'span_start', category: 'llm', name: 'thinking' },
              { seq: 6, kind: 'span_end', category: 'llm', name: 'thinking' },
            ],
            next_after: 7,
            next_offset: 100,
            has_more: false,
          });
        }
        return response({ items: [], next_after: 7, next_offset: 100, has_more: false });
      }
      return response({ id, status: 'complete', result });
    },
  });

  const run = await service.start({ question: 'Test checkpoint' }, 'alice', signal());
  const stream = service.eventsStream(run.id, 'alice', signal());
  const reader = stream.getReader();
  const decoder = new TextDecoder();
  let received = '';

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    received += decoder.decode(value, { stream: true });
  }

  assert.ok(received.includes('event: checkpoint'));
  assert.ok(received.includes('"cursor":7'));
  assert.ok(received.includes('event: complete'));
});

test('eventsStream respects consumer backpressure and delivers complete event once resumed', async () => {
  let clock = 1800000000000;
  const service = createChatService({
    env,
    now: () => clock,
    fetch: async (url) => {
      if (url.includes('/events?')) {
        return response({
          items: [
            { seq: 1, kind: 'span_start', category: 'node', name: 'decompose', span_id: 'n1', ts: 1700000000 },
          ],
          next_after: 2,
          next_offset: 10,
          has_more: false,
        });
      }
      return response({ id, status: 'complete', result });
    },
  });

  const run = await service.start({ question: 'Test backpressure' }, 'alice', signal());
  const stream = service.eventsStream(run.id, 'alice', signal());
  const reader = stream.getReader();
  const decoder = new TextDecoder();

  // Slow consumer: pauses before reading
  await new Promise((r) => setTimeout(r, 50));

  let received = '';
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    received += decoder.decode(value, { stream: true });
  }

  assert.ok(received.includes('event: activity'));
  assert.ok(received.includes('event: complete'));
  assert.ok(received.includes(result.answer));
});

