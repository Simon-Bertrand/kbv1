import test from 'node:test';
import assert from 'node:assert/strict';
import { ChatModel } from '../../store/chat/model.ts';
import { HttpConversationRepository } from '../../store/chat/http-repository.ts';
import { createConversationHandlers } from './conversation-handlers.ts';
import { withRequestContext,upstreamHeaders,actorId } from '../logger.ts';
const initial = {id:'new',title:'New',messages:[]};
const conversation = {id:'saved',title:'Saved',messages:[{id:'m',role:'assistant',content:'Answer'}]};
const tick=()=>new Promise(resolve=>setTimeout(resolve,10));

test('server conversations restore, queue saves, preserve message order and isolate bootstrap',async()=>{
 const storage=new Map();globalThis.localStorage={getItem:k=>storage.get(k)??null,setItem:(k,v)=>storage.set(k,v)};
 const saved=[];let resolveSave;
 const model=new ChatModel(initial,{sources:[],async *stream(){},persistence:{load:async()=>[structuredClone(conversation)],save:async items=>{saved.push(items);if(saved.length===1)await new Promise(r=>resolveSave=r);}}},()=>{});
 model.connect();await tick();
 assert.equal(model.activeId,'saved');assert.equal(model.conversations.length,1);
 model.setDraft('first');model.flush();await tick();
 model.setDraft('latest');model.flush();await tick();
 assert.equal(saved.length,1);resolveSave();await tick();assert.equal(saved.length,2);
 assert.equal(saved[1][0].draft,'latest');model.disconnect();delete globalThis.localStorage;
});
test('late server restoration cannot overwrite edits made while loading',async()=>{
 let restore;const model=new ChatModel(conversation,{sources:[],async *stream(){},persistence:{load:()=>new Promise(r=>restore=r),save:async()=>{}}},()=>{});
 model.connect();model.setDraft('edited');restore([structuredClone(conversation)]);await tick();
 assert.equal(model.active.draft,'edited');model.disconnect();
});
test('HTTP persistence restores interrupted generations and sends snapshots to authenticated routes',async()=>{
 const calls=[];const repository=new HttpConversationRepository(async(url,init)=>{
  calls.push({url,init});return Response.json(init.method==='POST'?{ok:true,revision:1}:{conversations:[{...conversation,messages:[{...conversation.messages[0],generating:true}]}],deletedIds:[],next:null});
 });
 const loaded=await repository.load(new AbortController().signal);
 assert.equal(loaded[0].messages[0].interrupted,true);assert.equal(loaded[0].messages[0].generating,false);
 await repository.save(loaded);assert.equal(calls.at(-1).init.credentials,'same-origin');assert.equal(calls.at(-1).init.method,'POST');
});
test('conversation API checks session, CSRF and bounded validated data before repository access',async()=>{
 const calls=[];const repo={list:async user=>{calls.push(user);return []},save:async(c,u)=>calls.push(u),get:async()=>null};
 const req=(body,headers={})=>new Request('http://webui/api/chat/conversations',{method:'POST',headers:{'Content-Type':'application/json',...headers},body:JSON.stringify(body)});
 assert.equal((await createConversationHandlers(async()=>null,repo)(req(conversation))).status,401);
 const handle=createConversationHandlers(async()=>({id:'alice'}),repo);
 assert.equal((await handle(req(conversation,{Origin:'https://evil'}))).status,403);
 assert.equal((await handle(req({...conversation,messages:[{id:'m',role:'system',content:'x'}]}))).status,400);
 assert.equal((await handle(req({...conversation,id:'../x'}))).status,400);
 assert.equal(calls.length,0);
 assert.equal((await handle(req(conversation))).status,200);assert.deepEqual(calls,['alice']);
});
test('concurrent request contexts retain their own verified user and request ID',async()=>{
 const results=await Promise.all(['alice','bob'].map((user,i)=>withRequestContext(new Request('http://webui/api/chat/runs/SECRET',{headers:{'X-Request-Id':'request-'+i,'X-Authenticated-User':'forged'}}),user,async()=>{
  await tick();const headers=upstreamHeaders();return Response.json({user:headers.get('X-Authenticated-User'),id:headers.get('X-Request-Id')});
 })));
 assert.deepEqual(await results[0].json(),{user:actorId('alice'),id:'request-0'});assert.deepEqual(await results[1].json(),{user:actorId('bob'),id:'request-1'});
});

test('offline edits survive a reload and are uploaded instead of overwritten by the older server copy',async()=>{
 const storage=new Map();globalThis.localStorage={getItem:k=>storage.get(k)??null,setItem:(k,v)=>storage.set(k,v)};
 const failed=new ChatModel(conversation,{sources:[],async *stream(){},persistence:{load:async()=>[structuredClone(conversation)],save:async()=>{throw new Error('offline')}}},()=>{});
 failed.connect();await tick();failed.setDraft('offline draft');failed.flush();await tick();failed.disconnect();
 const uploaded=[];
 const restored=new ChatModel(initial,{sources:[],async *stream(){},persistence:{load:async()=>[structuredClone(conversation)],save:async rows=>uploaded.push(rows)}},()=>{});
 restored.connect();await tick();
 assert.equal(restored.active.draft,'offline draft');assert.equal(uploaded.at(-1)[0].draft,'offline draft');
 restored.disconnect();delete globalThis.localStorage;
});


