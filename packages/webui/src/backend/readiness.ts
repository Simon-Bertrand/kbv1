import {getDatabasePool} from './chat/db';
import type { SearchMode } from '../store/search/types';

export function configuredSearchMode(env: Record<string, string | undefined> = process.env): SearchMode {
  const mode = env.WEBUI_SEARCH_MODE;
  return mode === 'lexical' || mode === 'semantic' ? mode : 'hybrid';
}

let cached:Response|undefined;
let validUntil=0;
let pending:Promise<Response>|undefined;

/** Coalesce public probes so they cannot multiply dependency requests. */
export async function readiness(env:Record<string,string|undefined>=process.env,fetchResponse:typeof fetch=fetch):Promise<Response> {
  if(env!==process.env || fetchResponse!==fetch)return evaluate(env,fetchResponse);
  if(cached && Date.now()<validUntil)return cached.clone();
  const request=pending??=evaluate(env,fetchResponse);
  try {
    const response=await request;
    cached=response.clone();validUntil=Date.now()+3000;
    return response.clone();
  }finally{if(pending===request)pending=undefined;}
}

async function evaluate(env:Record<string,string|undefined>,fetchResponse:typeof fetch):Promise<Response> {
  const capabilities:Record<string,boolean>={database:false,retrieval:false,agent:false,files:false};
  await Promise.all([
    (async()=>{try{
      const pool=getDatabasePool();if(!pool)return;
      await pool.query('SELECT revision,deleted_at,storage_bytes FROM webui.conversations LIMIT 0');
      await pool.query('SELECT tokens FROM webui.request_buckets LIMIT 0');capabilities.database=true;
    }catch{}})(),
    (async()=>{capabilities.retrieval=await serviceAvailable('retrieval', env, fetchResponse);})(),
    (async()=>{capabilities.agent=await serviceAvailable('agent', env, fetchResponse);})(),
    (async()=>{capabilities.files=Boolean(env.FILESERVER_JWT_SECRET && env.FILESERVER_JWT_SECRET.length>=32 && env.FILESERVER_PUBLIC_URL) && await probe(env.FILESERVER_INTERNAL_URL??'http://fileserver:8080','files',env,fetchResponse);})(),
  ]);
  const ready=Object.values(capabilities).every(Boolean);
  return Response.json({status:ready?'ready':'degraded',capabilities},{status:capabilities.database?200:503,headers:{'Cache-Control':'no-store'}});
}

async function probe(value: string | undefined, kind: SearchService | 'files', env: Record<string, string | undefined>, fetchResponse: typeof fetch): Promise<boolean> {
    try {
      const url=new URL(value??'');
      if(!['http:','https:'].includes(url.protocol) || url.username || url.password || url.pathname!=='/' || url.search || url.hash)return false;
      const response=await fetchResponse(url.origin+(kind === 'agent' || kind === 'reranker' ? '/ready' : '/health'),{
        redirect:'error',cache:'no-store',signal:AbortSignal.timeout(3000),
        headers:kind==='agent'?{Authorization:'Bearer '+(env.KM_AGENT_SERVICE_TOKEN??''),'X-Authenticated-User':'readiness'}:kind==='retrieval'?(env.KM_API_TOKEN?{Authorization:'Bearer '+env.KM_API_TOKEN}:{}):kind==='files'?{Host:new URL(env.FILESERVER_PUBLIC_URL??'').host}:{},
      });
      if(!response.ok){void response.body?.cancel();return false;}
      if(kind==='files')return (await response.text()).trim()==='OK';
      const data=await response.json();
      if (kind === 'agent' || kind === 'reranker') return data?.status === 'ready';
      if (data?.status !== 'ok' || data.backend !== 'postgres') return false;
      const capabilities = data.capabilities;
      const mode = configuredSearchMode(env);
      if (mode === 'lexical') return capabilities?.lexical === true;
      if (mode === 'semantic') return capabilities?.semantic === true;
      return (capabilities?.hybrid ?? capabilities?.semantic) === true;
    }catch{return false;}
}

type SearchService = 'agent' | 'retrieval' | 'reranker';
const serviceChecks = new Map<SearchService, { result: Promise<boolean>; expires: number }>();

export function serviceAvailable(kind: SearchService, env: Record<string, string | undefined> = process.env, fetchResponse: typeof fetch = fetch): Promise<boolean> {
  const url = { agent: env.WEBUI_AGENT_URL, retrieval: env.WEBUI_KM_API_URL, reranker: env.WEBUI_RERANKER_URL }[kind];
  if (env !== process.env || fetchResponse !== fetch) return probe(url, kind, env, fetchResponse);

  const cached = serviceChecks.get(kind);
  if (cached && Date.now() < cached.expires) return cached.result;

  const check = { result: probe(url, kind, env, fetchResponse), expires: Infinity };
  serviceChecks.set(kind, check);
  void check.result.then(() => { check.expires = Date.now() + 3000; });
  return check.result;
}
