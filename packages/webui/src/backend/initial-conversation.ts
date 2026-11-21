import localeCopy from '../content/fr.json';
const {common}=localeCopy;
import type { Conversation } from '../store/chat/types';

export function createInitialConversation(): Conversation {
  return { id: crypto.randomUUID(), title: common.newConversation, messages: [] };
}
