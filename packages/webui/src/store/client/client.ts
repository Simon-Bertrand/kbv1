import { createUiStore, type UiState } from '../ui-state';
import { selectStore, type Equality } from '../create-store';
import { createUiTransport } from './http';
import {
  parseUiCommands,
  UiClientError,
  type NotificationKind,
  type UiCommand,
  type UiCommandBatch,
} from './protocol';
/** Optional feature bridge. The controller itself has no dependency on React or the chat model. */
export interface UiClientAdapter {
  prepare: (command: UiCommand) => (() => void) | undefined;
  batch?: (run: () => void) => void;
  chat?: {
    activeConversationId: () => string;
    send: (text?: string) => Promise<void>;
    stop: () => void;
    retry: (id: string) => Promise<void>;
    compare: (id: string) => Promise<void>;
    choose: (id: string, version: 'a' | 'b') => void;
    vote: (id: string, sentiment: 'positive' | 'negative') => void;
  };
}
export interface UiClientOptions {
  initialState?: UiState;
  fetch?: typeof fetch;
}
export type UiClientState = UiState;
/** Standalone controller: owns its state and transport, and works before/outside React. */
export function createUiClient(options: UiClientOptions = {}) {
  const store = createUiStore(options.initialState);
  const subscriptions = new Set<() => void>();
  let adapter: UiClientAdapter | undefined;
  const requireChat = () => {
    if (!adapter?.chat)
      throw new UiClientError('target', 'No chat adapter is attached to this UI client.');
    return adapter.chat;
  };
  function prepare(command: UiCommand): () => void {
    switch (command.type) {
      case 'theme.set':
        return () => store.dispatch({ type: 'theme/selected', theme: command.value });
      case 'sidebar.set':
        return () => {
          if (command.width !== undefined)
            store.dispatch({ type: 'sidebar/resized', width: command.width });
          if (
            command.collapsed !== undefined &&
            store.getState().sidebar.collapsed !== command.collapsed
          )
            store.dispatch({ type: 'sidebar/toggled' });
        };
      case 'modal.open':
        return () =>
          store.dispatch({
            type: 'modal/opened',
            modal: { title: command.title, body: command.body },
          });
      case 'modal.close':
        return () => store.dispatch({ type: 'modal/opened', modal: null });
      case 'source.close':
        return () => store.dispatch({ type: 'source/opened', source: null });
      case 'feedback.close':
        return () => store.dispatch({ type: 'feedback/opened', id: null });
      case 'annotations.set':
        return () => store.dispatch({ type: 'annotations/opened', open: command.open });
      case 'notification.show':
        return () =>
          store.dispatch({
            type: 'notification/added',
            notice: {
              id: crypto.randomUUID(),
              message: command.message,
              kind: command.kind ?? 'info',
              duration: command.kind === 'error' ? 0 : 6000,
            },
          });
      case 'announcement.set':
        return () => store.dispatch({ type: 'announcement/changed', text: command.message });
      default: {
        const apply = adapter?.prepare(command);
        if (!apply)
          throw new UiClientError(
            'target',
            'This UI command requires an attached feature adapter.',
          );
        return apply;
      }
    }
  }
  function receive(payload: unknown): UiCommandBatch {
    const batch = parseUiCommands(payload);
    const commands = batch.commands.map(prepare);
    const apply = () => commands.forEach((command) => command());
    store.batch(() => (adapter?.batch ? adapter.batch(apply) : apply()));
    return batch;
  }
  const dispatch = (command: UiCommand) => {
    receive({ version: 1, commands: [command] });
  };
  const transport = createUiTransport(receive, options);
  return {
    store,
    getState: store.getState,
    dispatch,
    receive,
    attach(feature: UiClientAdapter) {
      if (adapter && adapter !== feature)
        throw new UiClientError(
          'target',
          'A feature adapter is already attached to this UI client.',
        );
      adapter = feature;
      return () => {
        if (adapter === feature) adapter = undefined;
      };
    },
    subscribe<Value>(
      selector: (state: UiClientState) => Value,
      listener: (value: Value) => void,
      equal?: Equality<Value>,
    ) {
      const selected = selectStore(store, selector, equal);
      const off = selected.subscribe(() => listener(selected.getState()));
      const unsubscribe = () => {
        off();
        subscriptions.delete(unsubscribe);
      };
      subscriptions.add(unsubscribe);
      return unsubscribe;
    },
    theme: { set: (value: UiState['theme']) => dispatch({ type: 'theme.set', value }) },
    sidebar: {
      set: (value: { width?: number; collapsed?: boolean }) =>
        dispatch({ type: 'sidebar.set', width: value.width, collapsed: value.collapsed }),
    },
    modal: {
      open: (title: string, body: string) => dispatch({ type: 'modal.open', title, body }),
      close: () => dispatch({ type: 'modal.close' }),
    },
    sources: {
      open: (messageId: string, sourceId: number) =>
        dispatch({ type: 'source.open', messageId, sourceId }),
      close: () => dispatch({ type: 'source.close' }),
    },
    feedback: {
      open: (messageId: string) => dispatch({ type: 'feedback.open', messageId }),
      close: () => dispatch({ type: 'feedback.close' }),
      vote: (messageId: string, sentiment: 'positive' | 'negative') =>
        requireChat().vote(messageId, sentiment),
    },
    annotations: { setOpen: (open: boolean) => dispatch({ type: 'annotations.set', open }) },
    notifications: {
      show: (message: string, kind: NotificationKind = 'info') =>
        dispatch({ type: 'notification.show', message, kind }),
      dismiss: (id: string) => store.dispatch({ type: 'notification/dismissed', id }),
    },
    announce: (message: string) => dispatch({ type: 'announcement.set', message }),
    chat: {
      setDraft: (value: string) =>
        dispatch({
          type: 'draft.set',
          conversationId: requireChat().activeConversationId(),
          value,
        }),
      send: (text?: string) => requireChat().send(text),
      stop: () => requireChat().stop(),
      retry: (messageId: string) => requireChat().retry(messageId),
      compare: (messageId: string) => requireChat().compare(messageId),
      choose: (messageId: string, version: 'a' | 'b') => requireChat().choose(messageId, version),
    },
    request: transport.request,
    stream: transport.stream,
    cancel: transport.cancel,
    dispose: () => {
      transport.cancel();
      [...subscriptions].forEach((unsubscribe) => unsubscribe());
      adapter = undefined;
    },
  };
}
export type UiClient = ReturnType<typeof createUiClient>;
