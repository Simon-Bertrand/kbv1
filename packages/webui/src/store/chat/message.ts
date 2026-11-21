import type { ChatTurn, Message } from './types';

export function isCompleteResponse(message: Message | undefined): message is Message {
  return (
    !!message &&
    message.role === 'assistant' &&
    !!message.content.trim() &&
    !message.generating &&
    !message.error &&
    !message.interrupted
  );
}
/** Preserve the context used at generation time, even if an earlier choice changes. */
export function historyFor(messages: readonly Message[], index = messages.length): ChatTurn[] {
  return (
    messages[index]?.context ??
    messages.slice(0, index).map(({ role, content }) => ({ role, content }))
  );
}
