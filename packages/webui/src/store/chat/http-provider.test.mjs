import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readSseStream, HttpChatProvider, ChatRequestError } from './http-provider.ts';
import copy from '../../content/fr.json' with { type: 'json' };

const signal = () => new AbortController().signal;

test('readSseStream parses single and multiple events correctly', async () => {
  const encoder = new TextEncoder();
  const chunks = [
    encoder.encode('id: 1\nevent: activity\ndata: {"step":"one"}\n\n'),
    encoder.encode('id: 2\nevent: activity\ndata: {"step":"two"}\n\n'),
  ];

  const stream = new ReadableStream({
    start(controller) {
      for (const chunk of chunks) controller.enqueue(chunk);
      controller.close();
    },
  });

  const messages = [];
  await readSseStream(stream, signal(), (msg) => messages.push(msg));

  assert.equal(messages.length, 2);
  assert.deepEqual(messages[0], { id: '1', event: 'activity', data: '{"step":"one"}' });
  assert.deepEqual(messages[1], { id: '2', event: 'activity', data: '{"step":"two"}' });
});

test('readSseStream correctly handles split UTF-8 characters across chunks', async () => {
  // 'é' is 0xC3 0xA9 in UTF-8
  // '€' is 0xE2 0x82 0xAC in UTF-8
  const fullText = 'id: 1\nevent: message\ndata: café €uro\n\n';
  const fullBytes = new TextEncoder().encode(fullText);

  // Split right in the middle of 'é' (0xC3 / 0xA9)
  const eIndex = fullText.indexOf('é');
  const splitIndex = new TextEncoder().encode(fullText.slice(0, eIndex)).length + 1; // splits 0xC3 and 0xA9

  const chunk1 = fullBytes.subarray(0, splitIndex);
  const chunk2 = fullBytes.subarray(splitIndex);

  const stream = new ReadableStream({
    start(controller) {
      controller.enqueue(chunk1);
      controller.enqueue(chunk2);
      controller.close();
    },
  });

  const messages = [];
  await readSseStream(stream, signal(), (msg) => messages.push(msg));

  assert.equal(messages.length, 1);
  assert.equal(messages[0].data, 'café €uro');
});

test('readSseStream handles CRLF line breaks, comments, and multiline data', async () => {
  const encoder = new TextEncoder();
  const text = ': ping comment\r\nid: 42\r\nevent: multi\r\ndata: line 1\r\ndata: line 2\r\n\r\n: trailing comment\r\n';
  const stream = new ReadableStream({
    start(controller) {
      controller.enqueue(encoder.encode(text));
      controller.close();
    },
  });

  const messages = [];
  await readSseStream(stream, signal(), (msg) => messages.push(msg));

  assert.equal(messages.length, 1);
  assert.deepEqual(messages[0], { id: '42', event: 'multi', data: 'line 1\nline 2' });
});

test('HttpChatProvider uses SSE with onProgress, updates activities, and yields answer once', async () => {
  const runId = 'a'.repeat(32) + '.' + '1'.repeat(13) + '.' + 'b'.repeat(64);
  const encoder = new TextEncoder();
  const calls = [];
  const progressReports = [];

  const provider = new HttpChatProvider({
    sse: true,
    fetch: async (url, init) => {
      calls.push({ url, init });
      if (url === '/api/chat/runs') {
        return Response.json({ id: runId, status: 'running', timeoutMs: 30000 });
      }
      if (url.includes('/events')) {
        const stream = new ReadableStream({
          start(controller) {
            controller.enqueue(encoder.encode('id: 1\nevent: activity\ndata: {"seq":1,"summary":"Recherche","step":{"id":"s1","phase":"search","label":"Recherche","status":"running"}}\n\n'));
            controller.enqueue(encoder.encode(': ping\n\n'));
            controller.enqueue(encoder.encode('id: 2\nevent: complete\ndata: {"answer":"Réponse SSE test","sources":[]}\n\n'));
            controller.close();
          },
        });
        return new Response(stream, {
          headers: { 'Content-Type': 'text/event-stream' },
        });
      }
      return Response.json({}, { status: 404 });
    },
  });

  const chunks = [];
  for await (const chunk of provider.stream({
    messages: [{ id: '1', role: 'user', content: 'Bonjour' }],
    attachments: [],
    signal: signal(),
    onProgress: (p) => progressReports.push(p),
  })) {
    chunks.push(chunk);
  }

  // Final answer yielded once
  assert.deepEqual(chunks, ['Réponse SSE test']);
  // onProgress called with activity transitions (initial running, activity update, completion)
  assert.equal(progressReports.length, 3);
  assert.equal(progressReports[0].status, 'running');
  assert.equal(progressReports[1].summary, 'Recherche');
  assert.equal(progressReports[1].steps.length, 1);
  assert.equal(progressReports[1].steps[0].id, 's1');
  assert.equal(progressReports[2].status, 'complete');
});

