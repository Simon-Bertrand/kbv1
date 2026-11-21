'use client';
import {
  createContext,
  useContext,
  useEffect,
  useLayoutEffect,
  useState,
  type ReactNode,
} from 'react';
import type { ChatProvider, Conversation, Source } from '../../store/chat/types';
import type { Annotation, FeedbackValues, ResponseSnapshot } from '../../store/feedback/types';
import { AnnotationRepository } from '../../store/feedback/repository';
import { historyFor, isCompleteResponse } from '../../store/chat/message';
import { useChatInstance } from './use-chat';
import { ChatModel } from '../../store/chat/model';
import type { ChatState } from '../../store/chat/state';
import type { UiStore } from '../../store/ui-state';
import { useStoreSelector } from '../providers/use-selector';
import type { Equality } from '../../store/create-store';
import { useUiStore, useUiSelector } from '../providers/store-provider';
import { useNotifications } from '../ui/notifications';
import { SourceDialog } from './source-dialog';
import { FeedbackDialog, AnnotationDialog } from './feedback-dialog';
import copy from '../../content/fr.json';
import { createUiClient, type UiClient } from '../../store/client/client';
import { UiClientProvider, useUiClient } from '../providers/client-provider';
import { createChatUiAdapter } from './ui-client-adapter';

