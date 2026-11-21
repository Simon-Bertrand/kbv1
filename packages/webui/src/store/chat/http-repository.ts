import type { Conversation, ConversationPersistence } from './types';
import { isConversation, stripVolatileActivity } from './repository';

/** Optional HTTP persistence for the application; the standalone library stays local. */
export class HttpConversationRepository implements ConversationPersistence {
  deletedIds: string[] = [];
  constructor(private fetchResponse: typeof fetch = globalThis.fetch) {}
  private async request(url: string, init: RequestInit = {}, attempt=0):Promise<Response> {
    const response = await this.fetchResponse(url, { ...init, credentials: 'same-origin', cache: 'no-store', redirect: 'error',
      signal: AbortSignal.any([...(init.signal ? [init.signal] : []), AbortSignal.timeout(20000)]) });
    if(response.status===429 && attempt<3) {
      const delay=Math.min(5000,Math.max(1000,Number(response.headers.get('Retry-After')??1)*1000));
      await response.body?.cancel();
      const signal=init.signal;
      await new Promise<void>((resolve,reject)=>{
        const aborted=()=>{clearTimeout(timer);signal?.removeEventListener('abort',aborted);reject(signal?.reason??new Error('Aborted'));};
        const timer=setTimeout(()=>{signal?.removeEventListener('abort',aborted);resolve();},Number.isFinite(delay)?delay:1000);
        signal?.addEventListener('abort',aborted,{once:true});
        if(signal?.aborted)aborted();
      });
      return this.request(url,init,attempt+1);
    }
    if (!response.ok) { await response.body?.cancel(); const error = new Error('Conversation persistence unavailable') as Error & {status:number}; error.status=response.status; throw error; }
    return response;
  }
  async load(signal: AbortSignal): Promise<Conversation[]> {
    const loaded:Conversation[]=[];
    const deleted=new Set<string>();
    const seen=new Set<string>();
    let after='';
    this.deletedIds=[];
    do {
      const reply=await (await this.request('/api/chat/conversations?view=summary'+(after?'&after='+encodeURIComponent(after):''),{signal})).json();
      if (!Array.isArray(reply.conversations) || reply.conversations.length>20 || !Array.isArray(reply.deletedIds) || reply.deletedIds.length>20)
        throw new Error('Invalid conversation page');
      for(const id of reply.deletedIds) {
        if(typeof id!=='string' || !/^[a-zA-Z0-9_-]{1,128}$/.test(id))throw new Error('Invalid deleted conversation');
        deleted.add(id);
      }
      for(const conversation of reply.conversations) {
        if (!isConversation(conversation) || seen.has(conversation.id))throw new Error('Invalid conversation');
        seen.add(conversation.id);
        for(const message of conversation.messages) {
          if(message.generating){message.generating=false;message.interrupted=true;}
          if(message.comparison?.state==='generating')message.comparison.state='error';
        }
        loaded.push(stripVolatileActivity(conversation));
      }
      if(reply.next!==null && (typeof reply.next!=='string' || !/^[a-zA-Z0-9_-]{1,128}$/.test(reply.next) || reply.next<=after))
        throw new Error('Invalid conversation cursor');
      after=reply.next??'';
    } while(after);
    this.deletedIds=[...deleted];
    return loaded.sort((a,b)=>(Date.parse(b.updatedAt??'')||0)-(Date.parse(a.updatedAt??'')||0));
  }
  async get(id:string,signal:AbortSignal):Promise<Conversation> {
    if(!/^[a-zA-Z0-9_-]{1,128}$/.test(id))throw new Error('Invalid conversation');
    const {conversation}=await (await this.request('/api/chat/conversations/'+id,{signal})).json();
    if(!isConversation(conversation) || conversation.id!==id || conversation.remoteStub)throw new Error('Invalid conversation');
    for(const message of conversation.messages) {
      if(message.generating){message.generating=false;message.interrupted=true;}
      if(message.comparison?.state==='generating')message.comparison.state='error';
    }
    return stripVolatileActivity(conversation);
  }

  async deleteEmpty(id: string): Promise<void> {
    try {
      await this.request('/api/chat/conversations/' + encodeURIComponent(id) + '?empty=true', { method: 'DELETE' });
    } catch (error) {
      if (!(error instanceof Error && 'status' in error && error.status === 404)) throw error;
    }
  }

  async save(conversations: readonly Conversation[], onSaved?: (ack: {id:string;revision:number}) => void): Promise<{id:string;revision:number}[]> {
    const acknowledgements: {id:string;revision:number}[] = [];
    for (const conversation of conversations) {
      if (!isConversation(conversation) || conversation.remoteStub) throw new Error('Invalid conversation');
      const sanitized = stripVolatileActivity(conversation);
      const response = await this.request('/api/chat/conversations', {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(sanitized),
      });
      const reply = await response.json();
      if (!Number.isSafeInteger(reply.revision) || reply.revision < 1) throw new Error('Invalid conversation revision');
      const ack = { id: conversation.id, revision: reply.revision };
      acknowledgements.push(ack);
      onSaved?.(ack);
    }
    return acknowledgements;
  }
}
