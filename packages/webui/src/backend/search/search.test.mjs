import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createSearchService } from './service.ts';
import { createSearchHandlers } from './handlers.ts';
import { createRateLimiter } from '../http/rate-limiter.ts';
import { createSearchClient } from '../../store/search/client.ts';
import copy from '../../content/fr.json';
const doc = 'd' + 'b'.repeat(32);
const env = { FILESERVER_PUBLIC_URL:'https://files.example', FILESERVER_JWT_SECRET:'test-secret-at-least-32-characters-long', WEBUI_AUTH_SECRET: 'a-test-secret-with-at-least-32-characters', WEBUI_KM_API_URL: 'http://km:8000' };
const hit = { document_id: doc, text: '<script>untrusted</script> passage', score: .8, original_name: 'Notice.pdf', locator: { pages: [12, 13] } };
const input = { question: 'pression', limit: 10, mode: 'hybrid' };
const signal = () => new AbortController().signal;
function setup(overrides = {}) {
  const calls = [];
  let clock = 1800000000000;
  const service = createSearchService({ env, now: () => clock, fetch: async (url, init) => {
    calls.push({ url, init });
    return url.endsWith('/source-reference')
      ? Response.json({document_id:doc,relative_path:'_versions/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.pdf'})
      : Response.json({ results: [hit] });
  }, ...overrides });
  return { service, calls, advance: (ms) => { clock += ms; } };
}
function request(value = input, headers = {}) {
  return new Request('http://webui/api/search', { method: 'POST', headers: { 'Content-Type': 'application/json', ...headers }, body: JSON.stringify(value) });
}
test('direct search reuses the agent retrieval endpoint and returns only public result fields', async () => {
  const { service, calls } = setup();
  const value = await service.search({ question: ' pression ' }, 'alice', signal());
  assert.equal(calls[0].url, 'http://km:8000/search');
  assert.deepEqual(JSON.parse(calls[0].init.body), { ...input, filters: {} });
  assert.equal(value.results[0].source.pdf.page, 12);
  assert.equal(value.results[0].source.excerpt, hit.text);
  assert.deepEqual(Object.keys(value).sort(), ['results']);
  const pdf = await service.file(new Request('http://webui/file', { headers: { Cookie: 'private', Range: 'bytes=0-7' } }), value.results[0].source.pdf.url.split('/').at(-1), 'alice');
  assert.equal(pdf.status, 200);
  assert.equal(calls.at(-1).url, `http://km:8000/documents/${doc}/source-reference`);
  assert.equal(calls.at(-1).init.headers.Cookie, undefined);
  assert.equal(new Headers(calls.at(-1).init.headers).get('Range'), null);
  assert.equal(new URL((await pdf.json()).url).origin, 'https://files.example');
});
test('PDF capabilities remain account-scoped when reopened after eight hours', async () => {
  const { service, calls, advance } = setup();
  const value = await service.search(input, 'alice', signal());
  const token = value.results[0].source.pdf.url.split('/').at(-1);
  const before = calls.length;
  await assert.rejects(service.file(new Request('http://webui/file'), token, 'bob'), { status: 404 });
  await assert.rejects(service.file(new Request('http://webui/file'), token.replace(doc, 'd' + 'c'.repeat(32)), 'alice'), { status: 404 });
  advance(8 * 3600 * 1000);
  assert.equal(calls.length, before);
  assert.equal((await service.file(new Request('http://webui/file'), token, 'alice')).status,200);
  assert.equal(calls.length,before+1);
});
test('search enforces sessions, origin, body bounds and strict search parameters', async () => {
  const { service, calls } = setup();
  const anonymous = createSearchHandlers({ authenticate: async () => null, service });
  assert.equal((await anonymous.search(request())).status, 401);
  assert.equal((await anonymous.file(new Request('http://webui/file'), 'fake')).status, 401);
  const handlers = createSearchHandlers({ authenticate: async () => ({ id: 'alice' }), service });
  assert.equal((await handlers.search(request(input, { Origin: 'https://foreign.test' }))).status, 403);
  assert.equal((await handlers.search(request(input, { 'Content-Type': 'text/plain' }))).status, 415);
  for (const value of [{ question: ' ' }, { ...input, limit: 101 }, { ...input, mode: 'other' }, { ...input, mode: ['hybrid'] }, { ...input, url: 'http://evil' }, { question: 'x'.repeat(4001) }])
    assert.equal((await handlers.search(request(value))).status, 400);
  assert.equal((await handlers.search(request({ question: 'x'.repeat(21000) }))).status, 413);
  assert.equal(calls.length, 0);
  const good = await handlers.search(request());
  assert.equal(good.status, 200);
  assert.equal(good.headers.get('cache-control'), 'private, no-store');
});
test('invalid config, upstream errors, malformed and oversized results fail with controlled messages', async () => {
  for (const configuration of [{ ...env, WEBUI_AUTH_SECRET: '' }, { ...env, WEBUI_KM_API_URL: 'http://km/path' }]) {
    const { service, calls } = setup({ env: configuration });
    await assert.rejects(service.search(input, 'alice', signal()), { status: 503 });
    assert.equal(calls.length, 0);
  }
  for (const data of [{ results: [{ ...hit, document_id: '../file' }] }, { results: [{ ...hit, score: '0.8' }] }, { results: Array(11).fill(hit) }]) {
    const { service } = setup({ fetch: async () => Response.json(data) });
    await assert.rejects(service.search(input, 'alice', signal()), { status: 502 });
  }
  const { service } = setup({ fetch: async () => new Response('internal diagnostics', { status: 429 }) });
  await assert.rejects(service.search(input, 'alice', signal()), { status: 429, message: copy.documentSearch.busy });
  const oversized = setup({ fetch: async () => new Response('x'.repeat(4 * 1024 * 1024 + 1)) });
  await assert.rejects(oversized.service.search(input, 'alice', signal()), { status: 502 });
});
test('search PDF access rejects non-JSON metadata and redirects', async () => {
  const { service } = setup();
  const value = await service.search(input, 'alice', signal());
  const token = value.results[0].source.pdf.url.split('/').at(-1);
  for (const status of [200,302]) {
    const bad = setup({fetch:async()=>new Response('private html',{status,headers:{'Content-Type':'text/html'}})});
    await assert.rejects(bad.service.file(new Request('http://webui/file'),token,'alice'),{status:503});
  }
});
test('client ignores stale responses and cancellation, with separate state per instance', async () => {
  const pending = [];
  const client = createSearchClient((url, init) => new Promise((resolve) => pending.push({ init, resolve })));
  const other = createSearchClient();
  const first = client.search(input);
  const second = client.search({ ...input, question: 'nouvelle' });
  assert.equal(pending[0].init.signal.aborted, true);
  pending[1].resolve(Response.json({ results: [] }));
  await second;
  pending[0].resolve(Response.json({ results: [hit] }));
  await first;
  assert.equal(client.getState().status, 'complete');
  assert.deepEqual(client.getState().results, []);
  assert.equal(other.getState().status, 'idle');
  const third = client.search(input);
  client.cancel();
  pending[2].resolve(Response.json({ results: [] }));
  await third;
  assert.equal(client.getState().status, 'idle');
});
test('client sanitizes network errors and rejects invalid source URLs', async () => {
  const client = createSearchClient(async () => { throw new Error('private internal diagnostics'); });
  await client.search(input);
  assert.equal(client.getState().error, copy.documentSearch.unavailable);
  const invalid = createSearchClient(async () => Response.json({ results: [{ documentId: doc, score: .8,
    source: { id: 1, title: 'Notice', category: '', pages: '', excerpt: '', pdf: { url: 'https://evil', page: 1 } } }] }));
  await invalid.search(input);
  assert.equal(invalid.getState().error, copy.documentSearch.invalidResponse);
});
test('search handlers enforce rate limiting when rateLimiter is provided', async () => {
  const { service } = setup();
  let clock = 1000;
  const rateLimiter = createRateLimiter({ rps: 1, burst: 1, now: () => clock });
  const handlers = createSearchHandlers({
    authenticate: async () => ({ id: 'alice' }),
    service,
    rateLimiter,
  });

  // 1st request succeeds
  const res1 = await handlers.search(request());
  assert.equal(res1.status, 200);

  // 2nd request in same second is rate-limited (429)
  const res2 = await handlers.search(request());
  assert.equal(res2.status, 429);
  assert.equal(res2.headers.get('retry-after'), '1');
  const body = await res2.json();
  assert.equal(body.error, copy.documentSearch.rateLimited);

  // After 1000ms, request succeeds again
  clock += 1000;
  const res3 = await handlers.search(request());
  assert.equal(res3.status, 200);
});