test('HttpChatProvider falls back to polling when SSE endpoint returns non-SSE response', async () => {
  const runId = 'a'.repeat(32) + '.' + '1'.repeat(13) + '.' + 'b'.repeat(64);
  const calls = [];

  let polls = 0;
  const provider = new HttpChatProvider({
    sse: true,
    pollMs: 1,
    fetch: async (url, init) => {
      calls.push({ url, init });
      if (url === '/api/chat/runs') {
        return Response.json({ id: runId, status: 'running', timeoutMs: 30000 });
      }
      if (url.includes('/events')) {
        // SSE route returns 404 or non-SSE
        return new Response('Not found', { status: 404 });
      }
      if (url === `/api/chat/runs/${runId}`) {
        polls++;
        if (polls === 1) return Response.json({ status: 'running' });
        return Response.json({ status: 'complete', answer: 'Réponse polling fallback', sources: [] });
      }
      return Response.json({}, { status: 404 });
    },
  });

  const chunks = [];
  for await (const chunk of provider.stream({
    messages: [{ id: '1', role: 'user', content: 'Fallback test' }],
    attachments: [],
    signal: signal(),
  })) {
    chunks.push(chunk);
  }

  assert.deepEqual(chunks, ['Réponse polling fallback']);
  // Verifies that polling was executed after SSE 404
  assert.ok(calls.some(c => c.url.includes('/events')));
  assert.ok(calls.some(c => c.url === `/api/chat/runs/${runId}`));
});

test('readSseStream chunk split CRLF edge case produces exactly 1 message instead of 3', async () => {
  const encoder = new TextEncoder();
  const rawChunks = [
    'id: 1\r',
    '\nevent: activity\r',
    '\ndata: {"phase":"search"}\r',
    '\n\r',
    '\n',
  ];
  const stream = new ReadableStream({
    start(controller) {
      for (const ch of rawChunks) controller.enqueue(encoder.encode(ch));
      controller.close();
    },
  });

  const messages = [];
  await readSseStream(stream, signal(), (msg) => messages.push(msg));

  assert.equal(messages.length, 1);
  assert.deepEqual(messages[0], { id: '1', event: 'activity', data: '{"phase":"search"}' });
});

test('readSseStream discards incomplete frame at EOF without blank line and ignores id/event without data', async () => {
  const encoder = new TextEncoder();
  // id: 1 without data, followed by incomplete data without terminating empty line
  const text = 'id: 1\nevent: ping\n\nid: 2\nevent: activity\ndata: partial';
  const stream = new ReadableStream({
    start(controller) {
      controller.enqueue(encoder.encode(text));
      controller.close();
    },
  });

  const messages = [];
  await readSseStream(stream, signal(), (msg) => messages.push(msg));

  // The id:1 event had no data, and partial was not followed by a blank line at EOF -> 0 dispatched
  assert.equal(messages.length, 0);
});

test('readSseStream reader abort terminates immediately on hanging stream and throws signal reason', async () => {
  let readerCanceled = false;
  const hangingStream = new ReadableStream({
    start() {},
    cancel() {
      readerCanceled = true;
    },
  });

  const abortController = new AbortController();
  const readPromise = readSseStream(hangingStream, abortController.signal, () => {});

  // Abort after small microtask tick
  await new Promise((r) => setTimeout(r, 5));
  const abortReason = new Error('Client requested abort');
  abortController.abort(abortReason);

  await assert.rejects(readPromise, (err) => {
    assert.equal(err, abortReason);
    return true;
  });
  assert.equal(readerCanceled, true);
});