test('HTTP restoration paginates beyond one hundred conversations and reads tombstones',async()=>{
 let page=0;
 const repo=new HttpConversationRepository(async()=>{
   const conversations=Array.from({length:20},(_,i)=>({id:'c'+String(page*20+i).padStart(4,'0'),title:'Stored',messages:[]}));
   page++;return Response.json({conversations,deletedIds:page===1?['deleted']:[],next:page===6?null:conversations.at(-1).id});
 });
 assert.equal((await repo.load(new AbortController().signal)).length,120);
 assert.equal(page,6);assert.deepEqual(repo.deletedIds,['deleted']);
});


test('a conflicting remote revision creates a separate local copy without overwriting either version',async()=>{
 let loads=0;const writes=[];
 const remote={...conversation,revision:1};
 const model=new ChatModel(initial,{sources:[],async *stream(){},persistence:{
  load:async()=>[{...remote,revision:++loads===1?1:2,title:loads===1?'Initial':'Other device'}],
  save:async rows=>{
   if(rows.some(c=>c.id==='saved'))throw Object.assign(new Error('conflict'),{status:409});
   writes.push(rows);return rows.map(c=>({id:c.id,revision:1}));
  }
 }},()=>{});
 model.connect();await tick();model.setDraft('my local draft');model.flush();await tick();
 await new Promise(resolve=>setTimeout(resolve,1100));
 assert.equal(model.conversations.find(c=>c.id==='saved').title,'Other device');
 const local=model.conversations.find(c=>c.id!=='saved');
 assert.equal(local.draft,'my local draft');assert.notEqual(local.messages[0].id,conversation.messages[0].id);
 assert.equal(model.activeId,local.id);assert.ok(writes.length);
 model.disconnect();
});


test('remote summaries hydrate only the active conversation and later selected histories',async()=>{
 const loaded=[];
 const model=new ChatModel(initial,{sources:[],async *stream(){},persistence:{
  load:async()=>['one','two'].map(id=>({id,title:id,revision:1,messages:[],remoteStub:true})),
  get:async id=>{loaded.push(id);return {id,title:id,revision:1,messages:[{id:'m-'+id,role:'user',content:'history '+id}]};},
  save:async()=>{}
 }},()=>{});
 model.connect();await tick();assert.deepEqual(loaded,['one']);assert.equal(model.active.messages[0].content,'history one');
 assert.equal(model.conversations.find(c=>c.id==='two').remoteStub,true);
 model.select('two');await tick();assert.deepEqual(loaded,['one','two']);assert.equal(model.active.messages[0].content,'history two');
 model.disconnect();
});


test('remote deletion quarantines offline edits and recovery explicitly creates new IDs',async()=>{
 const values=new Map();globalThis.localStorage={getItem:k=>values.get(k)??null,setItem:(k,v)=>values.set(k,v)};
 values.set('reno-plus-conversations-v1',JSON.stringify({version:1,activeId:'saved',conversations:[{...conversation,draft:'offline changes',revision:1}],dirtyIds:['saved']}));
 const writes=[];
 const model=new ChatModel(initial,{sources:[],async *stream(){},persistence:{deletedIds:['saved'],load:async()=>[],save:async rows=>{writes.push(rows);return rows.map(c=>({id:c.id,revision:1}));}}},()=>{});
 model.connect();await tick();
 assert.ok(!model.conversations.some(c=>c.id==='saved'));
 assert.equal(model.recoveredConversations[0].draft,'offline changes');
 assert.ok(!writes.flat().some(c=>c.id==='saved'));
 const restored=model.restoreDeletedAsNew('saved');model.flush();await tick();
 assert.notEqual(restored,'saved');assert.equal(model.active.draft,'offline changes');
 assert.notEqual(model.active.messages[0].id,conversation.messages[0].id);
 assert.equal(model.recoveredConversations.length,0);
 model.disconnect();delete globalThis.localStorage;
});