test('one source capability is reused when the clock advances between signing operations',async()=>{
  let clock=1800000000000;
  const {service}=setup({now:()=>clock++});
  const result=await service.search(input,'alice',signal());
  assert.equal(result.results[0].source.pdf.accessUrl,result.results[0].source.pdf.url);
});

test('a search citation opens its immutable PDF and exposes only that passage’s valid boxes', async () => {
  const id = 'c' + 'e'.repeat(32);
  const digest = 'a'.repeat(64);
  const location = { page_no: 12, bbox: [10, 20, 120, 150], coord_origin: 'BOTTOMLEFT' };
  let passageDocument = doc;
  const { service } = setup({ fetch: async (url) => {
    if (url.endsWith('/search')) return Response.json({ results: [{ ...hit, id }] });
    if (url.endsWith('/passages/' + id)) return Response.json({
      id, document_id: passageDocument, source_sha256: digest,
      record: { locations: [location, { ...location, bbox: [0, 'bad', 10, 10] }] },
    });
    const source = new URL(url);
    assert.equal(source.searchParams.get('source_sha256'), passageDocument === doc ? digest : null);
    return Response.json({ document_id: doc, relative_path: `_versions/${digest}.pdf` });
  } });
  const result = await service.search(input, 'alice', signal());
  const token = result.results[0].source.pdf.url.split('/').at(-1);
  const view = () => service.file(new Request('http://webui/file'), token, 'alice');
  assert.deepEqual((await (await view()).json()).locations, [location]);
  passageDocument = 'd' + 'f'.repeat(32);
  assert.equal((await (await view()).json()).locations, undefined);
});