test('HttpChatProvider exits immediately on complete event without waiting for stream EOF', async () => {
  const runId = 'a'.repeat(32) + '.' + '1'.repeat(13) + '.' + 'b'.repeat(64);
  const encoder = new TextEncoder();
  let streamClosed = false;

  const provider = new HttpChatProvider({
    sse: true,
    fetch: async (url) => {
      if (url === '/api/chat/runs') {
        return Response.json({ id: runId, status: 'running', timeoutMs: 30000 });
      }
      if (url.includes('/events')) {
        const stream = new ReadableStream({
          start(controller) {
            controller.enqueue(encoder.encode('id: 1\nevent: complete\ndata: {"answer":"Immediate answer","sources":[]}\n\n'));
            // Notice: do NOT call controller.close()! Stream remains open infinitely
          },
          cancel() {
            streamClosed = true;
          },
        });
        return new Response(stream, { headers: { 'Content-Type': 'text/event-stream' } });
      }
      return Response.json({}, { status: 404 });
    },
  });

  const chunks = [];
  for await (const chunk of provider.stream({
    messages: [{ id: '1', role: 'user', content: 'Test stream' }],
    attachments: [],
    signal: signal(),
  })) {
    chunks.push(chunk);
  }

  assert.deepEqual(chunks, ['Immediate answer']);
  assert.equal(streamClosed, true);
});

test('HttpChatProvider deduplicates seen seq and preserves step fusion on span_end', async () => {
  const runId = 'a'.repeat(32) + '.' + '1'.repeat(13) + '.' + 'b'.repeat(64);
  const encoder = new TextEncoder();
  const progressReports = [];

  const provider = new HttpChatProvider({
    sse: true,
    fetch: async (url) => {
      if (url === '/api/chat/runs') {
        return Response.json({ id: runId, status: 'running', timeoutMs: 30000 });
      }
      if (url.includes('/events')) {
        const stream = new ReadableStream({
          start(controller) {
            // Step 1: retrieval_result provides detail and count
            controller.enqueue(encoder.encode('id: 2\nevent: activity\ndata: {"seq":1,"summary":"Recherche","step":{"id":"span-1","phase":"search","label":"Recherche textuelle","status":"complete","detail":"5 passage(s) trouvé(s)","count":5}}\n\n'));
            // Duplicate seq:1 replayed -> must be ignored
            controller.enqueue(encoder.encode('id: 2\nevent: activity\ndata: {"seq":1,"summary":"Recherche","step":{"id":"span-1","phase":"search","label":"Recherche textuelle","status":"complete","detail":"5 passage(s) trouvé(s)","count":5}}\n\n'));
            // Step 2: span_end arrives without detail and count -> must preserve fusion!
            controller.enqueue(encoder.encode('id: 3\nevent: activity\ndata: {"seq":2,"summary":"Recherche","step":{"id":"span-1","phase":"search","label":"Recherche textuelle","status":"complete","durationMs":150}}\n\n'));
            controller.enqueue(encoder.encode('id: 4\nevent: complete\ndata: {"answer":"Done","sources":[]}\n\n'));
          },
        });
        return new Response(stream, { headers: { 'Content-Type': 'text/event-stream' } });
      }
      return Response.json({}, { status: 404 });
    },
  });

  const chunks = [];
  for await (const chunk of provider.stream({
    messages: [{ id: '1', role: 'user', content: 'Fusion test' }],
    attachments: [],
    signal: signal(),
    onProgress: (p) => progressReports.push(structuredClone(p)),
  })) {
    chunks.push(chunk);
  }

  assert.deepEqual(chunks, ['Done']);
  // Initial + seq 1 + seq 2 (fusion) + complete = 4 progress reports (duplicate seq 1 was skipped)
  assert.equal(progressReports.length, 4);
  const reportAfterSpanEnd = progressReports[2];
  const fusedStep = reportAfterSpanEnd.steps.find((s) => s.id === 'span-1');
  assert.equal(fusedStep?.detail, '5 passage(s) trouvé(s)');
  assert.equal(fusedStep?.count, 5);
  assert.equal(fusedStep?.durationMs, 150);
});

