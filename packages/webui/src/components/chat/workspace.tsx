'use client';
import { memo, useLayoutEffect, useRef, useState, type RefObject } from 'react';
import { useChatSelector, useRenoActions } from './provider';
import { useUiSelector } from '../providers/store-provider';
import { activeConversation } from '../../store/chat/state';
import { shallowEqual } from '../../store/create-store';
import { Message } from './message';
import { Composer } from './composer';
import { Icon } from '../ui/icon';
import copy from '../../content/fr.json';
const ConnectedMessage = memo(function ConnectedMessage({ id }: { id: string }) {
  const { chat, notify, setSource, thumb, choose, openFeedback } = useRenoActions();
  const message = useChatSelector((state) =>
    activeConversation(state).messages.find((item) => item.id === id),
  );
  const retryable = useChatSelector(
    (state) =>
      activeConversation(state).messages.at(-1)?.id === id && message?.role === 'assistant',
  );
  const busy = useChatSelector((state) => retryable && state.busy);
  const feedback = useUiSelector((state) =>
    state.records.find(
      (item) =>
        item.kind === 'feedback' &&
        item.snapshot.messageId === id &&
        item.snapshot.response === message?.content,
    ),
  );
  if (!message) return null;
  return (
    <Message
      message={message}
      busy={busy}
      retryable={retryable}
      feedback={feedback?.kind === 'feedback' ? feedback : undefined}
      onSource={setSource}
      onRetry={() => void chat.retry(id)}
      onEscalate={() => void chat.escalate(id)}
      onThumb={(sentiment) => thumb(id, sentiment)}
      onComment={() => openFeedback(id)}
      onChoose={(version) => choose(id, version)}
      onCloseComparison={() => chat.closeComparison(id)}
      onCompare={() => void chat.compare(id)}
      onCopy={() => {
        if (!navigator.clipboard) notify(copy.application.laCopieNEstPasDisponibleDans, 'error');
        else
          void navigator.clipboard
            .writeText(message.content)
            .then(() => notify(copy.application.reponseCopiee))
            .catch(() => notify(copy.application.impossibleDeCopierLaReponse, 'error'));
      }}
    />
  );
});
const MessageList = memo(function MessageList() {
  const ids = useChatSelector(
    (state) => activeConversation(state).messages.map((message) => message.id),
    shallowEqual,
  );
  const busy = useChatSelector((state) => state.busy);
  return (
    <div id="messages" aria-busy={busy}>
      {ids.map((id) => (
        <ConnectedMessage key={id} id={id} />
      ))}
    </div>
  );
});
const ConversationHeading = memo(function ConversationHeading({ demo }: { demo: boolean }) {
  const title = useChatSelector((state) => activeConversation(state).title);
  const empty = useChatSelector((state) => !activeConversation(state).messages.length);
  return (
    <div className="conversation-intro">
      {demo && <span className="eyebrow">{copy.common.conversation}</span>}
      <h1 id="conversation-title">{title}</h1>
      {demo ? <p>{copy.chat.posezUneQuestionOuAjoutezUnDocument}</p> : empty && <p>{copy.chat.agentIntro}</p>}
    </div>
  );
});
const EmptyState = memo(function EmptyState() {
  const empty = useChatSelector((state) => !activeConversation(state).messages.length);
  const { chat } = useRenoActions();
  if (!empty) return null;
  return (
    <div id="empty-state" className="empty-state">
      <div className="assistant-emblem">{copy.chat.r}</div>
      <h2>{copy.common.newConversation}</h2>
      <p>{copy.chat.saisissezVotreQuestionOuChoisissezUnExemple}</p>
      <div className="suggestions">
        {[
          [copy.chat.quelsSontLesPrincipesDeNotreDesign, copy.chat.resumerLesPrincipesDesign],
          [copy.chat.montreMoiUnExempleDeTokensCss, copy.chat.afficherUnExempleDeCode],
        ].map(([prompt, label]) => (
          <button
            key={prompt}
            className="suggestion"
            data-prompt={prompt}
            onClick={() => void chat.send(prompt)}
          >
            <Icon name="book" />
            {label}
            <Icon name="chevron" />
          </button>
        ))}
      </div>
    </div>
  );
});
/** Only this observer and the changed message react to streamed text. */
const ScrollManager = memo(function ScrollManager({
  scroller,
  follow,
  onLatest,
}: {
  scroller: RefObject<HTMLDivElement | null>;
  follow: RefObject<boolean>;
  onLatest: (value: boolean) => void;
}) {
  const { chat } = useRenoActions();
  const [id, messages] = useChatSelector(
    (state) => [state.activeId, activeConversation(state).messages] as const,
    shallowEqual,
  );
  const activeId = useRef<string | undefined>(undefined);
  const lastMessage = useRef<string | undefined>(undefined);
  useLayoutEffect(() => {
    const node = scroller.current!;
    if (activeId.current !== id) {
      activeId.current = id;
      if (!messages.length) node.scrollTop = 0;
      else if (chat.active.scrollTop !== undefined) node.scrollTop = chat.active.scrollTop;
      else {
        const question = node.querySelectorAll('.message--user');
        const last = question.item(question.length - 1);
        node.scrollTop =
          messages.at(-1)?.comparison && last
            ? node.scrollTop +
              last.getBoundingClientRect().top -
              node.getBoundingClientRect().top -
              16
            : node.scrollHeight;
      }
    } else {
      const last = messages.at(-1);
      if (lastMessage.current !== last?.id && last?.generating) follow.current = true;
      if (follow.current) node.scrollTop = node.scrollHeight;
    }
    lastMessage.current = messages.at(-1)?.id;
    follow.current = node.scrollHeight - node.scrollTop - node.clientHeight < 100;
    onLatest(!follow.current && !!messages.length);
  }, [chat, id, messages, scroller, follow, onLatest]);
  return null;
});
export function ChatWorkspace({ demo = true }: { demo?: boolean }) {
  const { chat } = useRenoActions();
  const scroller = useRef<HTMLDivElement>(null);
  const follow = useRef(true);
  const [latest, setLatest] = useState(false);
  return (
    <div className="chat-page">
      <div
        id="conversation-scroll"
        className="conversation-scroll"
        ref={scroller}
        onScroll={(event) => {
          const node = event.currentTarget;
          follow.current = node.scrollHeight - node.scrollTop - node.clientHeight < 100;
          setLatest(!follow.current && !!chat.active.messages.length);
          chat.setScrollPosition(node.scrollTop);
        }}
      >
        <div className="conversation-container">
          <ConversationHeading demo={demo} />
          {demo && <div className="date-divider">
            <span>{copy.chat.aujourdHui}</span>
          </div>}
          <MessageList />
          {demo && <EmptyState />}
          <div id="generation-status" className="generation-status">
            <span className="status-dot" />
            {copy.chat.renoPrepareUneReponse}
          </div>
        </div>
      </div>
      <ScrollManager scroller={scroller} follow={follow} onLatest={setLatest} />
      <Composer
        demo={demo}
        chat={chat}
        latest={latest}
        onLatest={() =>
          scroller.current?.scrollTo({
            top: scroller.current.scrollHeight,
            behavior: matchMedia('(prefers-reduced-motion: reduce)').matches ? 'instant' : 'smooth',
          })
        }
      />
    </div>
  );
}
