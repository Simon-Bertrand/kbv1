import type { Conversation, Message } from './types';
export interface ChatState {
  readonly conversations: readonly Conversation[];
  readonly activeId: string;
  readonly attachments: readonly File[];
  readonly busy: boolean;
  readonly persistent: boolean;
}
export type ChatAction =
  | { type: 'session/restored'; conversations: Conversation[]; activeId: string }
  | { type: 'conversation/created'; conversation: Conversation }
  | { type: 'conversation/selected'; id: string }
  | { type: 'conversation/removed'; id: string; fallback: Conversation }
  | {
      type: 'conversation/patched';
      id: string;
      patch: Partial<Pick<Conversation, 'draft' | 'scrollTop' | 'title' | 'messages' | 'revision' | 'remoteStub'>>;
    }
  | { type: 'message/added'; conversationId: string; message: Message }
  | {
      type: 'message/patched';
      conversationId: string;
      id: string;
      patch: Partial<Message>;
      comparisonId?: string;
    }
  | { type: 'attachments/changed'; files: readonly File[] }
  | { type: 'generation/busy'; value: boolean }
  | { type: 'storage/status'; persistent: boolean };
export const activeConversation = (state: ChatState): Conversation =>
  state.conversations.find((item) => item.id === state.activeId)!;
function updateConversation(
  state: ChatState,
  id: string,
  update: (conversation: Conversation) => Conversation,
): ChatState {
  const index = state.conversations.findIndex((item) => item.id === id);
  if (index < 0) return state;
  const previous = state.conversations[index]!;
  const next = update(previous);
  if (next === previous) return state;
  const conversations = [...state.conversations];
  conversations[index] = next;
  return { ...state, conversations };
}
function patch<Value extends object>(previous: Value, changes: Partial<Value>): Value {
  return Object.keys(changes).every((key) =>
    Object.is(Reflect.get(previous, key), Reflect.get(changes, key)),
  )
    ? previous
    : { ...previous, ...changes };
}
export function chatReducer(state: ChatState, action: ChatAction): ChatState {
  switch (action.type) {
    case 'session/restored':
      return { ...state, conversations: action.conversations, activeId: action.activeId };
    case 'conversation/created':
      return {
        ...state,
        conversations: [action.conversation, ...state.conversations],
        activeId: action.conversation.id,
        attachments: [],
      };
    case 'conversation/removed': {
      const remaining = state.conversations.filter((item) => item.id !== action.id);
      if (state.activeId !== action.id) return { ...state, conversations: remaining };
      const conversations = remaining.length ? remaining : [action.fallback];
      return { ...state, conversations, activeId: conversations[0]!.id, attachments: [] };
    }
    case 'conversation/selected':
      return state.activeId === action.id ||
        !state.conversations.some((item) => item.id === action.id)
        ? state
        : { ...state, activeId: action.id, attachments: [] };
    case 'conversation/patched':
      return updateConversation(state, action.id, (conversation) =>
        patch<Conversation>(conversation, action.patch),
      );
    case 'message/added':
      return updateConversation(state, action.conversationId, (conversation) => ({
        ...conversation,
        messages: [...conversation.messages, action.message],
      }));
    case 'message/patched':
      return updateConversation(state, action.conversationId, (conversation) => {
        const index = conversation.messages.findIndex((item) => item.id === action.id);
        const previous = conversation.messages[index];
        if (!previous || (action.comparisonId && previous.comparison?.id !== action.comparisonId))
          return conversation;
        const next = patch(previous, action.patch);
        if (next === previous) return conversation;
        const messages = [...conversation.messages];
        messages[index] = next;
        return { ...conversation, messages };
      });
    case 'attachments/changed':
      return state.attachments === action.files ? state : { ...state, attachments: action.files };
    case 'generation/busy':
      return state.busy === action.value ? state : { ...state, busy: action.value };
    case 'storage/status':
      return state.persistent === action.persistent
        ? state
        : { ...state, persistent: action.persistent };
  }
}
