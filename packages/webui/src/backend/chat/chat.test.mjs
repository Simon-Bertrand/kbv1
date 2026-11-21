import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createChatService, ChatError } from './service.ts';
import { createChatHandlers } from './handlers.ts';
import { createRateLimiter } from '../http/rate-limiter.ts';
import copy from '../../content/fr.json';
import { HttpChatProvider, ChatRequestError } from '../../store/chat/http-provider.ts';
import { ChatModel } from '../../store/chat/model.ts';
import { AnnotationRepository } from '../../store/feedback/repository.ts';

const id = 'a'.repeat(32);
const doc = 'd' + 'b'.repeat(32);
const env = { FILESERVER_PUBLIC_URL:'https://files.example', FILESERVER_JWT_SECRET:'test-secret-at-least-32-characters-long', WEBUI_AUTH_SECRET: 'secret-for-tests-with-at-least-32-characters', WEBUI_AGENT_URL: 'http://agent:8086', WEBUI_KM_API_URL: 'http://km:8000' };
const citation = { id: 'S3', document_id: doc, page_id: 'c' + 'e'.repeat(32), original_name: 'Notice.pdf', locator: { pages: [12, 13] } };
const result = { answer: '**Réponse étayée**', sources: [citation], warnings: [], internal: 'never expose' };
const signal = () => new AbortController().signal;
const response = (value, status = 200) => Response.json(value, { status });
function setup(options = {}) {
  const calls = [];
  let clock = 1800000000000;
  const service = createChatService({ env, now: () => clock, fetch: async (url, init) => {
    calls.push({ url, init });
    if (init.method === 'POST') return response({ id, status: 'running' });
    const requested = new URL(url);
    if (requested.pathname.endsWith('/source-reference')) {
      const digest = requested.searchParams.get('source_sha256');
      return response({ document_id: doc, relative_path: digest
        ? `_versions/docs/Notice.PDF/${digest}.pdf` : `_versions/${'a'.repeat(64)}.pdf` });
    }
    if (url.includes('/passages/')) return response({ document_id: doc, blocks: [{ type: 'text', text: 'Extrait original.' }] });
    return response({ id, status: 'complete', result, question: 'private', ...options.run });
  }, ...options.service });
  return { service, calls, advance: (ms) => { clock += ms; } };
}
async function start(service, user = 'alice') { return service.start({ question: 'Question ?' }, user, signal()); }
function request(body = { question: 'Question ?' }, headers = {}) {
  return new Request('http://webui/api/chat/runs', { method: 'POST', headers: { 'Content-Type': 'application/json', ...headers }, body: JSON.stringify(body) });
}

test('Athena launch uses the existing protocol and returns a signed per-user reference', async () => {
  const { service, calls } = setup();
  const run = await start(service);
  assert.match(run.id, /^[a-f0-9]{32}\.\d{13}\.[a-f0-9]{64}$/);
  assert.deepEqual(JSON.parse(calls[0].init.body), { question: 'Question ?', seconds: 180, search_mode: 'hybrid', mode: 'deep' });
  assert.equal(calls[0].url, 'http://agent:8086/runs');
  const value = await service.read(run.id, 'alice', signal());
  assert.equal(value.status, 'complete');
  assert.equal(value.answer, result.answer);
  assert.equal(value.sources[0].pdf.page, 12);
  assert.equal(value.sources[0].title, 'Notice.pdf');
  assert.match(value.sources[0].pdf.url, /^\/api\/chat\/runs\/d[a-f0-9]{32}\.[a-f0-9]{64}\/sources\/1$/);
  assert.deepEqual(Object.keys(value).sort(), ['answer', 'sources', 'status']);
});

test('run references reject another user, tampering and expiry before contacting Athena', async () => {
  const { service, calls, advance } = setup();
  const run = await start(service);
  const before = calls.length;
  await assert.rejects(service.read(run.id, 'bob', signal()), { status: 404 });
  const tampered = run.id.slice(0, -1) + (run.id.endsWith('0') ? '1' : '0');
  await assert.rejects(service.read(tampered, 'alice', signal()), { status: 404 });
  advance(8 * 3600 * 1000);
  await assert.rejects(service.read(run.id, 'alice', signal()), { status: 404 });
  assert.equal(calls.length, before);
});

