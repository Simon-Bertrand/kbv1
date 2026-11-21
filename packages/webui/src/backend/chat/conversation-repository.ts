import type { Pool, PoolClient } from 'pg';
import type { Conversation, Message } from '../../store/chat/types';
import { getDatabasePool } from './db';

export interface ConversationSummary {
  id: string;
  title: string;
  draft?: string;
  scrollTop?: number;
  createdAt: string;
  updatedAt: string;
  messageCount: number;
  revision: number;
  deletedAt?: string;
}

export class ConversationRepository {
  private pool: Pool | null;

  constructor(pool: Pool | null = getDatabasePool()) {
    this.pool = pool;
  }

  private getClient(): Pool {
    if (!this.pool) {
      throw new Error('Database pool not configured (missing WEBUI_DATABASE_URL).');
    }
    return this.pool;
  }

  private async asUser<T>(userId:string, operation:(client:PoolClient)=>Promise<T>):Promise<T> {
    const client=await this.getClient().connect();
    try {
      await client.query('BEGIN ISOLATION LEVEL REPEATABLE READ');
      await client.query("SELECT set_config('webui.user_id',$1,true)",[userId]);
      const result=await operation(client);
      await client.query('COMMIT');
      return result;
    } catch(error) {
      await client.query('ROLLBACK');throw error;
    } finally {client.release();}
  }

  async list(userId: string): Promise<ConversationSummary[]> {
    return this.asUser(userId, async client => {
    const query = `
      SELECT 
        c.id, 
        c.title, 
        c.draft, 
        c.scroll_top, 
        c.created_at, 
        c.updated_at, c.revision, c.deleted_at,
        COUNT(m.id)::int as message_count
      FROM webui.conversations c
      LEFT JOIN webui.messages m ON m.conversation_id = c.id
      WHERE c.user_id = $1
      GROUP BY c.id
      ORDER BY c.updated_at DESC, c.id DESC LIMIT 100;
    `;
    const res = await client.query(query, [userId]);
    return res.rows.map((r) => ({
      id: r.id,
      title: r.title,
      draft: r.draft ?? undefined,
      scrollTop: r.scroll_top !== null ? Number(r.scroll_top) : undefined,
      createdAt: r.created_at.toISOString(),
      updatedAt: r.updated_at.toISOString(),
      messageCount: r.message_count, revision:Number(r.revision ?? 1), deletedAt:r.deleted_at?.toISOString(),
    }));
    });
  }

  async get(id: string, userId: string): Promise<Conversation | null> {
    return this.asUser(userId, async client => {
    const convRes = await client.query(
      `SELECT id, title, draft, scroll_top, revision, updated_at FROM webui.conversations WHERE id = $1 AND user_id = $2 AND deleted_at IS NULL`,
      [id, userId],
    );

    if (convRes.rows.length === 0) {
      return null;
    }

    const conv = convRes.rows[0];
    const msgRes = await client.query(
      `SELECT id, role, content, sources, interrupted, error, error_message, generating, comparison, context,
              mode, fast_id, fast_answer, fast_sources
       FROM webui.messages
       WHERE conversation_id = $1 AND user_id = $2
       ORDER BY ordinal ASC, created_at ASC, id ASC`,
      [id, userId],
    );

    const messages = this.messages(msgRes.rows);

    return {
      id: conv.id,
      revision: Number(conv.revision ?? 1),
      updatedAt:conv.updated_at?.toISOString(),
      title: conv.title,
      draft: conv.draft ?? undefined,
      scrollTop: conv.scroll_top !== null ? Number(conv.scroll_top) : undefined,
      messages,
    };
    });
  }

