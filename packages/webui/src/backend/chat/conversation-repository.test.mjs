import assert from 'node:assert/strict';
import test from 'node:test';
import { ConversationRepository } from './conversation-repository.ts';
function repository(pool) {
 if(pool && !pool.connect) {
   const query=pool.query;
   pool.connect=async()=>({query:async(sql,params)=>sql.startsWith('SELECT set_config') || ['BEGIN ISOLATION LEVEL REPEATABLE READ','COMMIT','ROLLBACK'].includes(sql)?{rows:[]}:query(sql,params),release(){}});
 }
 return new ConversationRepository(pool);
}


test('ConversationRepository throws if database pool is not configured', async () => {
  const repo = repository(null);
  await assert.rejects(repo.list('alice'), /missing WEBUI_DATABASE_URL/);
});

test('ConversationRepository lists conversations filtered by user_id', async () => {
  const executed = [];
  const mockPool = {
    query: async (sql, params) => {
      executed.push({ sql, params });
      return {
        rows: [
          {
            id: 'conv-1',
            title: 'Audit Thermique',
            draft: null,
            scroll_top: '120.5',
            created_at: new Date('2026-11-14T10:00:00Z'),
            updated_at: new Date('2026-11-14T11:00:00Z'),
            message_count: 4,
          },
        ],
      };
    },
  };

  const repo = repository(mockPool);
  const list = await repo.list('user-123');

  assert.equal(executed.length, 1);
  assert.equal(executed[0].params[0], 'user-123');
  assert.equal(list.length, 1);
  assert.equal(list[0].id, 'conv-1');
  assert.equal(list[0].title, 'Audit Thermique');
  assert.equal(list[0].scrollTop, 120.5);
  assert.equal(list[0].messageCount, 4);
});

test('ConversationRepository fetches conversation and messages scoped to user_id', async () => {
  const queries = [];
  const mockPool = {
    query: async (sql, params) => {
      queries.push({ sql, params });
      if (sql.includes('FROM webui.conversations')) {
        return {
          rows: [
            {
              id: 'conv-1',
              title: 'Titre',
              draft: 'brouillon',
              scroll_top: null,
            },
          ],
        };
      }
      if (sql.includes('FROM webui.messages')) {
        return {
          rows: [
            {
              id: 'msg-1',
              role: 'user',
              content: 'Bonjour',
              sources: [],
              interrupted: false,
              error: false,
              error_message: null,
              generating: false,
              comparison: null,
              context: null,
            },
            {
              id: 'msg-2',
              role: 'assistant',
              content: 'Bonjour ! Comment vous aider ?',
              sources: [{ id: 1, title: 'Doc', category: 'cat', excerpt: 'text', pages: '1-2' }],
              interrupted: false,
              error: false,
              error_message: null,
              generating: false,
              comparison: null,
              context: null,
            },
          ],
        };
      }
      return { rows: [] };
    },
  };

  const repo = repository(mockPool);
  const conv = await repo.get('conv-1', 'user-123');

  assert.ok(conv);
  assert.equal(conv.id, 'conv-1');
  assert.equal(conv.messages.length, 2);
  assert.equal(conv.messages[0].role, 'user');
  assert.equal(conv.messages[1].sources[0].title, 'Doc');
  assert.equal(queries.length, 2);
  assert.deepEqual(queries[0].params, ['conv-1', 'user-123']);
  assert.deepEqual(queries[1].params, ['conv-1', 'user-123']);
});

test('ConversationRepository saves conversation and messages inside a transaction', async () => {
  const statements = [];
  const mockClient = {
    query: async (sql, params) => {
      if(sql.includes('pg_advisory_xact_lock') || sql.includes('storage_bytes'))return {rows:[{bytes:1024,objects:1}]};
      statements.push({ sql, params });
      return { rowCount: 1 };
    },
    release: () => {},
  };
  const mockPool = {
    connect: async () => mockClient,
  };

  const repo = repository(mockPool);
  await repo.save(
    {
      id: 'conv-99',
      title: 'Ma Conversation',
      messages: [
        {
          id: 'msg-991',
          role: 'user',
          content: 'Test',
        },
      ],
      draft: 'mon brouillon',
      scrollTop: 50,
    },
    'alice',
  );

  assert.equal(statements.shift().sql, 'BEGIN');
  assert.match(statements.shift().sql,/set_config/);
  statements.unshift({sql:'BEGIN'});
  assert.ok(statements[1].sql.includes('INSERT INTO webui.conversations'));
  assert.equal(statements[1].params[0], 'conv-99');
  assert.equal(statements[1].params[1], 'alice');
  assert.ok(statements[2].sql.includes('DELETE FROM webui.messages'));
  assert.deepEqual(statements[2].params, ['conv-99','alice',['msg-991']]);
  assert.ok(statements[3].sql.includes('INSERT INTO webui.messages'));
  assert.equal(statements[3].params[0], 'msg-991');
  assert.equal(statements[3].params[2], 'alice');
  assert.equal(statements[3].params[12], 0);
  assert.equal(statements[4].sql, 'COMMIT');
});

