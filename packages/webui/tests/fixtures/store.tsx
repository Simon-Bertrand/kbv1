import { createRoot } from 'react-dom/client';
import { flushSync } from 'react-dom';
import { useLayoutEffect } from 'react';
import { RenoProvider, useChatSelector, useRenoActions } from '../../src/components/chat/provider';
import { useUiDispatch, useUiSelector } from '../../src/components/providers/store-provider';
import { ChatWorkspace } from '../../src/components/chat/workspace';
import { activeConversation } from '../../src/store/chat/state';
const counts = { sidebar: 0, theme: 0, user: 0, answer: 0, actions: 0 };
let actions: ReturnType<typeof useRenoActions>;
let dispatch: ReturnType<typeof useUiDispatch>;
function Sidebar() {
  const collapsed = useUiSelector((state) => state.sidebar.collapsed);
  counts.sidebar++;
  return <span>{String(collapsed)}</span>;
}
function Theme() {
  const theme = useUiSelector((state) => state.theme);
  counts.theme++;
  return <span>{theme}</span>;
}
function User() {
  const user = useChatSelector((state) => activeConversation(state).messages[0]);
  counts.user++;
  return <span>{user?.content}</span>;
}
function Answer() {
  const answer = useChatSelector((state) => activeConversation(state).messages[1]);
  counts.answer++;
  return <span>{answer?.content}</span>;
}
function Actions() {
  const value = useRenoActions();
  const uiDispatch = useUiDispatch();
  counts.actions++;
  useLayoutEffect(() => {
    actions = value;
    dispatch = uiDispatch;
  }, [value, uiDispatch]);
  return null;
}
const tick = () => new Promise((resolve) => setTimeout(resolve, 0));
async function verifyStoreRendering() {
  const host = document.createElement('div');
  document.body.append(host);
  const root = createRoot(host);
  flushSync(() =>
    root.render(
      <RenoProvider
        initialConversation={{
          id: 'render-test',
          title: 'Render test',
          messages: [
            { id: 'user', role: 'user', content: 'Question' },
            { id: 'answer', role: 'assistant', content: 'A' },
          ],
        }}
      >
        <Sidebar />
        <Theme />
        <User />
        <Answer />
        <Actions />
        <ChatWorkspace />
      </RenoProvider>,
    ),
  );
  await tick();
  const before = { ...counts };
  flushSync(() => dispatch({ type: 'sidebar/toggled' }));
  const sidebar = { ...counts };
  flushSync(() =>
    actions.chat.dispatch({
      type: 'message/patched',
      conversationId: 'render-test',
      id: 'answer',
      patch: { content: 'AB' },
    }),
  );
  const answer = { ...counts };
  flushSync(() => actions.chat.setDraft('Brouillon'));
  const draft = { ...counts };
  flushSync(() => dispatch({ type: 'theme/selected', theme: 'dark' }));
  const theme = { ...counts };
  flushSync(() => root.unmount());
  const disposed = { ...counts };
  dispatch({ type: 'sidebar/toggled' });
  actions.chat.setDraft('Après démontage');
  await tick();
  host.remove();
  return { before, sidebar, answer, draft, theme, disposed, afterDispose: { ...counts } };
}
Object.assign(window, { verifyStoreRendering });