test('readSseStream parses valid 70,000 character and multibyte UTF-8 complete event without line limit error', async () => {
  const encoder = new TextEncoder();
  // 70k characters with accents and emojis (multibyte UTF-8: é is 2 bytes, 🌟 is 4 bytes / 2 code units, € is 3 bytes)
  const largeAnswer = 'é'.repeat(30000) + '🌟'.repeat(10000) + '€'.repeat(20000);
  assert.equal(largeAnswer.length, 70000);

  const payload = JSON.stringify({ answer: largeAnswer, sources: [] });
  const rawEvent = `id: 1\nevent: complete\ndata: ${payload}\n\n`;
  const stream = new ReadableStream({
    start(controller) {
      controller.enqueue(encoder.encode(rawEvent));
      controller.close();
    },
  });

  const messages = [];
  await readSseStream(stream, signal(), (msg) => {
    messages.push(msg);
  });

  assert.equal(messages.length, 1);
  assert.equal(messages[0].event, 'complete');
  const parsed = JSON.parse(messages[0].data);
  assert.equal(parsed.answer.length, 70000);
  assert.equal(parsed.answer, largeAnswer);
});

test('readSseStream rejects oversized line with localized fr.json invalidResponse text instead of English', async () => {
  const encoder = new TextEncoder();
  // Single line exceeding MAX_LINE_LENGTH (8MB)
  const hugeLine = 'data: ' + 'x'.repeat(8 * 1024 * 1024 + 10) + '\n\n';
  const stream = new ReadableStream({
    start(controller) {
      controller.enqueue(encoder.encode(hugeLine));
      controller.close();
    },
  });

  await assert.rejects(
    readSseStream(stream, signal(), () => {}),
    (err) => {
      assert.ok(err instanceof ChatRequestError);
      // Must use French localized string, no English technical text
      assert.equal(err.message, copy.agentChat.invalidResponse);
      assert.equal(err.message.includes('SSE line limit'), false);
      return true;
    },
  );
});

test('readSseStream stops immediately when async onMessage returns Promise<false>', async () => {
  const encoder = new TextEncoder();
  const text = 'id: 1\nevent: activity\ndata: {"step":1}\n\nid: 2\nevent: activity\ndata: {"step":2}\n\n';
  const stream = new ReadableStream({
    start(controller) {
      controller.enqueue(encoder.encode(text));
      controller.close();
    },
  });

  const dispatched = [];
  await readSseStream(stream, signal(), async (msg) => {
    dispatched.push(msg);
    // Async callback returning Promise<false>
    await new Promise((r) => setTimeout(r, 1));
    return false;
  });

  // Should have stopped after step 1!
  assert.equal(dispatched.length, 1);
  assert.equal(dispatched[0].id, '1');
});

test('HttpChatProvider resumes at checkpoint cursor without double +1 across filtered stream interruption', async () => {
  const runId = 'a'.repeat(32) + '.' + '1'.repeat(13) + '.' + 'b'.repeat(64);
  const encoder = new TextEncoder();
  const requestedUrls = [];
  const requestedHeaders = [];
  let connectionCount = 0;

  const provider = new HttpChatProvider({
    sse: true,
    fetch: async (url, init) => {
      requestedUrls.push(url);
      if (init?.headers) requestedHeaders.push(init.headers);
      if (url === '/api/chat/runs') {
        return Response.json({ id: runId, status: 'running', timeoutMs: 30000 });
      }
      if (url.includes('/events')) {
        connectionCount++;
        if (connectionCount === 1) {
          // Stream 1 emits a checkpoint cursor: 7, then cuts
          const stream = new ReadableStream({
            start(controller) {
              controller.enqueue(encoder.encode('id: 7\nevent: checkpoint\ndata: {"cursor":7}\n\n'));
              controller.close(); // stream drops!
            },
          });
          return new Response(stream, { headers: { 'Content-Type': 'text/event-stream' } });
        }
        if (connectionCount === 2) {
          // Stream 2 should be called with after=7 (not 8 / not double +1)
          const stream = new ReadableStream({
            start(controller) {
              controller.enqueue(encoder.encode('id: 8\nevent: complete\ndata: {"answer":"Checkpoint resume success","sources":[]}\n\n'));
              controller.close();
            },
          });
          return new Response(stream, { headers: { 'Content-Type': 'text/event-stream' } });
        }
      }
      return Response.json({}, { status: 404 });
    },
  });

  const chunks = [];
  for await (const chunk of provider.stream({
    messages: [{ id: '1', role: 'user', content: 'Checkpoint test' }],
    attachments: [],
    signal: signal(),
  })) {
    chunks.push(chunk);
  }

  assert.deepEqual(chunks, ['Checkpoint resume success']);
  // Connection 1 had no cursor, Connection 2 had after=7
  const eventUrls = requestedUrls.filter((u) => u.includes('/events'));
  assert.equal(eventUrls.length, 2);
  assert.equal(eventUrls[0].includes('after='), false);
  assert.ok(eventUrls[1].includes('after=7'));
});