test('ConversationRepository deletes conversation scoped to user_id', async () => {
  const executed = [];
  const mockPool = {
    query: async (sql, params) => {
      executed.push({ sql, params });
      return { rowCount: 1 };
    },
  };

  const repo = repository(mockPool);
  const ok = await repo.delete('conv-123', 'alice');
  assert.equal(ok, true);
  assert.equal(executed[0].params[0], 'conv-123');
  assert.equal(executed[0].params[1], 'alice');
});

test('foreign conversation or message IDs cause rollback before commit', async () => {
  for (const conflict of ['conversations', 'messages']) {
    const statements = [];
    const client = {query:async(sql)=>{statements.push(sql);return {rowCount:sql.includes('INSERT INTO webui.'+conflict)?0:1};},release(){}};
    const repo = repository({connect:async()=>client});
    await assert.rejects(repo.save({id:'c',title:'t',messages:[{id:'m',role:'user',content:'q'}]},'bob'),/owner mismatch/);
    assert.equal(statements.at(-1),'ROLLBACK');
    assert.ok(!statements.includes('COMMIT'));
  }
});

test('ConversationRepository saves and loads fast path fields (mode, fastId, fastAnswer, fastSources)', async () => {
  const statements = [];
  const fastSources = [{ id: 1, title: 'Notice.pdf', category: 'Doc', excerpt: 'Extrait', pages: '1' }];
  const mockClient = {
    query: async (sql, params) => {
      statements.push({ sql, params });
      return mockPool.query(sql, params);
    },
    release: () => {},
  };
  const mockPool = {
    connect: async () => mockClient,
    query: async (sql, params) => {
      if (sql.includes('pg_advisory_xact_lock') || sql.includes('storage_bytes')) return { rows: [{ bytes: 1024, objects: 1 }] };
      if (sql.includes('INSERT INTO webui.conversations')) return { rowCount: 1, rows: [{ id: 'conv-fast', revision: 1 }] };
      if (sql.includes('INSERT INTO webui.messages')) return { rowCount: 1, rows: [{ id: params[0] }] };
      if (sql.includes('DELETE FROM webui.messages')) return { rowCount: 1 };
      if (sql.includes('FROM webui.conversations')) {
        return { rows: [{ id: 'conv-fast', title: 'Fast Conv', draft: null, scroll_top: null, revision: 1 }] };
      }
      if (sql.includes('FROM webui.messages')) {
        return {
          rows: [
            {
              id: 'msg-u1',
              role: 'user',
              content: 'Question rapide ?',
              sources: [],
              interrupted: false,
              error: false,
              error_message: null,
              generating: false,
              comparison: null,
              context: null,
              mode: null,
              fast_id: null,
              fast_answer: null,
              fast_sources: null,
            },
            {
              id: 'msg-a1',
              role: 'assistant',
              content: 'Réponse rapide text',
              sources: fastSources,
              interrupted: false,
              error: false,
              error_message: null,
              generating: false,
              comparison: null,
              context: null,
              mode: 'fast',
              fast_id: 'fast-run-123',
              fast_answer: 'Réponse rapide text',
              fast_sources: fastSources,
            },
          ],
        };
      }
      return { rows: [] };
    },
  };

  const repo = repository(mockPool);
  await repo.save({
    id: 'conv-fast',
    title: 'Fast Conv',
    messages: [
      { id: 'msg-u1', role: 'user', content: 'Question rapide ?' },
      {
        id: 'msg-a1',
        role: 'assistant',
        content: 'Réponse rapide text',
        mode: 'fast',
        fastId: 'fast-run-123',
        fastAnswer: 'Réponse rapide text',
        fastSources,
        sources: fastSources,
      },
    ],
  }, 'alice');

  // Verify save parameters
  const msgInsert = statements.find(s => s.sql.includes('INSERT INTO webui.messages') && s.params[0] === 'msg-a1');
  assert.ok(msgInsert);
  assert.equal(msgInsert.params[13], 'fast');
  assert.equal(msgInsert.params[14], 'fast-run-123');
  assert.equal(msgInsert.params[15], 'Réponse rapide text');
  assert.equal(JSON.parse(msgInsert.params[16])[0].title, 'Notice.pdf');

  // Verify distant reload (relecture distante)
  const conv = await repo.get('conv-fast', 'alice');
  assert.ok(conv);
  assert.equal(conv.messages[1].mode, 'fast');
  assert.equal(conv.messages[1].fastId, 'fast-run-123');
  assert.equal(conv.messages[1].fastAnswer, 'Réponse rapide text');
  assert.deepEqual(conv.messages[1].fastSources, fastSources);
  assert.deepEqual(conv.messages[1].sources, fastSources);
});