function createRenoActions(
  chat: ChatModel,
  ui: UiStore,
  notify: ReturnType<typeof useNotifications>['notify'],
  client: UiClient,
) {
  let repository: AnnotationRepository | null = null;
  const refresh = () =>
    ui.dispatch({
      type: 'annotations/replaced',
      records: [...repository!.all()],
      persistent: repository!.persistent,
    });
  const connectAnnotations = () => {
    repository ??= new AnnotationRepository(chat.storageScope ? `reno-plus-annotations-athena-${chat.storageScope}` : undefined);
    refresh();
  };
  const setSource = (source: Source | null) => ui.dispatch({ type: 'source/opened', source });
  const openFeedback = (id: string | null) => ui.dispatch({ type: 'feedback/opened', id });
  const setAnnotationsOpen = (open: boolean) => ui.dispatch({ type: 'annotations/opened', open });
  const snapshot = (id: string) => {
    const index = chat.active.messages.findIndex((message) => message.id === id);
    const message = chat.active.messages[index];
    if (!isCompleteResponse(message)) return null;
    const context = historyFor(chat.active.messages, index);
    const prompt = context.filter((turn) => turn.role === 'user').at(-1)?.content;
    if (!prompt) return null;
    const value: ResponseSnapshot = structuredClone({
      conversationId: chat.activeId,
      messageId: id,
      prompt,
      context,
      response: message.content,
      sources: message.sources ?? [],
      origin: chat.origin,
    });
    return { message, snapshot: value };
  };
  const base = (value: ResponseSnapshot, comment = '') => ({
    schemaVersion: 1 as const,
    id: crypto.randomUUID(),
    createdAt: new Date().toISOString(),
    updatedAt: new Date().toISOString(),
    snapshot: value,
    comment,
  });
  const save = (record: Annotation) => {
    try {
      repository ??= new AnnotationRepository(chat.storageScope ? `reno-plus-annotations-athena-${chat.storageScope}` : undefined);
      repository.save(record);
      refresh();
      notify(
        repository.persistent
          ? copy.feedbackMessages.avisEnregistre
          : copy.feedbackMessages.avisConservePourCetteSessionUniquementLe,
        repository.persistent ? 'success' : 'warning',
      );
      return true;
    } catch (error) {
      const message = error instanceof Error ? error.message : copy.common.saveFailed;
      ui.dispatch({ type: 'feedback/failed', error: message });
      notify(message, 'error');
      return false;
    }
  };
  const thumb = (id: string, sentiment: FeedbackValues['sentiment']) => {
    const data = snapshot(id);
    if (!data) return;
    const previous = ui
      .getState()
      .records.find(
        (record) =>
          record.kind === 'feedback' &&
          record.snapshot.messageId === id &&
          record.snapshot.response === data.message.content,
      );
    const values: FeedbackValues = {
      satisfaction: null,
      relevance: null,
      accuracy: null,
      grounding: null,
      sourceId: null,
      passage: '',
      correction: '',
      issues: [],
      ...(previous?.kind === 'feedback' ? previous.values : {}),
      sentiment,
    };
    save({ ...base(data.snapshot, previous?.comment), kind: 'feedback', values });
  };
  const choose = (id: string, choice: 'a' | 'b') => {
    const data = snapshot(id);
    const comparison = data?.message.comparison;
    if (
      !data ||
      !comparison ||
      comparison.state !== 'ready' ||
      !comparison.responseB.trim() ||
      comparison.responseA.trim() === comparison.responseB.trim()
    )
      return;
    const record: Annotation = {
      ...base({
        ...data.snapshot,
        response: comparison.responseA,
        sources: structuredClone(comparison.sourcesA),
      }),
      kind: 'evaluation',
      values: {
        mode: 'dpo',
        comparisonId: comparison.id,
        preference: choice,
        alternative: { id: `${comparison.id}-b`, text: comparison.responseB, origin: chat.origin },
      },
    };
    if (save(record)) chat.chooseComparison(id, choice);
  };
  const comment = (id: string, form: FormData) => {
    const data = snapshot(id);
    if (!data) return;
    const values: FeedbackValues = {
      sentiment: form.get('sentiment') === 'positive' ? 'positive' : 'negative',
      satisfaction: null,
      relevance: null,
      accuracy: null,
      grounding: null,
      issues: form.getAll('issues').map(String),
      sourceId: null,
      passage: '',
      correction: '',
    };
    if (
      save({
        ...base(data.snapshot, String(form.get('comment') ?? '').trim()),
        kind: 'feedback',
        values,
      })
    )
      openFeedback(null);
  };
  const actions = {
    chat,
    ui,
    notify,
    thumb,
    choose,
    comment,
    setSource,
    openFeedback,
    setAnnotationsOpen,
    save,
    connectAnnotations,
  };
  return { ...actions, client };
}
type RenoActions = ReturnType<typeof createRenoActions>;
const Context = createContext<RenoActions | null>(null);
/** Stable services/actions: subscribing to this context never subscribes to chat state. */
export function useRenoActions() {
  const value = useContext(Context);
  if (!value) throw new Error('RenoProvider is required.');
  return value;
}
export function useChatSelector<Value>(
  selector: (state: ChatState) => Value,
  equal?: Equality<Value>,
) {
  return useStoreSelector(useRenoActions().chat.store, selector, equal);
}
export function useChatDispatch() {
  return useRenoActions().chat.dispatch;
}
/** Compatibility facade. New components should select only the fields they render. */
export function useReno() {
  const actions = useRenoActions();
  useChatSelector((state) => state);
  const state = useUiSelector((state) => state);
  return { ...actions, ...state };
}
function SourceOverlay() {
  const { setSource } = useRenoActions();
  const source = useUiSelector((state) => state.source);
  return <SourceDialog source={source} onClose={() => setSource(null)} />;
}
function StateProvider({
  initialConversation,
  provider,
  children,
}: {
  initialConversation: Conversation;
  provider?: ChatProvider;
  children: ReactNode;
}) {
  const ui = useUiStore();
  const client = useUiClient();
  const { notify } = useNotifications();
  const chat = useChatInstance(
    initialConversation,
    (text) => ui.dispatch({ type: 'announcement/changed', text }),
    provider,
  );
  const [actions] = useState(() => createRenoActions(chat, ui, notify, client));
  useEffect(() => actions.connectAnnotations(), [actions]);
  useLayoutEffect(() => client.attach(createChatUiAdapter(actions)), [actions, client]);
  return (
    <Context.Provider value={actions}>
      {children}
      <SourceOverlay />
      <FeedbackDialog />
      <AnnotationDialog />
    </Context.Provider>
  );
}
export function RenoProvider({
  client: suppliedClient,
  restorePreferences = !suppliedClient,
  ...props
}: {
  initialConversation: Conversation;
  provider?: ChatProvider;
  client?: UiClient;
  restorePreferences?: boolean;
  children: ReactNode;
}) {
  const [ownedClient] = useState(() => suppliedClient ?? createUiClient());
  const client = suppliedClient ?? ownedClient;
  return (
    <UiClientProvider
      client={client}
      disposeOnUnmount={!suppliedClient}
      restorePreferences={restorePreferences}
      dialogId="client-dialog"
    >
      <StateProvider {...props} />
    </UiClientProvider>
  );
}