test('HttpChatProvider notifyProgress uses reconnecting string and switches summary during fallback polling', async () => {
  const runId = 'a'.repeat(32) + '.' + '1'.repeat(13) + '.' + 'b'.repeat(64);
  const progressReports = [];
  let pollCount = 0;

  const provider = new HttpChatProvider({
    sse: true,
    pollMs: 1,
    fetch: async (url) => {
      if (url === '/api/chat/runs') {
        return Response.json({ id: runId, status: 'running', timeoutMs: 30000 });
      }
      if (url.includes('/events')) {
        // SSE fails immediately -> reconnect attempts -> polling fallback
        return new Response('Unavailable', { status: 503 });
      }
      if (url === `/api/chat/runs/${runId}`) {
        pollCount++;
        if (pollCount === 1) return Response.json({ status: 'running' });
        return Response.json({ status: 'complete', answer: 'Polling done', sources: [] });
      }
      return Response.json({}, { status: 404 });
    },
  });

  const chunks = [];
  for await (const chunk of provider.stream({
    messages: [{ id: '1', role: 'user', content: 'Progress status test' }],
    attachments: [],
    signal: signal(),
    onProgress: (p) => progressReports.push(structuredClone(p)),
  })) {
    chunks.push(chunk);
  }

  assert.deepEqual(chunks, ['Polling done']);

  // Check that reconnecting progress report has copy.agentChat.activities.reconnecting
  const reconnectingReport = progressReports.find((p) => p.status === 'reconnecting');
  assert.ok(reconnectingReport);
  assert.equal(reconnectingReport.summary, copy.agentChat.activities.reconnecting);

  // Check that fallback polling report uses fallbackSummary
  const fallbackReport = progressReports.find((p) => p.summary === copy.agentChat.activities.fallbackSummary);
  assert.ok(fallbackReport);
});


test('HttpChatProvider resumes after the last public event when the connection closes before its checkpoint', async () => {
  const token = 'a'.repeat(32) + '.' + '1'.repeat(13) + '.' + 'b'.repeat(64);
  const encoder = new TextEncoder();
  const requests = [];
  let starts = 0;
  const provider = new HttpChatProvider({ sse: true, fetch: async (url, init) => {
    if (url === '/api/chat/runs') {
      starts++;
      return Response.json({ id: token, status: 'running', timeoutMs: 30000 });
    }
    requests.push({ url, headers: init.headers });
    const data = requests.length === 1
      ? 'id: 5\nevent: activity\ndata: ' + JSON.stringify({ seq: 4, summary: 'Recherche', step: {
          id: 'search-1', type: 'tool', phase: 'search', label: 'Recherche', status: 'running', timestamp: Date.now(),
        } }) + '\n\n'
      : 'id: 6\nevent: complete\ndata: {"answer":"Réponse finale","sources":[]}\n\n';
    return new Response(new ReadableStream({ start(controller) {
      controller.enqueue(encoder.encode(data));
      controller.close();
    } }), { headers: { 'Content-Type': 'text/event-stream' } });
  } });
  const answers = [];
  for await (const answer of provider.stream({ messages: [{ role: 'user', content: 'Question' }],
    attachments: [], signal: signal(), onProgress() {} })) answers.push(answer);
  assert.equal(starts, 1);
  assert.equal(requests.length, 2);
  assert.equal(new URL(requests[1].url, 'http://webui').searchParams.get('after'), '5');
  assert.equal(requests[1].headers['Last-Event-ID'], '5');
  assert.deepEqual(answers, ['Réponse finale']);
});


