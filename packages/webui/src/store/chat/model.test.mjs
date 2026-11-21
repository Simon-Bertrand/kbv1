import { test, after } from 'node:test';
import { mkdtemp, writeFile, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import assert from 'node:assert/strict';
import { build } from 'rolldown';
import { pathToFileURL } from 'node:url';

// Exercise the chat model without a DOM, framework renderer or localhost.
const bundle = await build({
  input: new URL('./model.ts', import.meta.url).pathname,
  write: false,
  output: { format: 'es' },
});
const output = bundle.output.find((file) => file.type === 'chunk' && file.isEntry);
const directory = await mkdtemp(join(tmpdir(), 'reno-chat-test-'));
const entry = join(directory, 'model.mjs');
await writeFile(entry, output.code);
const { ChatModel, ChatRepository } = await import(pathToFileURL(entry).href);
const source = { id: 1, title: 'Source', category: 'Test', excerpt: 'Extrait', pages: '1' };

function setup(t, stream) {
  const storage = new Map();
  globalThis.localStorage = {
    getItem: (key) => storage.get(key) ?? null,
    setItem: (key, value) => storage.set(key, value),
  };
  const chat = new ChatModel(
    { id: 'test', title: 'Test', messages: [] },
    { sources: [source], stream },
    () => {},
  );
  t.after(() => chat.disconnect());
  return chat;
}
const aborted = (signal) =>
  new Promise((_, reject) => {
    if (signal.aborted) reject(signal.reason);
    else signal.addEventListener('abort', () => reject(signal.reason), { once: true });
  });

test('simple and alternative generations preserve their context and chosen version', async (t) => {
  const calls = [];
  const chat = setup(t, async function* (request) {
    calls.push({ messages: structuredClone(request.messages), variant: request.variant });
    yield request.variant === 'alternative' ? 'Version B' : 'Version A';
  });
  await chat.send('Question');
  const reply = chat.active.messages.at(-1);
  assert.equal(reply.content, 'Version A');
  assert.equal(reply.generating, false);
  assert.deepEqual(reply.sources, [source]);
  await chat.compare(reply.id);
  assert.equal(reply.comparison, undefined, 'the previous snapshot remains immutable');
  assert.equal(chat.active.messages.at(-1).comparison.state, 'ready');
  assert.equal(chat.active.messages.at(-1).comparison.responseB, 'Version B');
  assert.deepEqual(calls[1].messages, calls[0].messages);
  chat.chooseComparison(reply.id, 'b');
  await chat.send('Suite');
  assert.equal(calls[2].messages[1].content, 'Version B');
  assert.equal(reply.context.length, 1);
});

test('a failed generation keeps its partial text and releases the next request', async (t) => {
  let calls = 0;
  const chat = setup(t, async function* () {
    yield 'Partiel';
    if (++calls === 1) throw new Error('Transport failed');
  });
  await chat.send('Question');
  const failed = chat.active.messages.at(-1);
  assert.equal(failed.content, 'Partiel');
  assert.equal(failed.error, true);
  assert.equal(failed.generating, false);
  await chat.send('Nouvelle demande');
  assert.equal(calls, 3);
  assert.equal(chat.active.messages.at(-1).error, undefined);
});

test('stopping a generation marks it interrupted and permits another request', async (t) => {
  const ready = Promise.withResolvers();
  let calls = 0;
  const chat = setup(t, async function* ({ signal }) {
    if (++calls === 1) {
      yield 'Début';
      ready.resolve();
      await aborted(signal);
    } else yield 'Terminé';
  });
  const pending = chat.send('Question');
  await ready.promise;
  chat.stop();
  await pending;
  assert.equal(chat.active.messages.at(-1).interrupted, true);
  assert.equal(chat.active.messages.at(-1).generating, false);
  await chat.send('Après interruption');
  assert.equal(chat.active.messages.at(-1).content, 'Terminé');
});

test('closing a pending comparison preserves A and releases the next request', async (t) => {
  const ready = Promise.withResolvers();
  const chat = setup(t, async function* ({ variant, signal }) {
    if (variant === 'alternative') {
      yield 'B partielle';
      ready.resolve();
      await aborted(signal);
    } else yield 'Réponse A';
  });
  await chat.send('Question');
  const reply = chat.active.messages.at(-1);
  const pending = chat.compare(reply.id);
  await ready.promise;
  chat.closeComparison(reply.id);
  await pending;
  assert.equal(chat.active.messages.at(-1).comparison, undefined);
  assert.equal(chat.active.messages.at(-1).content, 'Réponse A');
  assert.deepEqual(reply.sources, [source]);
  chat.newConversation();
  await chat.send('Suite');
  assert.equal(chat.active.messages.at(-1).content, 'Réponse A');
});

after(() => rm(directory, { recursive: true, force: true }));

test('only the second question automatically produces a comparison', async (t) => {
  const calls = [];
  const chat = setup(t, async function* ({ variant }) {
    calls.push(variant ?? 'primary');
    yield variant === 'alternative' ? 'Alternative' : 'Réponse';
  });
  await chat.send('Première');
  assert.equal(chat.active.messages.at(-1).comparison, undefined);
  await chat.send('Deuxième');
  assert.equal(chat.active.messages.at(-1).comparison.state, 'ready');
  await chat.send('Troisième');
  assert.equal(chat.active.messages.at(-1).comparison, undefined);
  assert.deepEqual(calls, ['primary', 'primary', 'alternative', 'primary']);
});

test('regenerating an older reply never removes subsequent turns', async (t) => {
  const chat = setup(t, async function* () {
    yield 'Réponse';
  });
  await chat.send('Première');
  const id = chat.active.messages.at(-1).id;
  await chat.send('Deuxième');
  const before = structuredClone(chat.active.messages);
  await chat.retry(id);
  assert.deepEqual(chat.active.messages, before);
});

test('observable state restores drafts and unsubscribes cleanly', async (t) => {
  const chat = setup(t, async function* () {
    yield 'Réponse';
  });
  chat.connect();
  let changes = 0;
  const unsubscribe = chat.subscribe(() => changes++);
  chat.setDraft('Brouillon');
  assert.equal(changes, 1);
  assert.notEqual(chat.getSnapshot(), chat.getServerSnapshot());
  assert.equal(chat.getSnapshot(), chat.getSnapshot(), 'snapshots are cached until dispatch');
  chat.flush();
  const restored = new ChatModel(
    { id: 'fallback', title: 'Fallback', messages: [] },
    { sources: [], async *stream() {} },
    () => {},
  );
  restored.connect();
  assert.equal(restored.activeId, 'test');
  assert.equal(restored.active.draft, 'Brouillon');
  restored.disconnect();
  unsubscribe();
  chat.setDraft('Suite');
  assert.equal(changes, 1);
});

test('default generation uses fast path and escalate performs deep search on same message', async (t) => {
  const calls = [];
  const chat = setup(t, async function* (request) {
    calls.push({
      mode: request.mode,
      fastId: request.fastId,
      fastAnswer: request.fastAnswer,
      reason: request.reason,
    });
    yield request.mode === 'deep' ? 'Réponse approfondie' : 'Réponse rapide';
  });

  await chat.send('Question initiale');
  const fastReply = chat.active.messages.at(-1);
  assert.equal(fastReply.content, 'Réponse rapide');
  assert.equal(fastReply.mode, 'fast');
  assert.equal(calls[0].mode, 'fast');

  const originalId = fastReply.id;
  await chat.escalate(originalId);

  const deepReply = chat.active.messages.at(-1);
  assert.equal(deepReply.id, originalId, 'escalation preserves the same message id');
  assert.equal(deepReply.content, 'Réponse approfondie');
  assert.equal(deepReply.mode, 'deep');
  assert.equal(calls[1].mode, 'deep');
  assert.equal(calls[1].fastAnswer, 'Réponse rapide');
  assert.ok(calls[1].reason);
});

test('escalation failure restores previous fast answer and ignores concurrent calls', async (t) => {
  let calls = 0;
  const chat = setup(t, async function* (request) {
    calls++;
    if (request.mode === 'deep') {
      yield 'Échec partiel';
      throw new Error('Athena error');
    }
    yield 'Réponse rapide OK';
  });

  await chat.send('Question');
  const message = chat.active.messages.at(-1);
  assert.equal(message.content, 'Réponse rapide OK');
  assert.equal(message.mode, 'fast');

  await chat.escalate(message.id);
  const failed = chat.active.messages.at(-1);
  assert.equal(failed.id, message.id);
  assert.equal(failed.error, true);
  assert.equal(failed.content, 'Réponse rapide OK', 'restores fast answer on error');
  assert.equal(failed.mode, 'fast');

  const streamRunning = Promise.withResolvers();
  let deepCalls = 0;
  const chatDouble = setup(t, async function* (request) {
    if (request.mode === 'deep') {
      deepCalls++;
      await streamRunning.promise;
      yield 'Deep answer';
    } else {
      yield 'Fast answer';
    }
  });
  await chatDouble.send('Test double click');
  const m = chatDouble.active.messages.at(-1);
  const p1 = chatDouble.escalate(m.id);
  const p2 = chatDouble.escalate(m.id);
  streamRunning.resolve();
  await Promise.all([p1, p2]);
  assert.equal(deepCalls, 1, 'second concurrent escalate call is ignored');
  assert.equal(chatDouble.active.messages.length, 2);
});

test('rehydration during escalation restores fastAnswer, fastSources, mode=fast and preserves retry/re-escalate', async (t) => {
  const repo = new ChatRepository('reno-rehydration-test');
  const storage = new Map();
  globalThis.localStorage = {
    getItem: (key) => storage.get(key) ?? null,
    setItem: (key, value) => storage.set(key, value),
  };

  const fastSourcesList = [{ id: 1, title: 'Doc Fast', category: 'Guide', excerpt: 'Extrait Fast', pages: '1-2' }];
  // Simulate state where escalation was interrupted by reload while generating deep response:
  const conversation = {
    id: 'conv-1',
    title: 'Test escalade',
    messages: [
      { id: 'm-user', role: 'user', content: 'Explique le sujet' },
      {
        id: 'm-asst',
        role: 'assistant',
        content: '', // content was emptied or partial on escalate
        generating: true,
        fastAnswer: 'Réponse rapide originale',
        fastSources: fastSourcesList,
        fastId: 'fast-123',
        mode: 'fast',
      },
    ],
  };

  storage.set('reno-rehydration-test', JSON.stringify({
    version: 1,
    savedAt: Date.now(),
    activeId: 'conv-1',
    conversations: [conversation],
  }));

  const session = repo.load();
  assert.ok(session, 'session should be loaded');
  const restoredMsg = session.conversations[0].messages[1];
  assert.equal(restoredMsg.generating, false, 'generating flag is cleared');
  assert.equal(restoredMsg.interrupted, true, 'marked interrupted');
  assert.equal(restoredMsg.content, 'Réponse rapide originale', 'fast answer text restored');
  assert.deepEqual(restoredMsg.sources, fastSourcesList, 'fast sources restored');
  assert.equal(restoredMsg.mode, 'fast', 'mode remains fast');

  // Verify that passing this to ChatModel allows re-escalating successfully:
  let escalatedToDeep = false;
  const chat = new ChatModel(
    session.conversations[0],
    {
      sources: [{ id: 2, title: 'Deep Doc', category: 'RAG', excerpt: 'Extrait Deep', pages: '5' }],
      async *stream(req) {
        if (req.mode === 'deep') {
          escalatedToDeep = true;
          assert.equal(req.fastAnswer, 'Réponse rapide originale');
          assert.equal(req.fastId, 'fast-123');
          yield 'Réponse approfondie après rechargement';
        }
      },
    },
    () => {},
  );
  t.after(() => chat.disconnect());

  // Can re-escalate after rehydration:
  await chat.escalate('m-asst');
  const finalMsg = chat.active.messages.at(-1);
  assert.equal(escalatedToDeep, true, 'deep escalation executed');
  assert.equal(finalMsg.content, 'Réponse approfondie après rechargement');
  assert.equal(finalMsg.mode, 'deep');
});

test('escalation abort restores fastAnswer and fastSources and preserves mode=fast', async (t) => {
  const streamRunning = Promise.withResolvers();
  const fastSourcesList = [{ id: 1, title: 'Doc Fast', category: 'Guide', excerpt: 'Extrait Fast', pages: '1' }];
  const chat = setup(t, async function* (request) {
    if (request.mode === 'deep') {
      yield 'Deep partial...';
      streamRunning.resolve();
      await aborted(request.signal);
    } else {
      yield 'Fast initial answer';
    }
  });

  await chat.send('Question');
  const fastMsg = chat.active.messages.at(-1);
  assert.equal(fastMsg.content, 'Fast initial answer');
  assert.equal(fastMsg.mode, 'fast');

  const pending = chat.escalate(fastMsg.id);
  await streamRunning.promise;
  chat.stop();
  await pending;

  const abortedMsg = chat.active.messages.at(-1);
  assert.equal(abortedMsg.interrupted, true);
  assert.equal(abortedMsg.content, 'Fast initial answer');
  assert.deepEqual(abortedMsg.sources, [source]);
  assert.equal(abortedMsg.mode, 'fast');
});

test('new chat reuses an empty conversation and deletion preserves conversations with drafts', async (t) => {
  const chat = setup(t, async function* () {});
  chat.setDraft('Un brouillon à conserver');
  chat.newConversation();
  const empty = chat.activeId;
  for (let i = 0; i < 30; i++) chat.newConversation();
  assert.equal(chat.activeId, empty);
  assert.equal(chat.conversations.length, 2);
  assert.equal(await chat.deleteEmptyConversation('test'), false);
  assert.equal(await chat.deleteEmptyConversation(empty), true);
  assert.equal(chat.activeId, 'test');
  assert.equal(chat.active.draft, 'Un brouillon à conserver');
  assert.equal(chat.conversations.length, 1);
});
