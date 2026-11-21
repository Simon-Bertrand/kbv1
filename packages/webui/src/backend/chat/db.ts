import pg from 'pg';
import { log } from '../logger';

const { Pool } = pg;

declare global {
  // eslint-disable-next-line no-var
  var __webuiPgPool: pg.Pool | undefined;
}

export function getDatabasePool(databaseUrl = process.env.WEBUI_DATABASE_URL): pg.Pool | null {
  if (!databaseUrl && !process.env.WEBUI_DB_HOST) {
    return null;
  }

  if (!globalThis.__webuiPgPool) {
    globalThis.__webuiPgPool = new Pool({
      ...(process.env.WEBUI_DB_HOST ? {host:process.env.WEBUI_DB_HOST,port:Number(process.env.WEBUI_DB_PORT ?? 5432),database:process.env.WEBUI_DB_NAME ?? 'knowledgebase',user:process.env.WEBUI_DB_USER ?? 'webui-user',password:process.env.WEBUI_DB_PASSWORD} : {connectionString:databaseUrl}),
      max: 10,
      idleTimeoutMillis: 30000,
      connectionTimeoutMillis: 5000,
    });

    globalThis.__webuiPgPool.on('error', (err) => {
      log('ERROR', 'Database pool connection failed', { logger: 'database', code: (err as Error & {code?:string}).code });
    });
  }

  return globalThis.__webuiPgPool;
}

export async function closeDatabasePool(): Promise<void> {
  if (globalThis.__webuiPgPool) {
    await globalThis.__webuiPgPool.end();
    globalThis.__webuiPgPool = undefined;
  }
}
