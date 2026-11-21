// Local fixtures for the production Next.js server. No external service is contacted.
import { createServer } from 'node:http';
import { randomUUID } from 'node:crypto';
import { readFile } from 'node:fs/promises';
import { spawn } from 'node:child_process';
import { authTestEnv } from '../tests/fixtures/auth.mjs';

const documentId = 'd' + 'b'.repeat(32);
const passageId = 'c' + 'e'.repeat(32);
const runs = new Map();
const searchMode = process.env.WEBUI_SEARCH_MODE ?? 'hybrid';
const lexicalOnly = searchMode === 'lexical';
function json(res, body, status = 200) {
  res.writeHead(status, { 'Content-Type': 'application/json' });
  res.end(JSON.stringify(body));
}
const agent = createServer(async (req, res) => {
  if (req.url === '/ready' && process.env.WEBUI_TEST_OFFLINE === 'agent') return json(res, { status: 'degraded' }, 503);
  if (req.url === '/ready') return json(res, { status: 'ready' });
  if (req.method === 'POST' && req.url === '/runs') {
    let body = '';
    for await (const chunk of req) body += chunk;
    const input = JSON.parse(body);
    if (input.question === 'occupé') return json(res, {}, 429);
    const id = randomUUID().replaceAll('-', '');
    runs.set(id, {
      question: input.question,
      polls: 0,
      createdAt: Date.now(),
      fastId: input.fast_id,
      fastAnswer: input.fast_answer,
      reason: input.reason || input.escalation_reason,
    });
    return json(res, { id, status: 'running' }, 202);
  }
  if (req.method === 'POST' && (req.url === '/ask/fast' || req.url === '/ask')) {
    let body = '';
    for await (const chunk of req) body += chunk;
    const input = JSON.parse(body);
    if (input.question === 'occupé') return json(res, {}, 429);
    if (input.question === 'erreur') {
      res.writeHead(200, {
        'Content-Type': 'text/event-stream; charset=utf-8',
        'Cache-Control': 'no-cache, no-store, private',
        'Connection': 'keep-alive',
      });
      res.write(`event: error\ndata: ${JSON.stringify({ error: 'La recherche ou la génération a échoué. Vous pouvez réessayer.' })}\n\n`);
      res.end();
      return;
    }
    const fastId = randomUUID().replaceAll('-', '');
    res.writeHead(200, {
      'Content-Type': 'text/event-stream; charset=utf-8',
      'Cache-Control': 'no-cache, no-store, private',
      'Connection': 'keep-alive',
    });
    const sources = [
      { id: 1, original_name: 'Notice de test.pdf', document_id: documentId, page_id: passageId, locator: { page: 1, pages: [1] }, excerpt: 'La pression maximale est de 12 bar.' },
    ];
    res.write(`event: init\ndata: ${JSON.stringify({ id: fastId, mode: 'fast', sources })}\n\n`);
    res.write(`event: delta\ndata: ${JSON.stringify({ content: '**Réponse Fast Path** : pression maximale de 12 bar.' })}\n\n`);
    if (input.question === 'attente') {
      await new Promise((resolve) => setTimeout(resolve, 5000));
    }
    res.write(`event: complete\ndata: ${JSON.stringify({ id: fastId, mode: 'fast', answer: '**Réponse Fast Path** : pression maximale de 12 bar.', sources })}\n\n`);
    res.end();
    return;
  }
  const parsed = new URL(req.url, 'http://127.0.0.1');
  const pathParts = parsed.pathname.split('/').filter(Boolean);
  if (pathParts[0] === 'runs') {
    const id = pathParts[1];
    const run = runs.get(id);
    if (!run) return json(res, {}, 404);
    if (pathParts[2] === 'events') {
      const after = Number(parsed.searchParams.get('after') || '0');
      if (run.question === 'erreur') {
        return json(res, { items: [], next_after: 0, next_offset: 0, has_more: false });
      }
      if (run.question === 'progression-sse') {
        const elapsed = Date.now() - (run.createdAt || 0);
        const allEvents = [
          { seq: 1, ts: Date.now() / 1000, kind: 'span_start', category: 'node', name: 'decompose', span_id: 'node-1' },
          { seq: 2, ts: Date.now() / 1000, kind: 'span_start', category: 'tool', name: 'search_text', span_id: 'tool-2', inputs: { question: 'pression maximale' } },
          { seq: 3, ts: Date.now() / 1000, kind: 'retrieval_result', returned: 2, span_id: 'retrieval-3' },
          { seq: 4, ts: Date.now() / 1000, kind: 'span_start', category: 'node', name: 'synthesize', span_id: 'node-4' },
        ];
        let visibleCount = 1;
        if (elapsed >= 500) visibleCount = 3;
        if (elapsed >= 1000) visibleCount = 4;
        const available = allEvents.slice(0, visibleCount);
        const items = available.filter((e) => e.seq > after);
        return json(res, { items, next_after: items.length > 0 ? items.at(-1).seq : after, next_offset: 0, has_more: false });
      }
      const events = [
        { seq: 1, ts: Date.now() / 1000, kind: 'span_start', category: 'node', name: 'decompose', span_id: 'node-1' },
        { seq: 2, ts: Date.now() / 1000, kind: 'retrieval_result', returned: 1, span_id: 'retrieval-1' },
        { seq: 3, ts: Date.now() / 1000, kind: 'span_start', category: 'node', name: 'synthesize', span_id: 'node-3' },
      ];
      const items = events.filter((e) => e.seq > after);
      return json(res, { items, next_after: items.length > 0 ? items.at(-1).seq : after, next_offset: 0, has_more: false });
    }
    if (run.question === 'erreur') return json(res, { id, status: 'failed', error: 'private error' });
    if (run.question === 'attente' || run.question === 'attente-escalate') return json(res, { id, status: 'running' });
    if (run.question === 'progression-sse') {
      const elapsed = Date.now() - (run.createdAt || 0);
      if (elapsed < 1800) return json(res, { id, status: 'running' });
      return json(res, { id, status: 'complete', result: {
        answer: '**Réponse RAG de test** : pression maximale de 12 bar.',
        sources: [{ id: 'S1', original_name: 'Notice de test.pdf', document_id: documentId, page_id: passageId, locator: { page: 1, pages: [1] } }],
      } });
    }
    if (run.polls++ === 0) return json(res, { id, status: 'running' });
    return json(res, { id, status: 'complete', result: {
      answer: '**Réponse RAG de test** : pression maximale de 12 bar.',
      sources: [{ id: 'S1', original_name: 'Notice de test.pdf', document_id: documentId, page_id: passageId, locator: { page: 1, pages: [1] } }],
    } });
  }
});
const pdf = await readFile(new URL('../public/documents/guide-marque.pdf', import.meta.url));
const km = createServer(async (req, res) => {
  if (req.url === '/health' && process.env.WEBUI_TEST_OFFLINE === 'retrieval') return json(res, { status: 'error' }, 503);
  if (req.url === '/health') return json(res, { status: 'ok', backend: 'postgres', capabilities: { lexical: true, semantic: !lexicalOnly, hybrid: !lexicalOnly } });
  if (req.url === '/document-types') return json(res, { items: [
    { type_document_id: 1000, label: 'Tableau de composition illustré', perimeter_code: 'DOC_TECH', perimeter_label: 'Doc Tech' },
    { type_document_id: 2000, label: 'Notice de réparation', perimeter_code: 'RTA', perimeter_label: 'RTA' },
  ] });
  if (req.method === 'POST' && req.url === '/search') {
    let body = '';
    for await (const chunk of req) body += chunk;
    const input = JSON.parse(body);
    if (lexicalOnly && input.mode !== 'lexical') return json(res, {}, 503);
    if (input.question === 'occupé') return json(res, {}, 429);
    if (input.question === 'vide') return json(res, { results: [] });
    if (input.question === 'attente') await new Promise((resolve) => setTimeout(resolve, 5000));
    const results = [
      { id: passageId, document_id: documentId, score: .9, original_name: 'Notice de test.pdf', locator: { pages: [1] }, text: 'La pression maximale est de 12 bar.' },
      { document_id: documentId, score: .8, original_name: 'Notice de test.pdf', locator: { pages: [1] }, text: '<script>alert(1)</script> : vérifier la soupape.' },
      { document_id: 'd' + 'c'.repeat(32), score: .7, original_name: 'Entretien.pdf', locator: { pages: [1] }, text: 'Contrôler le circuit chaque année.' },
    ];
    const scoped = Boolean(input.perimeter_code || input.type_document_ids?.length || input.date_filters);
    const allowed = results.filter((hit) => {
      const [id, perimeter] = hit.document_id === documentId ? [1000, 'DOC_TECH'] : [2000, 'RTA'];
      const dates = id === 1000 ? { edition: '2025-01-01', approval: '2025-02-01', repeal: '2025-04-01' } : { edition: '2024-12-31' };
      const matchesDates = Object.entries(input.date_filters ?? {}).every(([key, { from, to }]) =>
        dates[key] && (!from || dates[key] >= from) && (!to || dates[key] <= to));
      return matchesDates && (!input.perimeter_code || input.perimeter_code === perimeter) &&
        (!input.type_document_ids?.length || input.type_document_ids.includes(id));
    });
    return json(res, { results: allowed.slice(0, input.limit), ...(scoped ? { scope_applied: true } : {}) });
  }
  if (req.url === `/passages/${passageId}`) return json(res, {
    id: passageId, document_id: documentId, source_sha256: 'a'.repeat(64),
    blocks: [{ type: 'text', text: 'La pression maximale est de 12 bar.' }],
    record: { locations: [{ page_no: 1, bbox: [30, 60, 160, 90], coord_origin: 'TOPLEFT' }] },
  });
  if (new URL(req.url, 'http://fixture').pathname === `/documents/${documentId}/source-reference`) return json(res,{document_id:documentId,relative_path:'_versions/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.pdf'});
  return json(res,{},404);
});
const listen = (server) => new Promise((resolve, reject) => {
  server.once('error', reject);
  server.listen(0, '127.0.0.1', () => resolve(`http://127.0.0.1:${server.address().port}`));
});
const files = createServer((req,res)=>{
  if (req.url === '/health') return res.end('OK');
  const token = new URL(req.url,'http://fixture').searchParams.get('token');
  if (!token || token.split('.').length !== 3) return json(res,{},401);
  res.writeHead(200, {'Content-Type':'application/pdf','Content-Length':pdf.byteLength,'Accept-Ranges':'bytes',
    'Access-Control-Allow-Origin':`http://127.0.0.1:${process.env.WEBUI_TEST_PORT ?? '4187'}`});
  res.end(req.method === 'HEAD' ? undefined : pdf);
});
const filesUrl = await listen(files);
const agentUrl = await listen(agent);
const kmUrl = await listen(km);
import { existsSync } from 'node:fs';
const port = process.env.WEBUI_TEST_PORT ?? '4187';
const serverScript = existsSync(new URL('../.next/standalone/server.js', import.meta.url))
  ? [new URL('../.next/standalone/server.js', import.meta.url).pathname]
  : ['node_modules/next/dist/bin/next', 'start', '--hostname', '127.0.0.1', '--port', port];