test('only cited sources receive direct Fileserver capabilities, renewed for the same account', async () => {
  const { service, calls, advance } = setup();
  const run = await start(service);
  const req = new Request('http://webui/file', {headers:{Cookie:'secret',Range:'bytes=0-7'}});
  const access = await service.file(req, run.id, '1', 'alice');
  const data = await access.json();
  const url = new URL(data.url);
  assert.equal(url.origin, 'https://files.example');
  assert.equal(url.pathname, '/files/_versions/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.pdf');
  assert.equal(access.headers.get('cache-control'), 'private, no-store');
  assert.equal(calls.at(-1).url, `http://km:8000/documents/${doc}/source-reference`);
  assert.equal(new Headers(calls.at(-1).init.headers).has('cookie'), false);
  assert.equal(new Headers(calls.at(-1).init.headers).has('range'), false);
  await assert.rejects(service.file(req, run.id, '2', 'alice'), {status:404});
  await assert.rejects(service.file(req, run.id, '1', 'bob'), {status:404});
  advance(3600*1000);
  const renewed = await (await service.file(req,run.id,'1','alice')).json();
  assert.notEqual(renewed.url, data.url);
  advance(8*3600*1000);
  assert.equal((await service.file(req,run.id,'1','alice')).status,200);
  await assert.rejects(service.read(run.id,'alice',signal()),{status:404});
  assert.ok(!calls.some(call => call.url.endsWith('/source')));
});

test('a signed citation retains its PDF version without querying the current KM profile', async () => {
  const sha = 'c'.repeat(64);
  const cited = { ...citation, locator: { ...citation.locator, source_sha256: sha } };
  const { service, calls, advance } = setup({ run: { result: { ...result, sources: [cited] } } });
  const run = await start(service);
  const { sources } = await service.read(run.id, 'alice', signal());
  const token = sources[0].pdf.accessUrl.split('/')[4];
  const req = new Request('http://webui/file');
  advance(3600 * 1000);
  const before = calls.length;
  const data = await (await service.file(req, token, '1', 'alice')).json();
  assert.equal(new URL(data.url).pathname, `/files/_versions/docs/Notice.PDF/${sha}.pdf`);
  assert.equal(calls.at(-1).url, `http://km:8000/documents/${doc}/source-reference?source_sha256=${sha}`);
  await assert.rejects(service.file(req, token, '1', 'bob'), { status: 404 });
  await assert.rejects(service.file(req, token.replace(sha, 'd'.repeat(64)), '1', 'alice'), { status: 404 });
  assert.equal(calls.length, before + 1);
});

test('PDF authorization refuses redirected, malformed and escaping metadata', async () => {
  for (const metadata of [null,{document_id:doc,relative_path:'mutable-source.pdf'}, {document_id:doc,relative_path:'../secret.pdf'}, {document_id:'other',relative_path:'a.pdf'}]) {
    const {service} = setup({service:{fetch:async (url, init) => {
      if (init.method === 'POST') return response({id});
      if (url.endsWith('/source-reference')) return metadata === null ? new Response(null,{status:302,headers:{Location:'http://internal'}}) : response(metadata);
      return response({id,status:'complete',result});
    }}});
    const run = await start(service);
    await assert.rejects(service.file(new Request('http://webui/file'),run.id,'1','alice'),{status:503});
  }
});

test('signed PDF URLs retain the Fileserver ingress prefix', async () => {
  const { service } = setup({ service: { env: { ...env, FILESERVER_PUBLIC_URL: 'https://files.example/company/fileserver/' } } });
  const run = await start(service);
  const access = await service.file(new Request('http://webui/file'), run.id, '1', 'alice');
  const url = new URL((await access.json()).url);
  assert.equal(url.origin, 'https://files.example');
  assert.equal(url.pathname, '/company/fileserver/files/_versions/' + 'a'.repeat(64) + '.pdf');
  assert.ok(url.searchParams.has('token'));
});

