"""Durable E5 checkpoints. The caller owns the global PostgreSQL writer lock."""
from psycopg.rows import dict_row

STATES = ('pending', 'encoding', 'encoded', 'indexed', 'failed', 'interrupted')


class Checkpoints:
    def __init__(self, conn, document_id, fingerprint, hashes, *, profile=None):
        self.conn, self.doc, self.fingerprint, self.hashes = conn, document_id, fingerprint, hashes
        self.profile=profile
        with conn.transaction():
            conn.execute("UPDATE rag.km_chunk_indexing SET status='interrupted',last_error='Writer interrupted',updated_at=now() WHERE status='encoding' AND encoder_fingerprint=%s",(fingerprint,))
            conn.execute('UPDATE rag.km_chunk_indexing SET is_current=false WHERE document_id=%s AND encoder_fingerprint=%s AND is_current', (self.doc,self.fingerprint))
            with conn.cursor() as cursor:
                cursor.executemany("""INSERT INTO rag.km_chunk_indexing(document_id,chunk_id,input_hash,encoder_fingerprint,profile)
                    VALUES (%s,%s,%s,%s,%s) ON CONFLICT(document_id,chunk_id,input_hash,encoder_fingerprint)
                    DO UPDATE SET is_current=true,profile=EXCLUDED.profile""",
                    ((*self.key(chunk),profile) for chunk in hashes))

    def key(self, chunk):
        return self.doc, chunk, self.hashes[chunk], self.fingerprint

    def saved(self):
        return {r['chunk_id']: r['embedding'] for r in self.conn.execute("""
            SELECT chunk_id,embedding::text FROM rag.km_chunk_indexing
            WHERE document_id=%s AND encoder_fingerprint=%s AND is_current
              AND status IN ('encoded','indexed') AND embedding IS NOT NULL""", (self.doc,self.fingerprint)).fetchall()}

    def begin(self, records):
        with self.conn.transaction(), self.conn.cursor() as cursor:
            cursor.executemany("""UPDATE rag.km_chunk_indexing SET status='encoding',attempts=attempts+1,
                last_error=NULL,updated_at=now() WHERE document_id=%s AND chunk_id=%s AND input_hash=%s AND encoder_fingerprint=%s""",
                (self.key(record['id']) for record in records))

    def store(self, vectors, *, published=False):
        with self.conn.transaction(), self.conn.cursor() as cursor:
            cursor.executemany("""UPDATE rag.km_chunk_indexing SET embedding=%s::rag.vector,status=%s,
                last_error=NULL,updated_at=now(),indexed_at=CASE WHEN %s THEN now() ELSE indexed_at END
                WHERE document_id=%s AND chunk_id=%s AND input_hash=%s AND encoder_fingerprint=%s""",
                ((vector,'indexed' if published else 'encoded',published,*self.key(chunk))
                 for chunk,vector in vectors.items()))

    def failed(self, records, error):
        with self.conn.transaction(), self.conn.cursor() as cursor:
            cursor.executemany("""UPDATE rag.km_chunk_indexing SET status='failed',last_error=%s,updated_at=now()
                WHERE document_id=%s AND chunk_id=%s AND input_hash=%s AND encoder_fingerprint=%s""",
                ((type(error).__name__,*self.key(record['id'])) for record in records))

    def published(self):
        # Part of the same transaction as documents/chunks/text_embeddings.
        self.conn.execute("""UPDATE rag.km_chunk_indexing SET status='indexed',indexed_at=now(),
            last_error=NULL,updated_at=now() WHERE document_id=%s AND encoder_fingerprint=%s AND is_current""", (self.doc,self.fingerprint))


def listing(conn, *, document_id=None, status=None, profile=None, limit=100, offset=0):
    where, params = 'is_current AND profile=%s', [profile]
    if document_id:
        where += ' AND document_id=%s'
        params.append(document_id)
    summary = {s: 0 for s in STATES}
    with conn.transaction():
        # A single consistent snapshot for counters and rows.
        conn.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
        for row in conn.execute('SELECT status,count(*) AS n FROM rag.km_chunk_indexing WHERE '+where+' GROUP BY status', params):
            summary[row['status']] = row['n']
        published_total=conn.execute("SELECT count(*) AS n FROM rag.km_text_embeddings WHERE profile=%s",(profile,)).fetchone()['n']
        if status:
            where += ' AND status=%s'
            params.append(status)
        total = conn.execute('SELECT count(*) AS n FROM rag.km_chunk_indexing WHERE '+where, params).fetchone()['n']
        rows = conn.execute('SELECT document_id,chunk_id,input_hash,encoder_fingerprint,status,attempts,last_error,created_at,updated_at,indexed_at FROM rag.km_chunk_indexing WHERE '+where+' ORDER BY document_id,chunk_id LIMIT %s OFFSET %s', [*params,limit,offset]).fetchall()
    return {'items':rows,'summary':summary,'profile':profile,'published_total':published_total,'total':total,'limit':limit,'offset':offset}
