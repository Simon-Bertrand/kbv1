import type { Conversation, Message, Source } from './types';
export const CHAT_STORAGE_KEY = 'reno-plus-conversations-v1';
export interface ChatSession {
  version: 1;
  activeId: string;
  conversations: Conversation[];
  dirtyIds?: string[];
}

function object(value: unknown): value is Record<string, unknown> {
  return !!value && typeof value === 'object';
}
function identifier(value: unknown): value is string {
  return typeof value === 'string' && /^[a-zA-Z0-9_-]{1,128}$/.test(value);
}
function source(value: unknown): value is Source {
  return (
    object(value) &&
    Number.isInteger(value.id) &&
    Number(value.id) >= 0 &&
    ['title', 'category', 'excerpt', 'pages'].every((key) => typeof value[key] === 'string') &&
    (value.pdf === undefined ||
      (object(value.pdf) &&
        typeof value.pdf.url === 'string' && value.pdf.url.length <= 4096 &&
        (value.pdf.accessUrl === undefined || value.pdf.accessUrl === value.pdf.url) &&
        Number.isInteger(value.pdf.page) &&
        Number(value.pdf.page) >= 1))
  );
}
function sources(value: unknown): value is Source[] {
  return Array.isArray(value) && value.every(source);
}
function message(value: unknown): value is Message {
  if (
    !object(value) ||
    !identifier(value.id) ||
    (value.role !== 'user' && value.role !== 'assistant') ||
    (typeof value.content !== 'string' || value.content.length > 1000000)
  )
    return false;
  if (value.errorMessage !== undefined && typeof value.errorMessage !== 'string') return false;
  if (value.sources !== undefined && !sources(value.sources)) return false;
  if (
    value.context !== undefined &&
    (!Array.isArray(value.context) ||
      !value.context.every(
        (turn) =>
          object(turn) &&
          (turn.role === 'user' || turn.role === 'assistant') &&
          typeof turn.content === 'string',
      ))
  )
    return false;
  if (
    ['generating', 'interrupted', 'error', 'escalating'].some(
      (key) => value[key] !== undefined && typeof value[key] !== 'boolean',
    )
  )
    return false;
  if (value.mode !== undefined && value.mode !== 'fast' && value.mode !== 'deep') return false;
  if (value.fastId !== undefined && typeof value.fastId !== 'string') return false;
  if (value.fastAnswer !== undefined && typeof value.fastAnswer !== 'string') return false;
  if (value.fastSources !== undefined && !sources(value.fastSources)) return false;
  if (value.comparison !== undefined) {
    const c = value.comparison;
    if (
      !object(c) ||
      !identifier(c.id) ||
      typeof c.responseA !== 'string' ||
      typeof c.responseB !== 'string' ||
      !sources(c.sourcesA) ||
      !sources(c.sourcesB) ||
      (c.state !== 'generating' && c.state !== 'ready' && c.state !== 'error') ||
      (c.choice !== undefined && c.choice !== 'a' && c.choice !== 'b')
    )
      return false;
  }
  return true;
}
export function isConversation(value: unknown): value is Conversation {
  return (
    object(value) &&
    identifier(value.id) &&
    (value.revision === undefined || (Number.isSafeInteger(value.revision) && Number(value.revision) >= 0)) &&
    typeof value.title === 'string' && value.title.length <= 255 &&
    Array.isArray(value.messages) &&
    value.messages.length <= 1000 && value.messages.every(message) && new Set(value.messages.map(m => m.id)).size === value.messages.length &&
    (value.draft === undefined || typeof value.draft === 'string') &&
    (value.scrollTop === undefined ||
      (typeof value.scrollTop === 'number' &&
        Number.isFinite(value.scrollTop) &&
        value.scrollTop >= 0))
  );
}
export function stripVolatileActivity(conversation: Conversation): Conversation {
  return {
    ...conversation,
    messages: conversation.messages.map((message) => {
      const { activity: _act, escalating: _esc, ...msg } = message;
      if (msg.comparison) {
        const { activity: _compAct, ...comparison } = msg.comparison;
        return { ...msg, comparison };
      }
      return msg;
    }),
  };
}

export class ChatRepository {
  constructor(private key = CHAT_STORAGE_KEY) {}
  load(): ChatSession | null {
    try {
      const data: unknown = JSON.parse(localStorage.getItem(this.key) ?? 'null');
      if (
        !object(data) ||
        data.version !== 1 ||
        typeof data.activeId !== 'string' ||
        !Array.isArray(data.conversations) ||
        !data.conversations.length ||
        !data.conversations.every(isConversation) ||
        !data.conversations.some((item) => item.id === data.activeId)
      )
        return null;
      if (typeof data.savedAt==='number' && Date.now()-data.savedAt>30*86400*1000) {
        localStorage.removeItem?.(this.key);
        return null;
      }
      const conversations = (data.conversations as Conversation[]).map(stripVolatileActivity);
      for (const item of conversations)
        for (const reply of item.messages) {
          if (reply.generating) {
            reply.generating = false;
            reply.interrupted = true;
            if (reply.fastAnswer) {
              reply.content = reply.fastAnswer;
              if (reply.fastSources) reply.sources = reply.fastSources;
              reply.mode = 'fast';
            }
          } else if (reply.interrupted && reply.fastAnswer && (!reply.content || (reply.content !== reply.fastAnswer && reply.mode !== 'deep'))) {
            reply.content = reply.fastAnswer;
            if (reply.fastSources) reply.sources = reply.fastSources;
            reply.mode = 'fast';
          }
          if (reply.comparison?.state === 'generating') reply.comparison.state = 'error';
        }
      const dirtyIds = Array.isArray(data.dirtyIds) ? data.dirtyIds.filter((id): id is string => identifier(id) && conversations.some(c => c.id === id)) : [];
      return { version: 1, activeId: data.activeId, conversations, ...(dirtyIds.length ? { dirtyIds } : {}) };
    } catch {
      return null;
    }
  }
  recovered():Conversation[] {
    try {
      const value=JSON.parse(localStorage.getItem(this.key+'-deleted-conflicts')??'null');
      if(!value || typeof value.savedAt!=='number')return [];
      if(Date.now()-value.savedAt>30*86400000){localStorage.removeItem?.(this.key+'-deleted-conflicts');return [];}
      return Array.isArray(value.conversations)?value.conversations.filter(isConversation).map(stripVolatileActivity):[];
    }catch{return [];}
  }
  quarantine(conversations:Conversation[]):void {
    const all=new Map(this.recovered().map(c=>[c.id,c]));
    for(const conversation of conversations)all.set(conversation.id,stripVolatileActivity(conversation));
    localStorage.setItem(this.key+'-deleted-conflicts',JSON.stringify({savedAt:Date.now(),conversations:[...all.values()]}));
  }
  clearRecovered(id:string):void {
    localStorage.setItem(this.key+'-deleted-conflicts',JSON.stringify({savedAt:Date.now(),conversations:this.recovered().filter(c=>c.id!==id)}));
  }
  save(activeId: string, conversations: readonly Conversation[], dirtyIds: readonly string[] = []): boolean {
    try {
      const cleanConversations = conversations.map(stripVolatileActivity);
      localStorage.setItem(
        this.key,
        JSON.stringify({ version: 1, savedAt:Date.now(), activeId, conversations: cleanConversations, ...(dirtyIds.length ? { dirtyIds } : {}) }),
      );
      return true;
    } catch {
      return false;
    }
  }
}
