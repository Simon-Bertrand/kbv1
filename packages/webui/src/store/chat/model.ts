import localeCopy from '../../content/fr.json';
const {common:commonCopy, chatMessages:chatMessagesCopy, agentChat}=localeCopy;
import { historyFor, isCompleteResponse } from './message';
import type { ChatProvider, ChatRequest, Conversation, Message, Source } from './types';
import { ChatRepository } from './repository';
export { ChatRepository } from './repository';
import { ChatRequestError } from './http-provider';
import { createStore, type Store } from '../create-store';
import { activeConversation, chatReducer, type ChatAction, type ChatState } from './state';
const copyReady = (origin: string) => origin === 'generated' ? agentChat.ready : chatMessagesCopy.laReponseEstPreteTroisSourcesDe;
const emptyConversation = (): Conversation => ({ id: crypto.randomUUID(), title: commonCopy.newConversation, messages: [] });
export const canDeleteEmptyConversation = (conversation: Conversation): boolean =>
  !conversation.messages.length && !conversation.draft &&
  (!conversation.remoteStub || conversation.title === commonCopy.newConversation);

type StreamResult = { ok: true; sources: Source[]; fastId?: string; mode?: 'fast' | 'deep' } | { ok: false; error: unknown };
/** Side effects/transport live here; every state change goes through the pure reducer. */
export class ChatModel {
  readonly store: Store<ChatState, ChatAction>;
  private hydrationAbort?:AbortController;
  private restoreTimer?: ReturnType<typeof setTimeout>;
  private retryDelay = 1000;
  private remoteFailed = false;
  private restoreAbort?: AbortController;
  private remoteReady = false;
  private revision = 0;
  private saving?: Promise<void>;
  private deleting = new Set<string>();
  private pending?: Conversation[];
  private dirty = new Set<string>();
  private unsynced = new Set<string>();
  private abort?: AbortController;
  private repository: ChatRepository;
  private saveTimer?: ReturnType<typeof setTimeout>;
  private storageFailed = false;
  private restored = false;
  private connected = false;
  constructor(
    initial: Conversation,
    private provider: ChatProvider,
    private announce: (text: string) => void,
  ) {
    this.repository = new ChatRepository(provider.storageScope ? `reno-plus-conversations-athena-${provider.storageScope}` : undefined);
    this.store = createStore(
      {
        conversations: [structuredClone(initial)],
        activeId: initial.id,
        attachments: [],
        busy: false,
        persistent: true,
      } as ChatState,
      chatReducer,
    );
  }
  subscribe = (listener: () => void) => this.store.subscribe(listener);
  getSnapshot = () => this.store.getState();
  getServerSnapshot = () => this.store.getInitialState();
  dispatch = (action: ChatAction): void => {
    const previous = this.store.getState();
    this.store.dispatch(action);
    if (this.store.getState() !== previous) {
      this.revision++;
      for (const conversation of this.conversations)
        if (previous.conversations.find(c => c.id === conversation.id) !== conversation) {
          this.dirty.add(conversation.id);
          if (this.provider.persistence) this.unsynced.add(conversation.id);
        }
      this.scheduleSave();
    }
  };
  get origin() { return this.provider.origin ?? 'demo'; }
  get storageScope() { return this.provider.storageScope; }
  get conversations() {
    return this.store.getState().conversations;
  }
  get activeId() {
    return this.store.getState().activeId;
  }
  get active(): Conversation {
    return activeConversation(this.store.getState());
  }
  get busy() {
    return this.store.getState().busy;
  }
  get persistent() {
    return this.store.getState().persistent;
  }
  get attachments() {
    return this.store.getState().attachments;
  }
  connect(): void {
    this.connected = true;
    if (!this.restored) {
      this.restored = true;
      const saved = this.repository.load();
      if (saved) {
        this.store.dispatch({ type: 'session/restored', ...saved });
        this.unsynced = new Set(saved.dirtyIds ?? []);
        this.dirty = new Set(this.unsynced);
      }
      this.scheduleSave();
    }
    if (this.provider.persistence && !this.remoteReady && !this.restoreAbort) void this.restoreRemote();
  }
  private async restoreRemote(): Promise<void> {
    const controller = new AbortController();
    this.restoreAbort = controller;
    const revision = this.revision;
    try {
      const remote = await this.provider.persistence!.load(controller.signal);
      if (controller.signal.aborted || !this.connected) return;
      const deletedEdits=this.conversations.filter(c=>this.provider.persistence?.deletedIds?.includes(c.id) && (this.unsynced.has(c.id)||this.dirty.has(c.id)));
      if(deletedEdits.length){this.repository.quarantine(deletedEdits);this.announce(chatMessagesCopy.modificationsSupprimeesConservees);}
      const local = this.conversations.filter(item => !this.provider.persistence?.deletedIds?.includes(item.id)).filter(item => !remote.length || item.messages.length || item.draft || this.dirty.has(item.id));
      const forks:Conversation[]=[];
      let activeFork:string|undefined;
      const merged = remote.map(item => {
        const edited=local.find(c=>c.id===item.id);
        const unsaved=this.unsynced.has(item.id) || (revision!==this.revision && this.dirty.has(item.id));
        if(!edited || !unsaved)return item;
        if((edited.revision??0)!==(item.revision??0)) {
          const fork:Conversation={...edited,id:crypto.randomUUID(),revision:undefined,
            title:(edited.title.slice(0,230)+chatMessagesCopy.copieLocaleConflitTitre).slice(0,255),
            messages:edited.messages.map(m=>({...m,id:crypto.randomUUID()}))};
          forks.push(fork);this.dirty.delete(item.id);this.unsynced.delete(item.id);
          this.dirty.add(fork.id);this.unsynced.add(fork.id);
          if(this.activeId===item.id)activeFork=fork.id;
          this.announce(chatMessagesCopy.copieLocaleConflit);
          return item;
        }
        return edited;
      });
      merged.push(...forks);
      merged.push(...local.filter(item => !remote.some(c => c.id === item.id)));
      if (!merged.length) merged.push({id:crypto.randomUUID(),title:commonCopy.newConversation,messages:[]});
      const activeId = activeFork ?? (merged.some(c => c.id === this.activeId) ? this.activeId : merged[0]!.id);
      const selected=merged.find(c=>c.id===activeId);
      if(selected?.remoteStub && this.provider.persistence?.get) {
        const beforeHydration=this.revision;
        const full=await this.provider.persistence.get(activeId,controller.signal);
        if(controller.signal.aborted || !this.connected)return;
        if(beforeHydration!==this.revision){this.scheduleRestore();return;}
        merged[merged.indexOf(selected)]=full;
      }
      this.store.dispatch({ type: 'session/restored', conversations: merged, activeId });
      this.remoteReady = true;
      this.remoteFailed = false;
      this.retryDelay = 1000;
      for (const item of merged) if (!remote.some(c => c.id === item.id)) this.dirty.add(item.id);
      this.flush();
    } catch (error) {
      if (!controller.signal.aborted) {
        this.remoteFailed = true;
        if (!(error && typeof error === 'object' && 'status' in error && [401,403].includes(Number(error.status)))) this.scheduleRestore();
        this.announce(chatMessagesCopy.sauvegardeServeurIndisponible);
        this.store.dispatch({ type: 'storage/status', persistent: false });
      }
    } finally { if (this.restoreAbort === controller) this.restoreAbort = undefined; }
  }
  private scheduleRestore(): void {
    if (!this.connected || this.restoreTimer !== undefined) return;
    this.restoreTimer = setTimeout(() => {
      this.restoreTimer = undefined;
      if (this.connected && !this.restoreAbort) void this.restoreRemote();
      else if (this.connected) this.scheduleRestore();
    }, this.retryDelay);
    this.retryDelay = Math.min(30000, this.retryDelay * 2);
  }
  disconnect(): void {
    clearTimeout(this.restoreTimer);
    this.restoreTimer = undefined;
    this.stop();
    this.flush();
    this.connected = false;
    this.restoreAbort?.abort();
  }
  setDraft(value: string): void {
    if (!this.deleting.has(this.activeId))
      this.dispatch({ type: 'conversation/patched', id: this.activeId, patch: { draft: value } });
  }
  setScrollPosition(value: number): void {
    this.dispatch({ type: 'conversation/patched', id: this.activeId, patch: { scrollTop: value } });
  }
  addFiles(files: readonly File[]): void {
    if (this.busy) return;
    if (
      files.some((file) => file.size > 10 * 1024 * 1024) ||
      files.length + this.attachments.length > 5
    ) {
      this.announce(chatMessagesCopy.limiteCinqFichiersDeDixMegaoctetsMaximum);
      return;
    }
    this.dispatch({ type: 'attachments/changed', files: [...this.attachments, ...files] });
    this.announce(chatMessagesCopy.documentAjouteLesFichiersNeSontPas);
  }
  removeFile(index: number): void {
    this.dispatch({
      type: 'attachments/changed',
      files: this.attachments.filter((_, position) => index !== position),
    });
  }
  private scheduleSave(): void {
    if (this.connected && this.saveTimer === undefined)
      this.saveTimer = setTimeout(() => this.flush(), 250);
  }
  flush(): void {
    clearTimeout(this.saveTimer);
    this.saveTimer = undefined;
    const saved = this.repository.save(this.activeId, this.conversations, [...this.unsynced]);
    if (!saved && !this.storageFailed)
      this.announce(chatMessagesCopy.stockageLocalIndisponibleLesConversationsRestentDisponibles);
    this.storageFailed = !saved;
    this.store.dispatch({ type: 'storage/status', persistent: saved && (!this.provider.persistence || (this.remoteReady && !this.remoteFailed)) });
    if (this.provider.persistence && this.remoteReady && this.dirty.size) {
      const snapshot = structuredClone(this.conversations.filter(c =>
        !c.remoteStub && !this.deleting.has(c.id) && this.dirty.has(c.id) &&
        (c.revision !== undefined || c.messages.length || c.draft),
      ));
      const queued = new Map((this.pending ?? []).map(c => [c.id, c]));
      for (const item of snapshot) queued.set(item.id, item);
      this.pending = [...queued.values()];
      this.dirty.clear();
      if (this.pending.length && !this.saving)
        this.saving = this.saveRemote().finally(() => { this.saving = undefined; });
    }
  }
  private async saveRemote(): Promise<void> {
    while (this.pending?.length) {
      const snapshot = this.pending;
      this.pending = undefined;
      const acknowledged = new Set<string>();
      const acknowledge = (ack: {id:string;revision:number}) => {
        if (acknowledged.has(ack.id)) return;
        acknowledged.add(ack.id);
        this.store.dispatch({type:'conversation/patched',id:ack.id,patch:{revision:ack.revision}});
        const pending = this.pending as Conversation[] | undefined;
        if (pending) this.pending = pending.map(item => item.id === ack.id ? {...item,revision:ack.revision} : item);
        const original = snapshot.find(item => item.id === ack.id);
        const current = this.conversations.find(item => item.id === ack.id);
        if (original && JSON.stringify(current) === JSON.stringify({...original, revision:ack.revision})) {
          this.unsynced.delete(ack.id);
          this.dirty.delete(ack.id);
        }
        // Persist each committed revision even when a later item fails or the page closes.
        this.repository.save(this.activeId, this.conversations, [...this.unsynced]);
      };
      try {
        const acknowledgements = await this.provider.persistence!.save(snapshot, acknowledge);
        for (const ack of acknowledgements ?? []) acknowledge(ack);
        this.remoteFailed = false;
        for (const item of snapshot)
          if (JSON.stringify(this.conversations.find(c => c.id === item.id)) === JSON.stringify({...item, revision:this.conversations.find(c=>c.id===item.id)?.revision})) this.unsynced.delete(item.id);
        this.repository.save(this.activeId, this.conversations, [...this.unsynced]);
      }
      catch (error) {
        this.remoteFailed = true;
        this.remoteReady = false;
        if (!(error && typeof error === 'object' && 'status' in error && [401,403].includes(Number(error.status)))) this.scheduleRestore();
        for (const item of snapshot) if (!acknowledged.has(item.id)) this.dirty.add(item.id);
        this.announce(error && typeof error==='object' && 'status' in error && error.status===409 ? chatMessagesCopy.copieLocaleConflit : chatMessagesCopy.sauvegardeServeurIndisponible);
        this.store.dispatch({ type: 'storage/status', persistent: false });
        break;
      }
    }
  }
  get recoveredConversations():readonly Conversation[] {return this.connected?this.repository.recovered():[];}
  restoreDeletedAsNew(id:string):string|undefined {
    const original=this.repository.recovered().find(c=>c.id===id);
    if(!original)return;
    this.stop();
    const conversation:Conversation={...original,id:crypto.randomUUID(),revision:undefined,deletedAt:undefined,
      title:(original.title.slice(0,230)+chatMessagesCopy.copieLocaleConflitTitre).slice(0,255),
      messages:original.messages.map(m=>({...m,id:crypto.randomUUID()}))};
    this.dispatch({type:'conversation/created',conversation});
    if(this.repository.save(this.activeId,this.conversations,[...this.unsynced]))this.repository.clearRecovered(id);
    return conversation.id;
  }
  newConversation(): void {
    const blank = this.conversations.find((item) =>
      !item.remoteStub && !this.deleting.has(item.id) && canDeleteEmptyConversation(item),
    );
    if (blank) {
      if (blank.id !== this.activeId) this.select(blank.id);
      return;
    }
    this.stop();
    this.dispatch({ type: 'conversation/created', conversation: emptyConversation() });
  }
  async deleteEmptyConversation(id: string): Promise<boolean> {
    const item = this.conversations.find((conversation) => conversation.id === id);
    if (!item || !canDeleteEmptyConversation(item) || this.deleting.has(id) ||
        (this.activeId === id && (this.busy || this.attachments.length))) return false;
    this.deleting.add(id);
    try {
      this.pending = this.pending?.filter((conversation) => conversation.id !== id);
      await this.saving;
      const persistence = this.provider.persistence;
      const saved = this.conversations.find((conversation) => conversation.id === id)?.revision !== undefined;
      if (persistence && saved) {
        if (!persistence.deleteEmpty) throw new Error('Conversation deletion unavailable');
        await persistence.deleteEmpty(id);
      }
      const wasActive = this.activeId === id;
      if (wasActive) this.stop();
      this.restoreAbort?.abort();
      if (this.provider.persistence && !this.remoteReady && this.connected) this.scheduleRestore();
      this.dirty.delete(id);
      this.unsynced.delete(id);
      this.dispatch({ type: 'conversation/removed', id, fallback: emptyConversation() });
      if (wasActive && this.active.remoteStub) this.select(this.activeId);
      this.flush();
      this.announce(commonCopy.emptyConversationDeleted);
      return true;
    } catch {
      this.announce(commonCopy.emptyConversationDeleteFailed);
      return false;
    } finally {
      this.deleting.delete(id);
    }
  }
  select(id: string): void {
    const conversation=this.conversations.find(item=>item.id===id);
    if(!conversation)return;
    this.stop();
    if(conversation.remoteStub && this.provider.persistence?.get) {
      const controller=new AbortController();this.hydrationAbort=controller;
      this.announce(chatMessagesCopy.chargementConversation);
      void this.provider.persistence.get(id,controller.signal).then(full=>{
        if(controller.signal.aborted || !this.connected)return;
        this.store.dispatch({type:'conversation/patched',id,patch:{...full,remoteStub:false}});
        this.dispatch({type:'conversation/selected',id});
      }).catch(()=>{
        if(!controller.signal.aborted)this.announce(chatMessagesCopy.sauvegardeServeurIndisponible);
      }).finally(()=>{if(this.hydrationAbort===controller)this.hydrationAbort=undefined;});
      return;
    }
    this.dispatch({type:'conversation/selected',id});
  }
  stop(): void {
    this.hydrationAbort?.abort();
    this.abort?.abort();
  }
  async send(text = this.active.draft ?? ''): Promise<void> {
    if (!text.trim() || this.abort || this.deleting.has(this.activeId)) return;
    const id = this.activeId;
    if (!this.active.messages.length)
      this.dispatch({
        type: 'conversation/patched',
        id,
        patch: { title: text.trim().slice(0, 65) },
      });
    this.dispatch({ type: 'conversation/patched', id, patch: { draft: '' } });
    this.dispatch({
      type: 'message/added',
      conversationId: id,
      message: { id: crypto.randomUUID(), role: 'user', content: text.trim() },
    });
    await this.generate(id);
  }
  async retry(id: string): Promise<void> {
    if (this.abort) return;
    const index = this.active.messages.findIndex(
      (message) => message.id === id && message.role === 'assistant',
    );
    if (index < 1 || index !== this.active.messages.length - 1) return;
    this.dispatch({
      type: 'conversation/patched',
      id: this.activeId,
      patch: { messages: this.active.messages.slice(0, index) },
    });
    await this.generate(this.activeId);
  }
  async escalate(id: string): Promise<void> {
    if (this.abort || this.busy) return;
    const conversationId = this.activeId;
    const message = this.message(conversationId, id);
    if (!message || message.generating || message.escalating) return;

    const requestMessages = message.context
      ? structuredClone(message.context)
      : structuredClone(historyFor(this.active.messages));

    const fastAnswer = message.content;
    const fastId = message.fastId;
    const fastSources = message.fastSources ?? (message.sources ? structuredClone(message.sources) : undefined);

    this.dispatch({
      type: 'message/patched',
      conversationId,
      id,
      patch: {
        generating: true,
        escalating: true,
        fastAnswer,
        fastSources,
        fastId,
        content: '',
        activity: {
          status: 'running',
          summary: agentChat.activities?.initialSummary ?? 'Initialisation…',
          steps: [],
        },
        error: false,
        errorMessage: undefined,
      },
    });

    this.announce(chatMessagesCopy.renoPrepareUneReponse);
    await this.streamResponse(
      requestMessages,
      (chunk) => {
        const current = this.message(conversationId, id);
        if (current) {
          this.dispatch({
            type: 'message/patched',
            conversationId,
            id,
            patch: { content: current.content + chunk },
          });
        }
      },
      (result) => {
        const patch: Partial<Message> = { generating: false, escalating: false, errorMessage: undefined };
        if (result.ok) {
          patch.sources = result.sources;
          patch.mode = 'deep';
          this.announce(copyReady(this.origin));
        } else if (result.error instanceof DOMException && result.error.name === 'AbortError') {
          patch.interrupted = true;
          patch.content = fastAnswer;
          if (fastSources) patch.sources = fastSources;
          patch.mode = 'fast';
          this.announce(commonCopy.reponseInterrompue);
        } else {
          patch.error = true;
          patch.content = fastAnswer;
          if (fastSources) patch.sources = fastSources;
          patch.mode = 'fast';
          if (result.error instanceof ChatRequestError) patch.errorMessage = result.error.message;
          this.announce(chatMessagesCopy.laReponseAEchoueVousPouvezReessayer);
        }
        this.dispatch({ type: 'message/patched', conversationId, id, patch });
      },
      undefined,
      (activity) => {
        this.dispatch({
          type: 'message/patched',
          conversationId,
          id,
          patch: { activity },
        });
      },
      { mode: 'deep', fastId, fastAnswer, reason: 'Je ne suis pas satisfait de la réponse (Recherche approfondie)' },
    );
  }
  private message(conversationId: string, id: string) {
    return this.conversations
      .find((item) => item.id === conversationId)
      ?.messages.find((item) => item.id === id);
  }
  async compare(id: string): Promise<void> {
    if (this.abort) return;
    const conversationId = this.activeId;
    const index = this.active.messages.findIndex((message) => message.id === id);
    const message = this.active.messages[index];
    if (!isCompleteResponse(message)) return;
    const comparisonId = crypto.randomUUID();
    this.dispatch({
      type: 'message/patched',
      conversationId,
      id,
      patch: {
        comparison: {
          id: comparisonId,
          responseA: message.content,
          sourcesA: [...(message.sources ?? [])],
          responseB: '',
          sourcesB: [],
          state: 'generating',
        },
      },
    });
    const update = (changes: Partial<NonNullable<Message['comparison']>>) => {
      const comparison = this.message(conversationId, id)?.comparison;
      if (comparison?.id !== comparisonId) return;
      this.dispatch({
        type: 'message/patched',
        conversationId,
        id,
        comparisonId,
        patch: { comparison: { ...comparison, ...changes } },
      });
    };
    this.announce(chatMessagesCopy.uneSecondeVersionEstEnPreparation);
    await this.streamResponse(
      historyFor(this.active.messages, index),
      (chunk) => {
        const comparison = this.message(conversationId, id)?.comparison;
        if (comparison) update({ responseB: comparison.responseB + chunk });
      },
      (result) => {
        update({
          state: result.ok ? 'ready' : 'error',
          ...(result.ok ? { sourcesB: result.sources } : {}),
        });
        this.announce(
          result.ok
            ? chatMessagesCopy.lesDeuxVersionsSontDisponiblesChoisissezCelle
            : chatMessagesCopy.laComparaisonEstInterrompueOuIndisponibleVous,
        );
      },
      'alternative',
      (activity) => {
        update({ activity });
      },
    );
  }
  chooseComparison(id: string, choice: 'a' | 'b'): void {
    const message = this.message(this.activeId, id);
    const comparison = message?.comparison;
    if (
      !comparison ||
      comparison.state !== 'ready' ||
      !comparison.responseB.trim() ||
      comparison.responseA.trim() === comparison.responseB.trim()
    )
      return;
    this.dispatch({
      type: 'message/patched',
      conversationId: this.activeId,
      id,
      patch: {
        comparison: { ...comparison, choice },
        content: choice === 'a' ? comparison.responseA : comparison.responseB,
        sources: choice === 'a' ? comparison.sourcesA : comparison.sourcesB,
      },
    });
  }
  closeComparison(id: string): void {
    const message = this.message(this.activeId, id);
    if (!message?.comparison) return;
    if (message.comparison.state === 'generating') this.stop();
    this.dispatch({
      type: 'message/patched',
      conversationId: this.activeId,
      id,
      patch: { comparison: undefined },
    });
  }
  private async streamResponse(
    messages: ChatRequest['messages'],
    append: (chunk: string) => void,
    settle: (result: StreamResult) => void,
    variant?: ChatRequest['variant'],
    onProgress?: ChatRequest['onProgress'],
    options?: { mode?: 'fast' | 'deep'; fastId?: string; fastAnswer?: string; reason?: string },
  ): Promise<void> {
    const controller = new AbortController();
    this.abort = controller;
    this.dispatch({ type: 'generation/busy', value: true });
    let result: StreamResult;
    try {
      for await (const chunk of this.provider.stream({
        messages,
        attachments: [...this.attachments],
        signal: controller.signal,
        variant,
        mode: options?.mode,
        fastId: options?.fastId,
        fastAnswer: options?.fastAnswer,
        reason: options?.reason,
        onProgress: onProgress ? (activity) => {
          if (controller.signal.aborted || this.abort !== controller) return;
          onProgress(activity);
        } : undefined,
      })) {
        if (controller.signal.aborted || this.abort !== controller) break;
        controller.signal.throwIfAborted();
        append(chunk);
      }
      controller.signal.throwIfAborted();
      const lastFastId = (this.provider as unknown as { lastFastId?: string }).lastFastId;
      const lastMode = (this.provider as unknown as { lastMode?: 'fast' | 'deep' }).lastMode;
      result = { ok: true, sources: [...this.provider.sources], fastId: lastFastId, mode: lastMode ?? options?.mode };
    } catch (error) {
      result = { ok: false, error };
    }
    try {
      settle(result);
    } finally {
      if (this.abort === controller) {
        this.abort = undefined;
        this.dispatch({ type: 'generation/busy', value: false });
      }
    }
  }
  private async generate(conversationId: string): Promise<void> {
    const conversation = this.conversations.find((item) => item.id === conversationId)!;
    const requestMessages = structuredClone(historyFor(conversation.messages));
    const id = crypto.randomUUID();
    this.dispatch({
      type: 'message/added',
      conversationId,
      message: { id, role: 'assistant', content: '', generating: true, mode: 'fast', context: requestMessages },
    });
    this.announce(chatMessagesCopy.renoPrepareUneReponse);
    await this.streamResponse(
      requestMessages,
      (chunk) => {
        const message = this.message(conversationId, id)!;
        this.dispatch({
          type: 'message/patched',
          conversationId,
          id,
          patch: { content: message.content + chunk },
        });
      },
      (result) => {
        const patch: Partial<Message> = { generating: false, errorMessage: undefined };
        if (result.ok) {
          patch.sources = result.sources;
          patch.mode = result.mode ?? 'fast';
          if (result.fastId) patch.fastId = result.fastId;
          this.announce(copyReady(this.origin));
        } else if (result.error instanceof DOMException && result.error.name === 'AbortError') {
          patch.interrupted = true;
          this.announce(commonCopy.reponseInterrompue);
        } else {
          patch.error = true;
          if (result.error instanceof ChatRequestError) patch.errorMessage = result.error.message;
          this.announce(chatMessagesCopy.laReponseAEchoueVousPouvezReessayer);
        }
        this.dispatch({ type: 'message/patched', conversationId, id, patch });
      },
      undefined,
      (activity) => {
        this.dispatch({
          type: 'message/patched',
          conversationId,
          id,
          patch: { activity },
        });
      },
      { mode: 'fast' },
    );
    const message = this.message(conversationId, id)!;
    const current = this.conversations.find((item) => item.id === conversationId)!;
    if (
      !message.error &&
      !message.interrupted &&
      current.messages.filter((item) => item.role === 'user').length === 2 &&
      this.activeId === conversationId
    )
      await this.compare(id);
  }
}
