import subprocess,time,json,pathlib
import tempfile,atexit,shutil
work=pathlib.Path(tempfile.mkdtemp(prefix='kb-postgres-test-'))
atexit.register(shutil.rmtree,work,ignore_errors=True)
root=pathlib.Path(__file__).resolve().parents[3]
name='kb-audit-integration-'+str(__import__('os').getpid())
def run(*args,input=None):
 p=subprocess.run(args,input=input,text=True,capture_output=True)
 if p.returncode:raise RuntimeError(p.stderr or p.stdout)
 return p.stdout
try:
 run('docker','run','--rm','-d','--name',name,'--tmpfs','/var/lib/postgresql/data','-p','127.0.0.1::5432','-e','POSTGRES_PASSWORD=temporary-integration-only','postgres:17')
 for attempt in range(50):
  p=subprocess.run(['docker','exec',name,'pg_isready','-U','postgres'],capture_output=True)
  if p.returncode==0:break
  time.sleep(.2)
 def sql(value):return run('docker','exec','-i',name,'psql','-U','postgres','-v','ON_ERROR_STOP=1',input=value)
 assert (root/'packages/webui').is_dir()
 sql('CREATE SCHEMA rag;')
 for file in ['00_schema.sql','10_tables.sql','35_security.sql','36_isolation.sql']:
  sql((root/'packages/database/sql/webui'/file).read_text())
 sql("ALTER ROLE \"webui-user\" LOGIN PASSWORD 'isolated-pg-password';")
 port=run('docker','inspect','--format','{{(index (index .NetworkSettings.Ports "5432/tcp") 0).HostPort}}',name).strip()
 script=work/'integration.mjs'
 script.write_text('''import assert from 'node:assert/strict';
import pg from 'pg';
import {consumeDatabaseQuota} from '/home/dev/dev/knowledgebase/packages/webui/src/backend/http/database-quota.ts';
import {ConversationRepository} from '/home/dev/dev/knowledgebase/packages/webui/src/backend/chat/conversation-repository.ts';
const pool=new pg.Pool({host:'127.0.0.1',port:Number(process.env.TEST_PG_PORT),user:'webui-user',password:'isolated-pg-password',database:'postgres'});
const repo=new ConversationRepository(pool);
try {
 const owner='oidc:'+ 'long-subject'.repeat(100);
 const initial={id:'integration',title:'Original',messages:[{id:'first',role:'user',content:'keep me'}]};
 const revision=await repo.save(initial,owner);assert.equal(revision,1);
 assert.equal(await repo.get(initial.id,'bob'),null);
 assert.deepEqual(await repo.list('bob'),[]);
 // Parallel writers both possess revision 1. Exactly one wins; the loser cannot remove messages.
 const outcomes=await Promise.allSettled([repo.save({...initial,revision,title:'writer-a'},owner),repo.save({...initial,revision,title:'writer-b'},owner)]);
 assert.equal(outcomes.filter(o=>o.status==='fulfilled').length,1);
 assert.equal(outcomes.find(o=>o.status==='rejected').reason.status,409);
 const saved=await repo.get(initial.id,owner);assert.equal(saved.messages[0].content,'keep me');assert.equal(saved.revision,2);
 // RLS denies a naked query even if a developer forgets the WHERE owner predicate.
 assert.equal((await pool.query('SELECT * FROM webui.conversations')).rowCount,0);
 const client=await pool.connect();try {
  await client.query('BEGIN');await client.query("SELECT set_config('webui.user_id',$1,true)",['bob']);
  assert.equal((await client.query('SELECT * FROM webui.conversations')).rowCount,0);
  await client.query('COMMIT');
 } finally{client.release();}
 assert.equal(await repo.delete(initial.id,owner),true);
 await assert.rejects(repo.save({...saved,title:'resurrect'},owner),e=>e.status===409);
 const page=await repo.page(owner);assert.deepEqual(page.deletedIds,[initial.id]);

 // Fast Path persistence, distant reload, escalation and user isolation
 const fastSourcesList = [{ id: 1, title: 'Notice.pdf', category: 'Notice', excerpt: 'Extrait notice technique', pages: '1-2', pdf: { url: '/api/chat/runs/d12345678901234567890123456789012.0123456789012345678901234567890123456789012345678901234567890123/sources/1', page: 1 } }];
 const fastConv = {
  id: 'fast-persistence-test',
  title: 'Fast Path Conversation',
  messages: [
   { id: 'msg-u1', role: 'user', content: 'Quelle est la puissance requise ?' },
   {
    id: 'msg-a1',
    role: 'assistant',
    content: 'La puissance requise est de 12 kW selon la notice rapide.',
    mode: 'fast',
    fastId: 'run-fast-abc123',
    fastAnswer: 'La puissance requise est de 12 kW selon la notice rapide.',
    fastSources: fastSourcesList,
    sources: fastSourcesList,
   },
  ],
 };
 const fastRev = await repo.save(fastConv, owner);
 assert.equal(fastRev, 1);

 // Distant reload (relecture distante)
 const reloaded = await repo.get(fastConv.id, owner);
 assert.ok(reloaded);
 assert.equal(reloaded.messages.length, 2);
 const assistantMsg = reloaded.messages[1];
 assert.equal(assistantMsg.mode, 'fast');
 assert.equal(assistantMsg.fastId, 'run-fast-abc123');
 assert.equal(assistantMsg.fastAnswer, 'La puissance requise est de 12 kW selon la notice rapide.');
 assert.deepEqual(assistantMsg.fastSources, fastSourcesList);
 assert.deepEqual(assistantMsg.sources, fastSourcesList);
 assert.equal(assistantMsg.sources[0].pdf.page, 1);

 // User isolation: Bob cannot read or list Alice's fast path conversation
 assert.equal(await repo.get(fastConv.id, 'bob'), null);
 const bobList = await repo.list('bob');
 assert.equal(bobList.some(c => c.id === fastConv.id), false);
 const bobPage = await repo.page('bob');
 assert.equal(bobPage.conversations.some(c => c.id === fastConv.id), false);

 // Escalation: upgrade the same fast message to deep search result while preserving fastId, fastAnswer, fastSources
 const deepSourcesList = [{ id: 1, title: 'Norme-NF.pdf', category: 'Norme', excerpt: 'Calcul approfondi certifie 11.8 kW', pages: '12', pdf: { url: '/api/chat/runs/d99999999901234567890123456789012.0123456789012345678901234567890123456789012345678901234567890123/sources/1', page: 12 } }];
 const escalatedConv = {
  ...reloaded,
  messages: [
   reloaded.messages[0],
   {
    id: 'msg-a1',
    role: 'assistant',
    content: 'Après analyse approfondie, la puissance nominale certifiée est de 11.8 kW.',
    mode: 'deep',
    fastId: 'run-fast-abc123',
    fastAnswer: 'La puissance requise est de 12 kW selon la notice rapide.',
    fastSources: fastSourcesList,
    sources: deepSourcesList,
   },
  ],
 };
 const escalatedRev = await repo.save(escalatedConv, owner);
 assert.equal(escalatedRev, 2);

 // Distant reload of escalated conversation
 const reloadedEscalated = await repo.get(fastConv.id, owner);
 assert.ok(reloadedEscalated);
 const escalatedMsg = reloadedEscalated.messages[1];
 assert.equal(escalatedMsg.mode, 'deep');
 assert.equal(escalatedMsg.content, 'Après analyse approfondie, la puissance nominale certifiée est de 11.8 kW.');
 assert.equal(escalatedMsg.fastId, 'run-fast-abc123');
 assert.equal(escalatedMsg.fastAnswer, 'La puissance requise est de 12 kW selon la notice rapide.');
 assert.deepEqual(escalatedMsg.fastSources, fastSourcesList);
 assert.deepEqual(escalatedMsg.sources, deepSourcesList);

 // Bob cannot overwrite, escalate, or tamper with Alice's conversation
 await assert.rejects(repo.save({ ...escalatedConv, title: 'hacked' }, 'bob'), e => e.status === 409 || e.code === '42501' || /violates row-level security policy/i.test(e.message));
 assert.equal(await repo.get(fastConv.id, 'bob'), null);

 // Retrocompatibility: legacy message without fast path metadata loads cleanly
 const legacyConv = {
  id: 'legacy-conv-test',
  title: 'Ancienne conversation',
  messages: [
   { id: 'leg-u1', role: 'user', content: 'Historique question' },
   { id: 'leg-a1', role: 'assistant', content: 'Historique réponse sans champs fast' },
  ],
 };
 await repo.save(legacyConv, owner);
 const reloadedLegacy = await repo.get('legacy-conv-test', owner);
 assert.ok(reloadedLegacy);
 assert.equal(reloadedLegacy.messages[1].content, 'Historique réponse sans champs fast');
 assert.equal(reloadedLegacy.messages[1].mode, undefined);
 assert.equal(reloadedLegacy.messages[1].fastId, undefined);
 assert.equal(reloadedLegacy.messages[1].fastAnswer, undefined);
 assert.equal(reloadedLegacy.messages[1].fastSources, undefined);

 const checks=await Promise.all(Array.from({length:12},()=>consumeDatabaseQuota(pool,'shared-test','actor',0.001,2)));
 assert.equal(checks.filter(c=>c.allowed).length,2);
 const ownerClient=await pool.connect();
 try{await ownerClient.query('BEGIN');await ownerClient.query("SELECT set_config('webui.user_id',$1,true)",[owner]);await ownerClient.query('UPDATE webui.conversations SET storage_bytes=$1 WHERE id=$2',[101*1024*1024,initial.id]);await ownerClient.query('COMMIT');}finally{ownerClient.release();}
 await assert.rejects(repo.save({id:'over-budget',title:'test',messages:[]},owner),e=>e.status===413);
 assert.equal(await repo.get('over-budget',owner),null);
 console.log('Per-account storage quota rolls back writes: PASS');
 console.log('Shared quota across concurrent callers: PASS');
 console.log('PostgreSQL: long OIDC identity, concurrent revisions, RLS, pool isolation, tombstone: PASS');
 console.log('Fast Path: distant reload, escalation, fastSources preservation, user isolation: PASS');
 console.log('Retrocompatibility: legacy messages load without mode/fast metadata: PASS');
} finally {await pool.end();}
''')
 script.write_text(script.read_text().replace('/home/dev/dev/knowledgebase',str(root)))
 node=root/'packages/webui';modules=work/'node_modules'
 if not modules.exists():modules.symlink_to(node/'node_modules',target_is_directory=True)
 code="import {build} from 'rolldown';await build({input:'/tmp/kb-implementation/integration.mjs',external:(id)=>id.startsWith('node:')||id==='pg',output:{file:'/tmp/kb-implementation/integration-bundle.mjs',format:'es'}});"
 code=code.replace('/tmp/kb-implementation',str(work))
 p=subprocess.run(['node','--input-type=module','-e',code],cwd=node,text=True,capture_output=True)
 if p.returncode:raise RuntimeError(p.stderr)
 import os
 p=subprocess.run(['node',str(work/'integration-bundle.mjs')],env={**os.environ,'TEST_PG_PORT':port},text=True,capture_output=True)
 print(p.stdout)
 if p.returncode:raise RuntimeError(p.stderr)
finally:
 subprocess.run(['docker','rm','-f',name],capture_output=True)
