import type { UiClientAdapter } from '../../store/client/client';
import { UiClientError } from '../../store/client/protocol';
import type { ChatModel } from '../../store/chat/model';
import type { UiStore } from '../../store/ui-state';
import { isCompleteResponse } from '../../store/chat/message';
/** Optional KM feature bridge; the independent UI controller never imports this module. */
export function createChatUiAdapter({
  chat,
  ui,
  thumb,
  choose,
}: {
  chat: ChatModel;
  ui: UiStore;
  thumb: (id: string, sentiment: 'positive' | 'negative') => void;
  choose: (id: string, version: 'a' | 'b') => void;
}): UiClientAdapter {
  const target = (id: string) => {
    const message = chat.active.messages.find((item) => item.id === id);
    if (!isCompleteResponse(message))
      throw new UiClientError(
        'target',
        'The completed assistant response is unavailable in the active conversation.',
      );
    return message;
  };
  return {
    batch: chat.store.batch,
    prepare(command) {
      switch (command.type) {
        case 'draft.set':
          if (!chat.conversations.some((item) => item.id === command.conversationId))
            throw new UiClientError('target', 'The draft conversation is unavailable.');
          return () =>
            chat.dispatch({
              type: 'conversation/patched',
              id: command.conversationId,
              patch: { draft: command.value },
            });
        case 'source.open': {
          const message = target(command.messageId);
          const source = [
            ...(message.sources ?? []),
            ...(message.comparison?.sourcesA ?? []),
            ...(message.comparison?.sourcesB ?? []),
          ].find((item) => item.id === command.sourceId);
          if (!source)
            throw new UiClientError('target', 'The source is not cited by this response.');
          return () => ui.dispatch({ type: 'source/opened', source });
        }
        case 'feedback.open':
          target(command.messageId);
          return () => ui.dispatch({ type: 'feedback/opened', id: command.messageId });
        default:
          return undefined;
      }
    },
    chat: {
      activeConversationId: () => chat.activeId,
      send: (text) => chat.send(text),
      stop: () => chat.stop(),
      retry: (id) => chat.retry(id),
      compare: (id) => chat.compare(id),
      choose: (id, version) => {
        target(id);
        choose(id, version);
      },
      vote: (id, sentiment) => {
        target(id);
        thumb(id, sentiment);
      },
    },
  };
}