test('HTTP chat sends the latest question with recent bounded context in chronological order',async()=>{
  const calls=[];
  const provider=new HttpChatProvider({fetch:async(url,init)=>{
    calls.push(JSON.parse(init.body));throw new Error('captured request');
  }});
  const history=Array.from({length:30},(_,i)=>({role:i%2?'assistant':'user',content:String(i)+':'+ 'x'.repeat(5000)}));
  const consume=async()=>{for await(const _ of provider.stream({messages:[...history,{role:'user',content:'Et sa date ?'}],attachments:[],signal:signal()})) {}};
  await assert.rejects(consume());
  assert.equal(calls[0].question,'Et sa date ?');
  assert.equal(calls[0].history.length,3);
  assert.deepEqual(calls[0].history.map(t=>t.content.slice(0,3)),['27:','28:','29:']);
  assert.equal(calls[0].history.reduce((n,t)=>n+t.content.length,0),12000);
});

test('HttpChatProvider streams from /api/chat/fast when mode is fast and captures lastFastId and lastSources', async () => {
  const encoder = new TextEncoder();
  const requests = [];
  const sseData = [
    'event: init\ndata: {"id":"fast-123"}\n\n',
    'event: delta\ndata: {"delta":"Bonjour "}\n\n',
    'event: delta\ndata: {"delta":"le monde !"}\n\n',
    'event: complete\ndata: {"id":"fast-123","answer":"Bonjour le monde !","sources":[{"id":1,"title":"Doc 1","excerpt":"Extrait","pages":"1","category":"Guide"}]}\n\n',
  ].join('');

  const provider = new HttpChatProvider({
    fetch: async (url, init) => {
      requests.push({ url, body: JSON.parse(init.body) });
      return new Response(
        new ReadableStream({
          start(controller) {
            controller.enqueue(encoder.encode(sseData));
            controller.close();
          },
        }),
        { headers: { 'Content-Type': 'text/event-stream' } },
      );
    },
  });

  const chunks = [];
  for await (const chunk of provider.stream({
    messages: [{ role: 'user', content: 'Quelle est la procédure ?' }],
    attachments: [],
    mode: 'fast',
    signal: signal(),
  })) {
    chunks.push(chunk);
  }

  assert.equal(requests.length, 1);
  assert.equal(requests[0].url, '/api/chat/fast');
  assert.equal(requests[0].body.question, 'Quelle est la procédure ?');
  assert.equal(requests[0].body.mode, 'fast');
  assert.deepEqual(chunks, ['Bonjour ', 'le monde !']);
  assert.equal(provider.lastMode, 'fast');
  assert.equal(provider.lastFastId, 'fast-123');
  assert.equal(provider.sources?.length, 1);
  assert.equal(provider.sources[0].title, 'Doc 1');
});

test('HttpChatProvider forwards escalation context (fastId, fastAnswer, reason) to /api/chat/runs when mode is deep', async () => {
  const requests = [];
  const runId = 'a'.repeat(32) + '.' + '1'.repeat(13) + '.' + 'f'.repeat(64);
  const encoder = new TextEncoder();

  const provider = new HttpChatProvider({
    sse: true,
    fetch: async (url, init) => {
      requests.push({ url, body: init?.body ? JSON.parse(init.body) : undefined });
      if (url === '/api/chat/runs') {
        return Response.json({ id: runId, status: 'running', timeoutMs: 30000 });
      }
      if (url.includes('/events')) {
        const streamData = 'id: 1\nevent: complete\ndata: {"answer":"Réponse approfondie","sources":[]}\n\n';
        return new Response(
          new ReadableStream({
            start(controller) {
              controller.enqueue(encoder.encode(streamData));
              controller.close();
            },
          }),
          { headers: { 'Content-Type': 'text/event-stream' } },
        );
      }
      return Response.json({}, { status: 404 });
    },
  });

  const chunks = [];
  for await (const chunk of provider.stream({
    messages: [{ role: 'user', content: 'Question' }],
    attachments: [],
    mode: 'deep',
    fastId: 'fast-456',
    fastAnswer: 'Réponse rapide précédente',
    reason: 'Non satisfait',
    signal: signal(),
  })) {
    chunks.push(chunk);
  }

  assert.equal(requests[0].url, '/api/chat/runs');
  assert.equal(requests[0].body.mode, 'deep');
  assert.equal(requests[0].body.fast_id, 'fast-456');
  assert.equal(requests[0].body.fast_answer, 'Réponse rapide précédente');
  assert.equal(requests[0].body.reason, 'Non satisfait');
  assert.deepEqual(chunks, ['Réponse approfondie']);
  assert.equal(provider.lastMode, 'deep');
});
