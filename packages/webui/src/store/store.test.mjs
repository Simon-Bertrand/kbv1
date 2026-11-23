import { test, after } from 'node:test';
import assert from 'node:assert/strict';
import { build } from 'rolldown';
import { mkdtemp, writeFile, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { pathToFileURL } from 'node:url';
const bundle = await build({
  input: new URL('./index.ts', import.meta.url).pathname,
  write: false,
  output: { format: 'es' },
});
const directory = await mkdtemp(join(tmpdir(), 'reno-store-test-'));
const entry = join(directory, 'store.mjs');
await writeFile(entry, bundle.output.find((file) => file.type === 'chunk' && file.isEntry).code);
const {
  createStore,
  selectStore,
  shallowEqual,
  createUiStore,
  uiReducer,
  createUiState,
  chatReducer,
  activeConversation,
} = await import(pathToFileURL(entry).href);
after(() => rm(directory, { recursive: true, force: true }));
function freeze(value) {
  if (value && typeof value === 'object') {
    Object.values(value).forEach(freeze);
    Object.freeze(value);
  }
  return value;
}
function chatStore() {
  return createStore(
    freeze({
      conversations: [
        {
          id: 'chat',
          title: 'Chat',
          messages: [
            { id: 'user', role: 'user', content: 'Question' },
            { id: 'assistant', role: 'assistant', content: 'A' },
          ],
        },
      ],
      activeId: 'chat',
      attachments: [],
      busy: false,
      persistent: true,
    }),
    chatReducer,
  );
}
function count(store, selector, equal) {
  let notifications = 0;
  const selected = selectStore(store, selector, equal);
  const unsubscribe = selected.subscribe(() => notifications++);
  return {
    selected,
    get count() {
      return notifications;
    },
    unsubscribe,
  };
}
test('dispatch only notifies subscribers when the reducer returns a new state', () => {
  const store = createUiStore();
  const before = store.getState();
  let calls = 0;
  const off = store.subscribe(() => calls++);
  store.dispatch({ type: 'sidebar/resized', width: before.sidebar.width });
  assert.equal(store.getState(), before);
  assert.equal(calls, 0);
  store.dispatch({ type: 'sidebar/toggled' });
  assert.equal(calls, 1);
  off();
  store.dispatch({ type: 'sidebar/toggled' });
  assert.equal(calls, 1);
});
test('UI reducers preserve frozen snapshots and unrelated branches', () => {
  const initial = freeze(createUiState());
  const changed = uiReducer(initial, { type: 'sidebar/toggled' });
  assert.equal(initial.sidebar.collapsed, false);
  assert.equal(changed.sidebar.collapsed, true);
  assert.equal(changed.records, initial.records);
  assert.equal(changed.source, initial.source);
});
test('sidebar, theme and modal subscriptions remain isolated', () => {
  const store = createUiStore();
  const sidebar = count(store, (state) => state.sidebar);
  const theme = count(store, (state) => state.theme);
  const source = count(store, (state) => state.source);
  store.dispatch({ type: 'sidebar/toggled' });
  assert.equal(sidebar.count, 1);
  assert.equal(theme.count, 0);
  assert.equal(source.count, 0);
  store.dispatch({ type: 'theme/selected', theme: 'dark' });
  assert.equal(sidebar.count, 1);
  assert.equal(theme.count, 1);
  store.dispatch({ type: 'source/opened', source: { id: 1 } });
  assert.equal(source.count, 1);
  assert.equal(theme.count, 1);
});
test('draft and scroll updates never notify message, title or busy selections', () => {
  const store = chatStore();
  const messages = count(store, (state) => activeConversation(state).messages);
  const title = count(store, (state) => activeConversation(state).title);
  const busy = count(store, (state) => state.busy);
  const draft = count(store, (state) => activeConversation(state).draft);
  store.dispatch({ type: 'conversation/patched', id: 'chat', patch: { draft: 'Brouillon' } });
  store.dispatch({ type: 'conversation/patched', id: 'chat', patch: { scrollTop: 200 } });
  assert.equal(draft.count, 1);
  assert.equal(messages.count, 0);
  assert.equal(title.count, 0);
  assert.equal(busy.count, 0);
});
test('streaming only replaces the affected message and its containers', () => {
  const store = chatStore();
  const before = store.getState();
  const user = count(store, (state) => activeConversation(state).messages[0]);
  const reply = count(store, (state) => activeConversation(state).messages[1]);
  const ids = count(
    store,
    (state) => activeConversation(state).messages.map((item) => item.id),
    shallowEqual,
  );
  for (const content of ['AB', 'ABC', 'ABCD'])
    store.dispatch({
      type: 'message/patched',
      conversationId: 'chat',
      id: 'assistant',
      patch: { content },
    });
  assert.equal(reply.count, 3);
  assert.equal(user.count, 0);
  assert.equal(ids.count, 0);
  assert.equal(before.conversations[0].messages[1].content, 'A');
  assert.equal(store.getState().conversations[0].messages[0], before.conversations[0].messages[0]);
});
test('cached array selections retain identity until their contents change', () => {
  const store = chatStore();
  const selected = selectStore(
    store,
    (state) => activeConversation(state).messages.map((item) => item.id),
    shallowEqual,
  );
  const before = selected.getState();
  store.dispatch({ type: 'generation/busy', value: true });
  assert.equal(selected.getState(), before);
  store.dispatch({
    type: 'message/added',
    conversationId: 'chat',
    message: { id: 'next', role: 'user', content: 'Suite' },
  });
  assert.notEqual(selected.getState(), before);
  assert.deepEqual(selected.getInitialState(), ['user', 'assistant']);
});
test('each store owns its state and keeps its server snapshot stable', () => {
  const first = createUiStore();
  const second = createUiStore();
  const initial = first.getInitialState();
  first.dispatch({ type: 'sidebar/toggled' });
  assert.equal(second.getState().sidebar.collapsed, false);
  assert.equal(first.getInitialState(), initial);
  assert.equal(initial.sidebar.collapsed, false);
});
test('system theme changes respect explicit preference and width inputs are bounded', () => {
  const store = createUiStore();
  store.dispatch({ type: 'theme/selected', theme: 'light' });
  const before = store.getState();
  store.dispatch({ type: 'theme/system', theme: 'dark' });
  assert.equal(store.getState(), before);
  store.dispatch({ type: 'sidebar/resized', width: NaN });
  assert.equal(store.getState(), before);
  store.dispatch({ type: 'sidebar/resized', width: 999 });
  assert.equal(store.getState().sidebar.width, 360);
});
test('closed or replaced comparisons reject late transport patches', () => {
  const store = chatStore();
  const before = store.getState();
  store.dispatch({
    type: 'message/patched',
    conversationId: 'chat',
    id: 'assistant',
    comparisonId: 'closed',
    patch: { content: 'Late result' },
  });
  assert.equal(store.getState(), before);
});

test('nested synchronous batches publish one final snapshot and no-op batches stay silent', () => {
  const store = createUiStore(); let calls = 0;
  store.subscribe(() => calls++);
  store.batch(() => { store.dispatch({type:'theme/selected',theme:'dark'}); store.batch(() => store.dispatch({type:'sidebar/toggled'})); });
  assert.equal(calls,1); assert.equal(store.getState().theme,'dark'); assert.equal(store.getState().sidebar.collapsed,true);
  store.batch(() => {}); assert.equal(calls,1);
});
test('a throwing batch releases notifications and remains usable', () => {
  const store=createUiStore(); let calls=0; store.subscribe(()=>calls++);
  assert.throws(()=>store.batch(()=>{store.dispatch({type:'sidebar/toggled'});throw Error('Interrupted')}));
  assert.equal(calls,1); store.dispatch({type:'sidebar/toggled'}); assert.equal(calls,2);
});