test('HTTP and localStorage persistence strip volatile activity from normal and comparison messages on save and restore', async () => {
  const volatileActivity = {
    status: 'running',
    summary: 'Recherche en cours',
    steps: [{ id: 's1', type: 'tool', phase: 'search', label: 'Recherche', status: 'complete', timestamp: Date.now() }],
  };

  const convWithActivity = {
    id: 'conv-with-activities',
    title: 'With activities',
    messages: [
      {
        id: 'msg-1',
        role: 'assistant',
        content: 'Normal answer',
        activity: volatileActivity,
      },
      {
        id: 'msg-2',
        role: 'assistant',
        content: 'Comparison parent',
        activity: volatileActivity,
        comparison: {
          id: 'cmp-1',
          responseA: 'Ans A',
          sourcesA: [],
          responseB: 'Ans B',
          sourcesB: [],
          state: 'ready',
          activity: volatileActivity,
        },
      },
    ],
  };

  // 1. Test HttpConversationRepository save
  let sentPayload = null;
  const httpRepo = new HttpConversationRepository(async (_url, init) => {
    if (init.method === 'POST') {
      sentPayload = JSON.parse(init.body);
      return Response.json({ ok: true, revision: 1 });
    }
    return Response.json({ conversations: [sentPayload ?? convWithActivity], deletedIds: [], next: null });
  });

  await httpRepo.save([convWithActivity]);

  // Assert sentPayload has stripped activities
  assert.equal(sentPayload.messages[0].activity, undefined);
  assert.equal(sentPayload.messages[1].activity, undefined);
  assert.equal(sentPayload.messages[1].comparison.activity, undefined);

  // Assert loaded payload from HTTP has no activities
  const loadedHttp = await httpRepo.load(new AbortController().signal);
  assert.equal(loadedHttp[0].messages[0].activity, undefined);
  assert.equal(loadedHttp[0].messages[1].activity, undefined);
  assert.equal(loadedHttp[0].messages[1].comparison.activity, undefined);

  // 2. Test ChatRepository (localStorage) save and load
  const storage = new Map();
  globalThis.localStorage = {
    getItem: (k) => storage.get(k) ?? null,
    setItem: (k, v) => storage.set(k, v),
  };

  const { ChatRepository } = await import('../../store/chat/repository.ts');
  const chatRepo = new ChatRepository();
  chatRepo.save(convWithActivity.id, [convWithActivity]);

  const rawSavedJson = storage.get('reno-plus-conversations-v1');
  assert.ok(rawSavedJson);
  assert.equal(rawSavedJson.includes('Recherche en cours'), false);
  assert.equal(rawSavedJson.includes('"activity":'), false);

  const loadedLocal = chatRepo.load();
  assert.ok(loadedLocal);
  const loadedConv = loadedLocal.conversations[0];
  assert.equal(loadedConv.messages[0].activity, undefined);
  assert.equal(loadedConv.messages[1].activity, undefined);
  assert.equal(loadedConv.messages[1].comparison?.activity, undefined);

  delete globalThis.localStorage;
});


test('HTTP persistence acknowledges committed revisions before a later save fails',async()=>{
 const saved=[];
 let fail=true;
 const repo=new HttpConversationRepository(async(_url,init)=>{
  const item=JSON.parse(init.body);
  if(item.id==='second' && fail)return new Response(null,{status:503});
  return Response.json({revision:(item.revision??0)+1});
 });
 const items=[{id:'first',title:'A',revision:1,messages:[]},{id:'second',title:'B',revision:1,messages:[]}];
 await assert.rejects(repo.save(items,ack=>{saved.push(ack);items.find(c=>c.id===ack.id).revision=ack.revision;}),{status:503});
 assert.deepEqual(saved,[{id:'first',revision:2}]);
 assert.equal(items[0].revision,2);
 fail=false;
 await repo.save([items[1]],ack=>saved.push(ack));
 assert.deepEqual(saved,[{id:'first',revision:2},{id:'second',revision:2}]);
});

test('model retains partial acknowledgements across retry without creating false conflict copies',async()=>{
 const values=new Map();
 globalThis.localStorage={getItem:k=>values.get(k)??null,setItem:(k,v)=>values.set(k,v)};
 const remote=[{id:'first',title:'A',revision:1,messages:[]},{id:'second',title:'B',revision:1,messages:[]}];
 const batches=[];
 const model=new ChatModel(initial,{sources:[],async *stream(){},persistence:{
  load:async()=>structuredClone(remote),
  save:async(items,onSaved)=>{
   batches.push(items.map(c=>c.id));
   const acknowledgements=[];
   for(const item of items) {
    if(item.id==='second' && batches.length===1)throw Object.assign(new Error('temporary failure'),{status:503});
    const ack={id:item.id,revision:item.revision+1};
    remote.splice(remote.findIndex(c=>c.id===item.id),1,{...structuredClone(item),revision:ack.revision});
    onSaved?.(ack);acknowledgements.push(ack);
   }
   return acknowledgements;
  }
 }},()=>{});
 try {
  model.connect();await tick();
  model.setDraft('edit A');model.select('second');model.setDraft('edit B');model.flush();await tick();
  assert.equal(model.conversations.find(c=>c.id==='first').revision,2);
  const local=JSON.parse(values.get('reno-plus-conversations-v1'));
  assert.equal(local.conversations.find(c=>c.id==='first').revision,2);
  assert.ok(!local.dirtyIds.includes('first'));
  await new Promise(resolve=>setTimeout(resolve,1100));
  assert.equal(model.conversations.length,2);
  assert.deepEqual(batches,[['first','second'],['second']]);
  assert.equal(model.conversations.find(c=>c.id==='second').revision,2);
 } finally {model.disconnect();delete globalThis.localStorage;}
});