const child = spawn(process.execPath, serverScript, {
  stdio: 'inherit', env: { ...process.env,
    PORT: port, HOSTNAME: '127.0.0.1',
    // Foreign standard Auth.js variables must not override the WebUI namespace.
    AUTH_URL: 'https://other-application.invalid', NEXTAUTH_URL: 'https://legacy-application.invalid',
    AUTH_SECRET: 'other-application-only-secret-at-least-32-characters',
    AUTH_SECRET_1: 'other-application-rotated-secret-at-least-32-characters',
    ...authTestEnv, KB_ENV: 'dev', WEBUI_DATABASE_URL: '',
    WEBUI_LOGIN_IP_RPS:'100',WEBUI_LOGIN_IP_BURST:'1000',WEBUI_LOGIN_ACCOUNT_RPS:'100',WEBUI_LOGIN_ACCOUNT_BURST:'1000',
    WEBUI_CHAT_RATE_LIMIT_RPS:'100', WEBUI_CHAT_RATE_LIMIT_BURST:'100', WEBUI_SEARCH_RATE_LIMIT_RPS:'100', WEBUI_SEARCH_RATE_LIMIT_BURST:'100',
    WEBUI_AUTH_URL: `http://127.0.0.1:${port}`,
    FILESERVER_PUBLIC_URL:filesUrl, FILESERVER_INTERNAL_URL:filesUrl, FILESERVER_JWT_SECRET:'fixture-only-fileserver-secret-with-32-characters',
    WEBUI_AGENT_URL: agentUrl, WEBUI_RERANKER_URL: agentUrl, WEBUI_KM_API_URL: kmUrl, WEBUI_CHAT_SECONDS: '30', WEBUI_SEARCH_MODE: searchMode,
  },
});
function stop() { child.kill('SIGTERM'); agent.close(); km.close(); files.close(); }
process.on('SIGTERM', stop);
process.on('SIGINT', stop);
child.on('exit', (code) => { agent.close(); km.close(); files.close(); process.exitCode = code ?? 0; });
