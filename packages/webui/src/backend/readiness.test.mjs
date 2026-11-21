import test from 'node:test';
import assert from 'node:assert/strict';
import {readiness} from './readiness.ts';

test('readiness reports degraded services and keeps WebUI ready while SQL is available',async()=>{
 const previous=process.env.WEBUI_DB_HOST;process.env.WEBUI_DB_HOST='fixture';
 globalThis.__webuiPgPool={query:async()=>({rows:[]})};
 const env={WEBUI_KM_API_URL:'http://km',WEBUI_AGENT_URL:'http://agent',FILESERVER_INTERNAL_URL:'http://files',FILESERVER_PUBLIC_URL:'https://files.example',FILESERVER_JWT_SECRET:'x'.repeat(32),KM_AGENT_SERVICE_TOKEN:'test-token'};
 try {
  const health={status:'ok',backend:'postgres',capabilities:{lexical:true,semantic:true}};
  const fetch=async url=>url.startsWith('http://files')?new Response('OK\n'):Response.json(url.startsWith('http://agent')?{status:'ready'}:health);
  assert.equal((await readiness(env,fetch)).status,200);
  const degraded=await readiness(env,async url=>url.startsWith('http://km')?Response.json({...health,capabilities:{lexical:true,semantic:false}}):fetch(url));
  assert.equal(degraded.status,200);assert.equal((await degraded.json()).capabilities.retrieval,false);
  const lexical={...env,WEBUI_SEARCH_MODE:'lexical'};
  assert.equal((await readiness(lexical,async url=>url.startsWith('http://km')?Response.json({...health,capabilities:{lexical:true,semantic:false}}):fetch(url))).status,200);
  for (const invalid of [{...health,status:'error'},{...health,backend:'other'},
    {...health,capabilities:{}},{security_contract:'kb-internal-v1'},
    {...health,capabilities:{lexical:false,semantic:true}}]) {
    const response=await readiness(lexical,async url=>url.startsWith('http://km')?Response.json(invalid):fetch(url));
    assert.equal(response.status,200);
    assert.equal((await response.json()).capabilities.retrieval,false);
  }
  for (const agentUrl of [undefined, env.WEBUI_AGENT_URL]) {
    const unavailable = await readiness({ ...env, WEBUI_AGENT_URL: agentUrl }, async url => {
      if (url.startsWith('http://agent')) throw new Error('agent offline');
      return fetch(url);
    });
    assert.equal(unavailable.status, 200);
    assert.deepEqual(await unavailable.json(), {
      status: 'degraded',
      capabilities: { database: true, retrieval: true, agent: false, files: true },
    });
  }
  const offline = await readiness(env, async () => new Response(null, { status: 503 }));
  assert.equal(offline.status, 200);
  assert.deepEqual(await offline.json(), { status: 'degraded', capabilities: { database: true, retrieval: false, agent: false, files: false } });
  globalThis.__webuiPgPool = { query: async () => { throw new Error('database offline'); } };
  assert.equal((await readiness(env, fetch)).status, 503);
 } finally {delete globalThis.__webuiPgPool;if(previous===undefined)delete process.env.WEBUI_DB_HOST;else process.env.WEBUI_DB_HOST=previous;}
});
