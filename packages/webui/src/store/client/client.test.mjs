import { test, after } from 'node:test';
import assert from 'node:assert/strict';
import { build } from 'rolldown';
import { mkdtemp, mkdir, writeFile, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join, dirname } from 'node:path';
import { pathToFileURL } from 'node:url';
const bundle = await build({
  input: {
    client: new URL('./index.ts', import.meta.url).pathname,
    model: new URL('../chat/model.ts', import.meta.url).pathname,
    store: new URL('../index.ts', import.meta.url).pathname,
    adapter: new URL('../../components/chat/ui-client-adapter.ts', import.meta.url).pathname,
    route: new URL('../../app/api/ui-demo/route.ts', import.meta.url).pathname,
  },
  write: false,
  output: {
    format: 'es',
    entryFileNames: '[name].mjs',
    chunkFileNames: 'chunks/[name]-[hash].mjs',
  },
});
const directory = await mkdtemp(join(tmpdir(), 'reno-client-test-'));
for (const output of bundle.output)
  if (output.type === 'chunk') {
    const file = join(directory, output.fileName);
    await mkdir(dirname(file), { recursive: true });
    await writeFile(file, output.code);
  }
const { createUiClient, createUiCommands, parseUiCommands, UiClientError } = await import(
  pathToFileURL(join(directory, 'client.mjs')).href
);
const { ChatModel } = await import(pathToFileURL(join(directory, 'model.mjs')).href);
const { createChatUiAdapter } = await import(pathToFileURL(join(directory, 'adapter.mjs')).href);
const { POST } = await import(pathToFileURL(join(directory, 'route.mjs')).href);
after(() => rm(directory, { recursive: true, force: true }));
const source = {
  id: 1,
  title: 'Cited document',
  category: 'Test',
  excerpt: 'Excerpt',
  pages: '1',
  pdf: { url: '/documents/cited.pdf', page: 1 },
};
function setup(options) {
  const notifications = [],
    votes = [],
    choices = [];
  const client = createUiClient(options);
  const ui = client.store;
  const chat = new ChatModel(
    {
      id: 'conversation',
      title: 'Test',
      messages: [
        { id: 'question', role: 'user', content: 'Question' },
        { id: 'answer', role: 'assistant', content: 'Response', sources: [source] },
      ],
    },
    {
      sources: [],
      async *stream() {
        yield 'Generated';
      },
    },
    () => {},
  );
  client.attach(
    createChatUiAdapter({
      ui,
      chat,
      thumb: (...args) => votes.push(args),
      choose: (...args) => choices.push(args),
    }),
  );
  const seen = new Set();
  ui.subscribe(() =>
    ui.getState().notifications.forEach((notice) => {
      if (!seen.has(notice.id)) {
        seen.add(notice.id);
        notifications.push([notice.message, notice.kind]);
      }
    }),
  );
  return { client, ui, chat, notifications, votes, choices };
}
test('typed client controls UI, scoped drafts, feedback and existing citations', () => {
  const { client, ui, chat, votes, choices } = setup();
  client.theme.set('dark');
  client.sidebar.set({ width: 280, collapsed: true });
  client.chat.setDraft('Draft');
  client.modal.open('Title', '<img src=x onerror=bad()>');
  assert.equal(ui.getState().theme, 'dark');
  assert.equal(ui.getState().sidebar.width, 280);
  assert.equal(chat.active.draft, 'Draft');
  assert.equal(ui.getState().modal.body, '<img src=x onerror=bad()>');
  client.sources.open('answer', 1);
  assert.equal(ui.getState().source.title, source.title);
  client.feedback.open('answer');
  assert.equal(ui.getState().feedbackId, 'answer');
  client.feedback.vote('answer', 'positive');
  client.chat.choose('answer', 'b');
  assert.deepEqual(votes, [['answer', 'positive']]);
  assert.deepEqual(choices, [['answer', 'b']]);
  client.sources.close();
  client.feedback.close();
  client.modal.close();
  client.annotations.setOpen(true);
  client.announce('Ready');
  assert.equal(ui.getState().source, null);
  assert.equal(ui.getState().feedbackId, null);
  assert.equal(ui.getState().modal, null);
  assert.equal(ui.getState().annotationsOpen, true);
});
test('a batch validates all commands and targets before changing any state', () => {
  const { client, ui, notifications } = setup();
  const before = ui.getState();
  assert.throws(
    () =>
      client.receive({
        version: 1,
        commands: [{ type: 'theme.set', value: 'dark' }, { type: 'unknown' }],
      }),
    UiClientError,
  );
  assert.equal(ui.getState(), before);
  assert.throws(
    () =>
      client.receive(
        createUiCommands(
          { type: 'notification.show', message: 'Premature' },
          { type: 'source.open', messageId: 'answer', sourceId: 999 },
        ),
      ),
    (error) => error.code === 'target',
  );
  assert.equal(ui.getState(), before);
  assert.deepEqual(notifications, []);
  assert.throws(
    () => client.sources.open('question', 1),
    (error) => error.code === 'target',
  );
  assert.throws(
    () =>
      client.receive(
        createUiCommands({ type: 'draft.set', conversationId: 'missing', value: 'Wrong' }),
      ),
    (error) => error.code === 'target',
  );
});
test('wire validation rejects extra fields, invalid values, unsupported versions and large batches', () => {
  for (const payload of [
    null,
    { version: 2, commands: [] },
    { version: 1, commands: Array(51).fill({ type: 'source.close' }) },
    { version: 1, commands: [{ type: 'sidebar.set', width: NaN }] },
    { version: 1, commands: [{ type: 'sidebar.set', width: 361 }] },
    { version: 1, commands: [{ type: 'sidebar.set' }] },
    { version: 1, commands: [{ type: 'theme.set', value: 'dark', html: 'unsafe' }] },
    JSON.parse('{"version":1,"commands":[{"type":"source.close","__proto__":{}}]}'),
  ])
    assert.throws(
      () => parseUiCommands(payload),
      (error) => error.code === 'protocol',
    );
});
test('commands are copied and combined store subscriptions observe one final state per batch', () => {
  const { client, ui, chat } = setup();
  let uiCalls = 0,
    chatCalls = 0,
    selectedCalls = 0;
  ui.subscribe(() => uiCalls++);
  chat.store.subscribe(() => chatCalls++);
  client.subscribe(
    (state) => state,
    () => selectedCalls++,
  );
  const payload = createUiCommands(
    { type: 'theme.set', value: 'dark' },
    { type: 'sidebar.set', width: 270, collapsed: true },
    { type: 'draft.set', conversationId: 'conversation', value: 'Draft' },
  );
  client.receive(payload);
  payload.commands[0].value = 'light';
  assert.equal(ui.getState().theme, 'dark');
  assert.equal(uiCalls, 1);
  assert.equal(chatCalls, 1);
  assert.equal(selectedCalls, 1);
  assert.equal(client.getState(), client.getState());
});
test('client subscriptions are selective, isolated and cleaned by reusable disposal', () => {
  const first = setup().client,
    second = setup().client;
  let calls = 0;
  first.subscribe(
    (state) => state.theme,
    () => calls++,
  );
  first.sidebar.set({ collapsed: true });
  assert.equal(calls, 0);
  first.theme.set('dark');
  assert.equal(calls, 1);
  assert.equal(second.getState().theme, 'light');
  first.dispose();
  first.theme.set('light');
  assert.equal(calls, 1);
  const off = first.subscribe(
    (state) => state.theme,
    () => calls++,
  );
  first.theme.set('dark');
  assert.equal(calls, 2);
  off();
});
test('request applies JSON returned by a TypeScript backend', async () => {
  const { client, ui } = setup({
    fetch: async () => Response.json(createUiCommands({ type: 'theme.set', value: 'dark' })),
  });
  await client.request('/api/ui');
  assert.equal(ui.getState().theme, 'dark');
});
test('a late response never overwrites a newer response on the same channel', async () => {
  const responses = [];
  const { client, ui } = setup({ fetch: () => new Promise((resolve) => responses.push(resolve)) });
  const old = client.request('/old').catch((error) => error);
  const current = client.request('/current');
  responses[1](Response.json(createUiCommands({ type: 'theme.set', value: 'dark' })));
  await current;
  responses[0](Response.json(createUiCommands({ type: 'theme.set', value: 'light' })));
  assert.equal((await old).name, 'AbortError');
  assert.equal(ui.getState().theme, 'dark');
});
test('independent channels coexist and dispose aborts every pending request', async () => {
  const { client } = setup({
    fetch: (_url, { signal }) =>
      new Promise((_resolve, reject) =>
        signal.addEventListener('abort', () => reject(signal.reason), { once: true }),
      ),
  });
  const a = client.request('/a', { channel: 'a' }).catch((error) => error);
  const b = client.request('/b', { channel: 'b' }).catch((error) => error);
  client.dispose();
  assert.equal((await a).name, 'AbortError');
  assert.equal((await b).name, 'AbortError');
});
test('aborted callers, HTTP errors and invalid JSON leave stores unchanged', async () => {
  const controller = new AbortController();
  controller.abort();
  let fetches = 0;
  const { client, ui } = setup({
    fetch: async () => {
      fetches++;
      return new Response('failure', { status: 503 });
    },
  });
  const before = ui.getState();
  await assert.rejects(client.request('/api/ui', { signal: controller.signal }), {
    name: 'AbortError',
  });
  assert.equal(fetches, 0);
  await assert.rejects(
    client.request('/api/ui'),
    (error) => error.code === 'http' && error.status === 503,
  );
  assert.equal(ui.getState(), before);
  const invalid = setup({ fetch: async () => new Response('invalid') });
  await assert.rejects(invalid.client.request('/api/ui'), (error) => error.code === 'protocol');
});
test('NDJSON handles split UTF-8, blank lines and a final line without newline', async () => {
  const payload =
    '\n' +
    JSON.stringify(createUiCommands({ type: 'notification.show', message: 'Préférences reçues' })) +
    '\r\n' +
    JSON.stringify(createUiCommands({ type: 'theme.set', value: 'dark' }));
  const encoded = new TextEncoder().encode(payload);
  const { client, ui, notifications } = setup({
    fetch: async () =>
      new Response(
        new ReadableStream({
          start(controller) {
            for (const byte of encoded) controller.enqueue(Uint8Array.of(byte));
            controller.close();
          },
        }),
      ),
  });
  assert.equal(await client.stream('/events'), 2);
  assert.equal(ui.getState().theme, 'dark');
  assert.equal(notifications[0][0], 'Préférences reçues');
});
test('invalid stream frames cancel the reader without partial application of that frame', async () => {
  let cancelled = false;
  const response = new Response(
    new ReadableStream({
      start(controller) {
        controller.enqueue(
          new TextEncoder().encode(
            '{"version":1,"commands":[{"type":"theme.set","value":"dark"},{"type":"invalid"}]}\n',
          ),
        );
      },
      cancel() {
        cancelled = true;
      },
    }),
  );
  const { client, ui } = setup({ fetch: async () => response });
  await assert.rejects(client.stream('/events'), (error) => error.code === 'protocol');
  assert.equal(cancelled, true);
  assert.equal(response.body.locked, false);
  assert.equal(ui.getState().theme, 'light');
});
test('draft commands keep their target conversation when the user changes conversation', () => {
  const { client, chat } = setup();
  const payload = createUiCommands({
    type: 'draft.set',
    conversationId: 'conversation',
    value: 'Original draft',
  });
  chat.newConversation();
  const active = chat.activeId;
  client.receive(payload);
  assert.equal(chat.activeId, active);
  assert.equal(chat.active.draft, undefined);
  assert.equal(
    chat.conversations.find((item) => item.id === 'conversation').draft,
    'Original draft',
  );
});
test('the Next.js example validates requests and emits commands that update the client', async () => {
  const invalid = await POST(
    new Request('http://localhost/api/ui-demo', { method: 'POST', body: '{}' }),
  );
  assert.equal(invalid.status, 400);
  const response = await POST(
    new Request('http://localhost/api/ui-demo', {
      method: 'POST',
      body: JSON.stringify({ theme: 'dark' }),
    }),
  );
  assert.equal(response.status, 200);
  const { client, ui, notifications } = setup();
  client.receive(await response.json());
  assert.equal(ui.getState().theme, 'dark');
  assert.equal(ui.getState().sidebar.width, 240);
  assert.ok(ui.getState().modal.title);
  assert.equal(notifications.length, 1);
});

test('cancelling an idle stream closes its body and rejects with AbortError', async () => {
  let cancelled = false;
  const ready = Promise.withResolvers();
  const response = new Response(
    new ReadableStream({
      pull() {
        ready.resolve();
      },
      cancel() {
        cancelled = true;
      },
    }),
  );
  const { client } = setup({ fetch: async () => response });
  const pending = client.stream('/events').catch((error) => error);
  await ready.promise;
  await Promise.resolve();
  client.cancel();
  assert.equal((await pending).name, 'AbortError');
  assert.equal(cancelled, true);
  assert.equal(response.body.locked, false);
});

test('the control library works with no React, RenoProvider or chat model', () => {
  const client = createUiClient();
  client.theme.set('dark');
  client.sidebar.set({ collapsed: true });
  client.modal.open('Independent UI', 'Text');
  client.notifications.show('Ready');
  assert.equal(client.getState().theme, 'dark');
  assert.equal(client.getState().modal.title, 'Independent UI');
  assert.equal(client.getState().notifications.length, 1);
  assert.throws(
    () => client.chat.setDraft('Requires an optional feature bridge'),
    (error) => error.code === 'target',
  );
  client.dispose();
});
