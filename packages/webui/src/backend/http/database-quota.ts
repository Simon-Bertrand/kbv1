import {createHash} from 'node:crypto';
import type {Pool} from 'pg';
import type {RateLimitResult} from './rate-limiter';

let quotaCalls=0;

/** Atomic shared bucket; PostgreSQL time and row locks define the replica boundary. */
export async function consumeDatabaseQuota(pool:Pool,scope:string,key:string,rps:number,capacity:number,cost=1):Promise<RateLimitResult> {
 const hash=createHash('sha256').update(JSON.stringify([scope,key])).digest('hex');
 if(pool.waitingCount>=100)throw new Error('Quota pool saturated');
 const client=await pool.connect();
 try {
  await client.query('BEGIN');
  await client.query(`INSERT INTO webui.request_buckets(key_hash,tokens,updated_at) VALUES($1,$2,clock_timestamp()) ON CONFLICT DO NOTHING`,[hash,capacity]);
  const {rows:[bucket]}=await client.query(`SELECT tokens,EXTRACT(EPOCH FROM (clock_timestamp()-updated_at))*1000 AS elapsed FROM webui.request_buckets WHERE key_hash=$1 FOR UPDATE`,[hash]);
  let tokens=Math.min(capacity,Number(bucket.tokens)+Math.max(0,Number(bucket.elapsed))*rps/1000);
  const allowed=tokens>=cost;
  if(allowed)tokens-=cost;
  await client.query('UPDATE webui.request_buckets SET tokens=$2,updated_at=clock_timestamp() WHERE key_hash=$1',[hash,tokens]);
  if(++quotaCalls%64===0)await client.query(`DELETE FROM webui.request_buckets WHERE key_hash IN (
    SELECT key_hash FROM webui.request_buckets WHERE updated_at < clock_timestamp()-interval '1 day'
    ORDER BY updated_at LIMIT 128 FOR UPDATE SKIP LOCKED)`);
  await client.query('COMMIT');
  return {allowed,remaining:Math.floor(tokens),retryAfter:allowed?0:Math.max(1,Math.ceil((cost-tokens)/rps))};
 }catch(error){await client.query('ROLLBACK');throw error;}finally{client.release();}
}