  private messages(rows: Record<string, any>[]): Message[] {
    return rows.map((m) => {
      const sources = Array.isArray(m.sources)
        ? m.sources
        : typeof m.sources === 'string'
          ? (() => { try { return JSON.parse(m.sources); } catch { return undefined; } })()
          : undefined;
      const fastSources = Array.isArray(m.fast_sources)
        ? m.fast_sources
        : typeof m.fast_sources === 'string'
          ? (() => { try { return JSON.parse(m.fast_sources); } catch { return undefined; } })()
          : undefined;
      const mode = m.mode === 'fast' || m.mode === 'deep' ? m.mode : undefined;
      return {
        id: m.id,
        role: m.role as 'user' | 'assistant',
        content: m.content,
        sources: sources ?? undefined,
        interrupted: m.interrupted ?? undefined,
        error: m.error ?? undefined,
        errorMessage: m.error_message ?? undefined,
        generating: m.generating ?? undefined,
        comparison: m.comparison ?? undefined,
        context: m.context ?? undefined,
        ...(mode ? { mode } : {}),
        ...(typeof m.fast_id === 'string' && m.fast_id ? { fastId: m.fast_id } : {}),
        ...(typeof m.fast_answer === 'string' && m.fast_answer ? { fastAnswer: m.fast_answer } : {}),
        ...(fastSources ? { fastSources } : {}),
      };
    });
  }

  async page(userId: string, after = '', includeMessages=false): Promise<{conversations:Conversation[];deletedIds:string[];next:string|null}> {
    return this.asUser(userId, async client => {
    const rows=(await client.query(`SELECT c.id,c.title,c.draft,c.scroll_top,c.revision,c.deleted_at,c.updated_at,
       ${includeMessages ? `COALESCE((SELECT json_agg(m ORDER BY m.ordinal,m.created_at,m.id) FROM webui.messages m WHERE m.conversation_id=c.id AND m.user_id=$1), '[]'::json)` : `'[]'::json`} AS messages
       FROM webui.conversations c WHERE c.user_id=$1 AND c.id>$2 ORDER BY c.id LIMIT 20`,[userId,after])).rows;
    return {conversations:rows.filter(c=>!c.deleted_at).map(c=>({id:c.id,title:c.title,draft:c.draft??undefined,
      scrollTop:c.scroll_top===null?undefined:Number(c.scroll_top),revision:Number(c.revision),updatedAt:c.updated_at?.toISOString(),...(includeMessages?{}:{remoteStub:true}),messages:this.messages(c.messages)})),
      deletedIds:rows.filter(c=>c.deleted_at).map(c=>c.id),next:rows.length===20?rows.at(-1).id:null};
    });
  }