test('handlers enforce session, same origin, JSON and question bounds before creating a run', async () => {
  const { service, calls } = setup();
  const anonymous = createChatHandlers({ authenticate: async () => null, service });
  assert.equal((await anonymous.start(request())).status, 401);
  const handlers = createChatHandlers({ authenticate: async () => ({ id: 'alice' }), service });
  assert.equal((await handlers.start(request({}, { Origin: 'https://foreign.test' }))).status, 403);
  assert.equal((await handlers.start(request({}, { 'Content-Type': 'text/plain' }))).status, 415);
  assert.equal((await handlers.start(request({ question: ' '.repeat(20) }))).status, 400);
  assert.equal((await handlers.start(request({ question: 'x'.repeat(4001) }))).status, 400);
  assert.equal((await handlers.start(request({ question: 'x', agentUrl: 'http://evil' }))).status, 400);
  assert.equal((await handlers.start(request({ question: 'x'.repeat(100001) }))).status, 413);
  const malformed = new Request('http://webui/api/chat/runs', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{' });
  assert.equal((await handlers.start(malformed)).status, 400);
  assert.equal(calls.length, 0);
  assert.equal((await handlers.start(request())).status, 202);
});

test('invalid configuration creates no upstream execution, and upstream failures are sanitized', async () => {
  for (const overrides of [{ WEBUI_AUTH_SECRET: '' }, { WEBUI_AGENT_URL: 'http://agent/path' }, { WEBUI_CHAT_SECONDS: 'NaN' }, { WEBUI_SEARCH_MODE: 'bad' }]) {
    const { service, calls } = setup({ service: { env: { ...env, ...overrides } } });
    await assert.rejects(start(service));
    assert.equal(calls.length, 0);
  }
  const { service } = setup({ service: { fetch: async () => new Response('secret', { status: 429 }) } });
  await assert.rejects(start(service), { status: 429 });
  const failure = setup({ run: { status: 'failed', error: 'private stack' } });
  const run = await start(failure.service);
  const detail = await failure.service.read(run.id, 'alice', signal());
  assert.equal(detail.status, 'failed');
  assert.ok(!JSON.stringify(detail).includes('private stack'));
});

test('HTTP chat adapter polls the WebUI API and yields the final answer once, with real citations', async () => {
  const { service } = setup();
  const run = await start(service);
  const completed = await service.read(run.id, 'alice', signal());
  const calls = [];
  let polls = 0;
  const provider = new HttpChatProvider({ pollMs: 1, fetch: async (url, init) => {
    calls.push({ url, init });
    return response(url === '/api/chat/runs' ? run : ++polls === 1 ? { status: 'running' } : completed);
  } });
  const output = [];
  for await (const chunk of provider.stream({ messages: [{ role: 'user', content: 'Question ?' }], attachments: [], signal: signal() })) output.push(chunk);
  assert.deepEqual(output, [result.answer]);
  assert.deepEqual(provider.sources, completed.sources);
  assert.equal(calls.length, 3);
  assert.equal(provider.origin, 'generated');
});

test('HTTP adapter rejects attachments and overlong prompts, and aborts polling immediately', async () => {
  let count = 0;
  const { service } = setup();
  const run = await start(service);
  const provider = new HttpChatProvider({ pollMs: 60000, fetch: async () => { count++; return response(count === 1 ? run : { status: 'running' }); } });
  const consume = async (options) => { for await (const chunk of provider.stream(options)) assert.fail(chunk); };
  await assert.rejects(consume({ messages: [], attachments: [{}], signal: signal() }), ChatRequestError);
  await assert.rejects(consume({ messages: [{ role: 'user', content: 'x'.repeat(4001) }], attachments: [], signal: signal() }), ChatRequestError);
  assert.equal(count, 0);
  const controller = new AbortController();
  const pending = consume({ messages: [{ role: 'user', content: 'Q' }], attachments: [], signal: controller.signal });
  await new Promise((resolve) => setTimeout(resolve, 10));
  controller.abort();
  await assert.rejects(pending, { name: 'AbortError' });
  assert.equal(count, 2);
  assert.deepEqual(provider.sources, []);
});

test('chat model surfaces controlled integration errors without accepting fake sources', async () => {
  const model = new ChatModel({ id: 'chat', title: 'Chat', messages: [] }, {
    origin: 'generated', sources: [], async *stream() { throw new ChatRequestError('Erreur contrôlée'); },
  }, () => {});
  await model.send('Question');
  const answer = model.active.messages.at(-1);
  assert.equal(answer.error, true);
  assert.equal(answer.errorMessage, 'Erreur contrôlée');
  assert.equal(model.busy, false);
  assert.equal(model.origin, 'generated');
});

test('local conversation and annotation histories are separated per account; generated annotations restore', async () => {
  const storage = new Map();
  globalThis.localStorage = { getItem: (key) => storage.get(key) ?? null, setItem: (key, value) => storage.set(key, value) };
  const make = (scope) => new ChatModel({ id: 'chat', title: 'Chat', messages: [] }, {
    storageScope: scope, origin: 'generated', sources: [], async *stream() { yield 'Answer'; },
  }, () => {});
  const alice = make('alice'); alice.connect(); await alice.send('Question privée'); alice.disconnect();
  const bob = make('bob'); bob.connect(); assert.equal(bob.active.messages.length, 0); bob.disconnect();
  const again = make('alice'); again.connect(); assert.equal(again.active.messages[0].content, 'Question privée'); again.disconnect();
  const record = { schemaVersion: 1, id: 'note', createdAt: '', updatedAt: '', comment: '', kind: 'feedback', snapshot: {
    messageId: 'answer', conversationId: 'chat', prompt: 'Q', response: 'A', context: [], sources: [], origin: 'generated',
  }, values: { sentiment: 'positive', issues: [], satisfaction: null, relevance: null, accuracy: null, grounding: null, sourceId: null, passage: '', correction: '' } };
  new AnnotationRepository('alice-notes').save(record);
  assert.equal(new AnnotationRepository('alice-notes').all().length, 1);
  assert.equal(new AnnotationRepository('bob-notes').all().length, 0);
  delete globalThis.localStorage;
});

test('oversized upstream payload is rejected', async () => {
  const { service } = setup({ service: { fetch: async () => new Response('x'.repeat(2 * 1024 * 1024 + 1)) } });
  await assert.rejects(start(service), (error) => error instanceof ChatError && error.status === 502);
});


test('configured public origin works when Next.js uses an internal request URL', async () => {
  const { service } = setup();
  const handlers = createChatHandlers({ publicOrigin: 'https://chat.example', authenticate: async () => ({ id: 'alice' }), service });
  const req = new Request('http://internal:3000/api/chat/runs', { method: 'POST',
    headers: { 'Content-Type': 'application/json', Origin: 'https://chat.example' }, body: JSON.stringify({ question: 'Q' }) });
  assert.equal((await handlers.start(req)).status, 202);
});


test('HTTP adapter enforces the overall polling deadline without leaving timers running', async () => {
  const { service } = setup();
  const run = await start(service);
  const provider = new HttpChatProvider({ pollMs: 10, fetch: async (url) =>
    response(url === '/api/chat/runs' ? { ...run, timeoutMs: 1000 } : { status: 'running' }) });
  await assert.rejects(async () => {
    for await (const chunk of provider.stream({ messages: [{ role: 'user', content: 'Q' }], attachments: [], signal: signal() }))
      assert.fail(chunk);
  }, (error) => error instanceof ChatRequestError && error.message.includes('délai'));
  assert.deepEqual(provider.sources, []);
});

test('chat handlers enforce rate limiting when rateLimiter is provided', async () => {
  const { service } = setup();
  let clock = 1000;
  const rateLimiter = createRateLimiter({ rps: 1, burst: 1, now: () => clock });
  const handlers = createChatHandlers({
    authenticate: async () => ({ id: 'alice' }),
    service,
    rateLimiter,
  });

  // 1st request succeeds
  const res1 = await handlers.start(request());
  assert.equal(res1.status, 202);

  // 2nd request in same second is rate-limited (429)
  const res2 = await handlers.start(request());
  assert.equal(res2.status, 429);
  assert.equal(res2.headers.get('retry-after'), '1');
  const body = await res2.json();
  assert.equal(body.error, copy.agentChat.rateLimited);

  // After 1000ms, request succeeds again
  clock += 1000;
  const res3 = await handlers.start(request());
  assert.equal(res3.status, 202);
});


test('durable PDF citations survive run expiry without reloading an archived run', async () => {
  const {service, calls, advance} = setup();
  const run = await start(service);
  const result = await service.read(run.id, 'alice', signal());
  const token = result.sources[0].pdf.url.split('/')[4];
  advance(31 * 86400000);
  const before = calls.length;
  const access = await service.file(new Request('http://webui/file'), token, '1', 'alice');
  assert.equal(access.status, 200);
  assert.equal(calls.length, before + 1);
  assert.ok(calls.at(-1).url.endsWith('/source-reference'));
  await assert.rejects(service.file(new Request('http://webui/file'), token, '1', 'bob'), {status:404});
  await assert.rejects(service.file(new Request('http://webui/file'), token, '2', 'alice'), {status:404});
  const revoked = createChatService({env:{...env, WEBUI_SESSION_EPOCH:'revoked'}});
  await assert.rejects(revoked.file(new Request('http://webui/file'), token, '1', 'alice'), {status:404});
  await assert.rejects(service.read(run.id, 'alice', signal()), {status:404});
});

test('chat forwards bounded conversation history and rejects invalid context before launch', async () => {
  const {service,calls} = setup();
  const history = [{role:'user',content:'Document X'}, {role:'assistant',content:'Sa pression est 12 bar.'}];
  await service.start({question:'Et sa date ?',history},'alice',signal());
  assert.deepEqual(JSON.parse(calls[0].init.body).history,history);
  const count=calls.length;
  for (const invalid of [null, {}, [{role:'system',content:'instruction'}], [{role:'user',content:'x',extra:1}],
    [{role:'user',content:' '}], Array.from({length:13},()=>({role:'user',content:'x'})),
    Array.from({length:4},()=>({role:'assistant',content:'x'.repeat(4000)}))]) {
    await assert.rejects(service.start({question:'Q',history:invalid},'alice',signal()), {status:400});
  }
  assert.equal(calls.length,count);
});

test('fastStream handles CRLF line breaks, chunk splits and rewrites citations', async () => {
  const enc = new TextEncoder();
  const initPayload = JSON.stringify({ id: 'fast-run-1', sources: [citation] });
  const completePayload = JSON.stringify({ answer: 'Réponse rapide', sources: [citation] });

  // Stream chunks with CRLF and chunk split across \r and \n
  const chunks = [
    enc.encode(`event: init\r\ndata: ${initPayload}\r\n\r\n`),
    enc.encode('event: delta\r\ndata: {"delta": "Première partie"}\r'),
    enc.encode('\n\r\nevent: delta\r\ndata:\r\ndata: second line\r\n\r\n'),
    enc.encode('event: complete\r\ndata: ' + completePayload + '\r\n\r\n'),
    enc.encode('event: complete\r\ndata: {"answer":"truncated"}\r\n'),
  ];

  let chunkIdx = 0;
  const upstreamBody = new ReadableStream({
    pull(controller) {
      if (chunkIdx < chunks.length) {
        controller.enqueue(chunks[chunkIdx++]);
      } else {
        controller.close();
      }
    },
  });

  const { service } = setup({
    service: {
      fetch: async (url) => {
        if (url.endsWith('/ask/fast')) {
          return new Response(upstreamBody, {
            status: 200,
            headers: { 'Content-Type': 'text/event-stream' },
          });
        }
        return response({ id, status: 'complete', result });
      },
    },
  });

  const stream = await service.fastStream({ question: 'Question rapide ?' }, 'alice', signal());
  const reader = stream.getReader();
  const dec = new TextDecoder();
  let fullOutput = '';
  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    fullOutput += dec.decode(value, { stream: true });
  }

  // Verify all frames are standard LF (\n\n) without \r\n leakage
  assert.ok(!fullOutput.includes('\r'));
  assert.ok(fullOutput.includes('event: init\n'));
  assert.ok(fullOutput.includes('event: delta\n'));
  assert.ok(fullOutput.includes('event: complete\n'));
  assert.ok(fullOutput.includes('event: delta\ndata: \ndata: second line\n\n'));
  assert.ok(!fullOutput.includes('truncated'));
  // Verify citation was rewritten by BFF with signed token
  assert.match(fullOutput, /\/api\/chat\/runs\/[a-zA-Z0-9_.-]+\/sources\/1/);
});

test('fastStream rejects oversized buffer when upstream exceeds 2MB without event boundary', async () => {
  const enc = new TextEncoder();
  const bigChunk = enc.encode('data: ' + 'x'.repeat(1024 * 1024) + '\n');

  let sent = 0;
  const upstreamBody = new ReadableStream({
    pull(controller) {
      if (sent < 3) {
        sent++;
        controller.enqueue(bigChunk);
      } else {
        controller.close();
      }
    },
  });

  const { service } = setup({
    service: {
      fetch: async (url) => {
        if (url.endsWith('/ask/fast')) {
          return new Response(upstreamBody, {
            status: 200,
            headers: { 'Content-Type': 'text/event-stream' },
          });
        }
        return response({ id, status: 'complete', result });
      },
    },
  });

  const stream = await service.fastStream({ question: 'Question rapide ?' }, 'alice', signal());
  const reader = stream.getReader();
  await assert.rejects(async () => {
    while (true) {
      const { done } = await reader.read();
      if (done) break;
    }
  }, (err) => err.status === 502);
});