  async save(conv: Conversation, userId: string): Promise<number> {
    const pool = this.getClient();
    const client: PoolClient = await pool.connect();
    try {
      await client.query('BEGIN');
      await client.query("SELECT set_config('webui.user_id',$1,true)",[userId]);

      await client.query("SELECT pg_advisory_xact_lock(hashtextextended('webui-storage:'||$1,0))",[userId]);
      // Upsert conversation
      const owner = await client.query(
        `INSERT INTO webui.conversations (id, user_id, title, draft, scroll_top, updated_at, revision)
         VALUES ($1, $2, $3, $4, $5, NOW(), 1)
         ON CONFLICT (id) DO UPDATE SET
           title = EXCLUDED.title,
           draft = EXCLUDED.draft,
           scroll_top = EXCLUDED.scroll_top,
           updated_at = NOW(), revision = webui.conversations.revision + 1
         WHERE webui.conversations.user_id = $2 AND webui.conversations.deleted_at IS NULL AND webui.conversations.revision = $6 RETURNING id, revision`,
        [conv.id, userId, conv.title, conv.draft ?? null, conv.scrollTop ?? null, conv.revision ?? 0],
      );

      if (!owner.rowCount) throw new ConversationConflict();
      await client.query(`DELETE FROM webui.messages WHERE conversation_id = $1 AND user_id = $2 AND NOT (id = ANY($3::varchar[]))`,
        [conv.id, userId, conv.messages.map(m => m.id)]);
      for (const [ordinal, m] of conv.messages.entries()) {
        const sources = Array.isArray(m.sources) ? m.sources.slice(0, 100) : [];
        const fastSources = Array.isArray(m.fastSources) ? m.fastSources.slice(0, 100) : null;
        const mode = m.mode === 'fast' || m.mode === 'deep' ? m.mode : null;
        const fastId = typeof m.fastId === 'string' ? m.fastId.slice(0, 128) : null;
        const fastAnswer = typeof m.fastAnswer === 'string' ? m.fastAnswer.slice(0, 1000000) : null;

        const message = await client.query(
          `INSERT INTO webui.messages
            (id, conversation_id, user_id, role, content, sources, interrupted, error, error_message, generating, comparison, context, ordinal, mode, fast_id, fast_answer, fast_sources)
           VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16, $17)
           ON CONFLICT (id) DO UPDATE SET
             content = EXCLUDED.content,
             sources = EXCLUDED.sources,
             interrupted = EXCLUDED.interrupted,
             error = EXCLUDED.error,
             error_message = EXCLUDED.error_message,
             generating = EXCLUDED.generating,
             comparison = EXCLUDED.comparison,
             context = EXCLUDED.context,
             ordinal = EXCLUDED.ordinal,
             role = EXCLUDED.role,
             mode = EXCLUDED.mode,
             fast_id = EXCLUDED.fast_id,
             fast_answer = EXCLUDED.fast_answer,
             fast_sources = EXCLUDED.fast_sources
           WHERE webui.messages.user_id = $3 AND webui.messages.conversation_id = $2 RETURNING id`,
          [
            m.id,
            conv.id,
            userId,
            m.role,
            m.content,
            JSON.stringify(sources),
            Boolean(m.interrupted),
            Boolean(m.error),
            m.errorMessage ? m.errorMessage.slice(0, 4000) : null,
            Boolean(m.generating),
            m.comparison ? JSON.stringify(m.comparison) : null,
            m.context ? JSON.stringify(m.context) : null,
            ordinal,
            mode,
            fastId,
            fastAnswer,
            fastSources ? JSON.stringify(fastSources) : null,
          ],
        );
        if (!message.rowCount) throw new Error('Message owner mismatch');
      }

      await client.query(`UPDATE webui.conversations c SET storage_bytes=1024+octet_length(to_jsonb(c)::text)+COALESCE((
        SELECT sum(octet_length(to_jsonb(m)::text)) FROM webui.messages m WHERE m.conversation_id=c.id AND m.user_id=$2),0)
        WHERE c.id=$1 AND c.user_id=$2`,[conv.id,userId]);
      const usage=(await client.query(`SELECT COALESCE(sum(storage_bytes),0) AS bytes,
        count(*) FILTER(WHERE deleted_at IS NULL) AS objects FROM webui.conversations WHERE user_id=$1`,[userId])).rows?.[0];
      if(usage && (Number(usage.bytes)>100*1024*1024 || Number(usage.objects)>10000))throw new ConversationQuota();
      await client.query('COMMIT');
      return Number(owner.rows?.[0]?.revision ?? 1);
    } catch (err) {
      await client.query('ROLLBACK');
      throw err;
    } finally {
      client.release();
    }
  }

  async delete(id: string, userId: string, emptyOnly = false): Promise<boolean> {
    return this.asUser(userId, async client => {
    const res = await client.query(
      `UPDATE webui.conversations SET deleted_at=NOW(), updated_at=NOW(), revision=revision+1
       WHERE id = $1 AND user_id = $2 AND deleted_at IS NULL AND (
         NOT $3::boolean OR (COALESCE(draft, '') = '' AND NOT EXISTS (
           SELECT 1 FROM webui.messages m WHERE m.conversation_id = $1 AND m.user_id = $2
         ))
       )`,
      [id, userId, emptyOnly],
    );
    return (res.rowCount ?? 0) > 0;
    });
  }
}

export class ConversationConflict extends Error {
  readonly status = 409;
  constructor(){super('Conversation owner mismatch or stale revision');}
}

export class ConversationQuota extends Error {
  readonly status=413;
  constructor(){super('Conversation storage quota exceeded');}
}
