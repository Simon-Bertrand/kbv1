"""Atomic PostgreSQL publication from PdfParse records and the dedicated E5 service."""
import hashlib
import json
import math
import os

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from .checkpoints import Checkpoints, listing
from km.profiles import register, snapshot, active, lock
from km.cache_contract import validate
from km.inventory import indexed_document_identity

def digest(text):
    return hashlib.sha256(text.encode()).hexdigest()

class Publisher:
    def __init__(self, dsn, encoder, *, km=False):
        self.dsn, self.encoder, self.km = dsn, encoder, km

    def publish(self, bundle, progress=lambda stage: None, *, verify_source=None, profile=None, chunk_ids=None, stop=lambda:False):
        validate(bundle)
        metadata = self.encoder.metadata
        fingerprint = self.encoder.fingerprint
        if metadata.get('dimension') != 1024:
            raise ValueError('KM E5 requires 1024 dimensions')
        doc, chunks, visuals = bundle['document_id'], bundle['chunks'], bundle['visuals']
        tokenizer_hash = metadata.get('chunk_tokenizer_hash')
        for record in chunks:
            tokenizer_id = record.get('tokenizer_id')
            if (tokenizer_hash and isinstance(tokenizer_id, str)
                    and tokenizer_id.startswith('multilingual-e5-large:passage:') and ':files-v1:' in tokenizer_id
                    and tokenizer_id.rsplit(':files-v1:', 1)[-1] != tokenizer_hash):
                raise ValueError('Tokenizer E5 différent entre PdfParse et l’encodeur. Recalculer les chunks.')
        original_name, type_document_id = indexed_document_identity(bundle)
        progress('embedding')
        with psycopg.connect(self.dsn, autocommit=True, connect_timeout=5, row_factory=dict_row) as conn:
            if not conn.execute("SELECT pg_try_advisory_lock(hashtextextended('km-embedding-writer',0)) AS ok").fetchone()['ok']:
                raise RuntimeError('Another KM embedding writer is active')
            registered=register(conn,metadata,fingerprint,profile)
            profile=registered['profile']
            bundle={**bundle,'metadata':{**bundle.get('metadata',{}),
                'embedding_profile':profile,'embedding_encoder_fingerprint':fingerprint,
                'embedding_configuration':metadata,'embedding_source':'pdfparse_cache'}}
            existing = {r['chunk_id']: r for r in conn.execute(
                'SELECT e.chunk_id,e.input_hash,e.embedding::text,u.document_id FROM rag.km_text_embeddings e JOIN rag.km_profile_units u ON u.profile=e.profile AND u.unit_id=e.chunk_id WHERE e.profile=%s AND e.chunk_id = ANY(%s)',
                (profile,[r['id'] for r in chunks])).fetchall()}
            if any(row['document_id'] != doc for row in existing.values()):
                raise ValueError('PdfParse record identity belongs to another document')
            hashes = {r['id']: digest(r['embedding_text']) for r in chunks}
            checkpoints = Checkpoints(conn, doc, fingerprint, hashes, profile=profile)
            vectors = checkpoints.saved()
            published = {record['id']: existing[record['id']]['embedding'] for record in chunks
                         if record['id'] in existing and existing[record['id']]['input_hash'] == hashes[record['id']]}
            vectors.update(published)
            checkpoints.store(published, published=True)
            pending = [record for record in chunks if record['id'] not in vectors]
            if chunk_ids is not None:
                if not set(chunk_ids)<={record['id'] for record in chunks}:raise ValueError('Chunk absent du cache vérifié.')
                pending=[record for record in pending if record['id'] in chunk_ids]
            reused = len(vectors)
            encoded_count=0
            def report():
                progress({'stage':'embedding','document_id':doc,'total':len(chunks),
                          'saved':len(vectors),'reused':reused,'encoded':len(vectors)-reused,'profile':profile})
            report()
            # Persist each batch before proceeding; publication stays document-atomic.
            for start in range(0, len(pending), 16):
                if stop():break
                batch = pending[start:start + 16]
                checkpoints.begin(batch)
                try:
                    tokenizer = None if getattr(self.encoder, 'validates_token_budget', False) is True else getattr(self.encoder, 'tokenizer', None)
                    max_tokens = int(metadata.get('max_tokens', 512)) if tokenizer else None
                    for record in batch:
                        if len(record['embedding_text']) > 32000:
                            raise RuntimeError('Chunk PdfParse exceeds E5 character limit; adjust chunking in Studio')
                        if tokenizer and len(tokenizer.encode('passage: ' + record['embedding_text'])) > max_tokens:
                            raise RuntimeError(f'Chunk PdfParse exceeds E5 {max_tokens}-token budget; adjust chunking in Studio, no truncation applied')
                    encoded = self.encoder.encode([r['embedding_text'] for r in batch], query=False)
                    if len(encoded) != len(batch):
                        raise ValueError('Wrong E5 batch length')
                    saved = {}
                    for record, vector in zip(batch, encoded):
                        if (len(vector) != 1024 or any(type(v) not in (int, float) or not math.isfinite(v) for v in vector)
                                or abs(sum(v*v for v in vector) - 1) > .01):
                            raise ValueError('Invalid E5 vector')
                        saved[record['id']] = json.dumps(vector, allow_nan=False)
                    if fingerprint != self.encoder.fingerprint:
                        raise ValueError('Encoder identity changed during indexing')
                    checkpoints.store(saved)
                    vectors.update(saved)
                    encoded_count+=len(saved)
                except Exception as exc:
                    checkpoints.failed(batch, exc)
                    raise
                report()
            if fingerprint != self.encoder.fingerprint:
                raise ValueError('Encoder identity changed during indexing')
            if verify_source:
                verify_source()
            if len(vectors)!=len(chunks):
                return {'document_id':doc,'chunks':len(chunks),'encoded':encoded_count,'reused':reused,
                        'remaining':len(chunks)-len(vectors),'published':False,'stopped':stop(),
                        'profile':profile,'encoder_fingerprint':fingerprint}
            progress('publication')
            with conn.transaction():
                conn.execute("SET LOCAL lock_timeout = '5s'")
                conn.execute("SET LOCAL statement_timeout = '120s'")
                # Never steal a chunk/figure ID belonging to another document.
                for table, column, records in [('chunks','chunk_id',chunks),('visuals','visual_id',visuals)]:
                    rows = conn.execute(f'SELECT document_id FROM rag.{table} WHERE {column} = ANY(%s) AND document_id <> %s',
                                        ([r['id'] for r in records], doc)).fetchall()
                    if rows:
                        raise ValueError('PdfParse record identity belongs to another document')
                conn.execute("""INSERT INTO rag.documents(document_id,filepath,metadata,original_name,type_document_id) VALUES (%s,%s,%s,%s,%s)
                    ON CONFLICT(document_id) DO UPDATE SET filepath=EXCLUDED.filepath,
                    metadata=rag.documents.metadata || EXCLUDED.metadata,original_name=EXCLUDED.original_name,
                    type_document_id=EXCLUDED.type_document_id,indexed_at=now()""",
                             (doc, bundle['filepath'], Jsonb(bundle.get('metadata', {})), original_name, type_document_id))
                with conn.cursor() as cursor:
                    cursor.executemany("""INSERT INTO rag.chunks VALUES (%s,%s,%s)
                        ON CONFLICT(chunk_id) DO UPDATE SET record=EXCLUDED.record""",
                        ((record['id'], doc, Jsonb(record)) for record in chunks))
                    cursor.executemany("""INSERT INTO rag.visuals VALUES (%s,%s,%s)
                        ON CONFLICT(visual_id) DO UPDATE SET record=EXCLUDED.record""",
                        ((record['id'], doc, Jsonb(record)) for record in visuals))
                    cursor.execute('DELETE FROM rag.chunk_visuals WHERE visual_id IN (SELECT visual_id FROM rag.visuals WHERE document_id=%s)', (doc,))
                    cursor.executemany('INSERT INTO rag.chunk_visuals VALUES (%s,%s)',
                        ((chunk, record['id']) for record in visuals for chunk in record.get('chunk_ids', [])))
                conn.execute('DELETE FROM rag.chunks WHERE document_id=%s AND NOT (chunk_id = ANY(%s))',
                             (doc, [r['id'] for r in chunks]))
                conn.execute('DELETE FROM rag.visuals WHERE document_id=%s AND NOT (visual_id = ANY(%s))',
                             (doc, [r['id'] for r in visuals]))
                if self.km:
                    from km.metadata import write_metadata, write_search_units
                    write_metadata(conn,bundle)
                    write_search_units(conn,bundle)
                snapshot(conn,profile,bundle,vectors,hashes,km=self.km,
                         original_name=original_name,type_document_id=type_document_id)
                checkpoints.published()
        return {'document_id': doc, 'chunks': len(chunks), 'visuals': len(visuals),
                'encoded': encoded_count, 'reused': reused, 'published':True,'profile': profile, 'encoder_fingerprint': fingerprint}
    def chunks(self, **kwargs):
        with psycopg.connect(self.dsn, autocommit=True, row_factory=dict_row, connect_timeout=5) as conn:
            if not kwargs.get('profile'):kwargs['profile']=active(conn)
            return listing(conn, **kwargs)

    def ensure_current_profile(self,requested=None):
        metadata=self.encoder.metadata
        with psycopg.connect(self.dsn,autocommit=True,row_factory=dict_row,connect_timeout=5) as conn:
            with conn.transaction():
                lock(conn)
                return register(conn,metadata,self.encoder.fingerprint,requested)

    def profiles(self):
        encoder=None;encoder_error=None
        try:encoder=self.encoder.metadata
        except (RuntimeError,ValueError):encoder_error='Encodeur indisponible : les profils conservés restent consultables.'
        with psycopg.connect(self.dsn,autocommit=True,row_factory=dict_row,connect_timeout=5) as conn:
            selected=active(conn)
            unassigned=conn.execute('SELECT count(*) AS n FROM rag.km_chunk_indexing WHERE profile IS NULL').fetchone()['n']
            rows=conn.execute("""
                SELECT p.*,coalesce(l.label,p.configuration->>'model',p.profile) AS label,
                  coalesce(d.documents,0) AS documents,coalesce(v.chunks,0) AS chunks,
                  coalesce(c.saved,0) AS saved,coalesce(c.failed,0) AS failed
                FROM rag.embedding_profiles p LEFT JOIN rag.km_profile_labels l USING(profile)
                LEFT JOIN (SELECT profile,count(*) AS documents FROM rag.km_profile_documents GROUP BY profile) d USING(profile)
                LEFT JOIN (SELECT profile,count(*) AS chunks FROM rag.km_text_embeddings GROUP BY profile) v USING(profile)
                LEFT JOIN (SELECT profile,count(*) FILTER(WHERE status='encoded') AS saved,
                           count(*) FILTER(WHERE status IN ('failed','interrupted')) AS failed
                           FROM rag.km_chunk_indexing WHERE is_current GROUP BY profile) c USING(profile)
                WHERE p.modality='text' ORDER BY p.profile
                """).fetchall()
        for row in rows:
            row['verified']=not row['configuration'].get('legacy_unverified',False)
            row['compatible']=bool(encoder and row['verified'] and row['encoder_fingerprint']==encoder.get('fingerprint') and row['dimension']==encoder.get('dimension'))
            row['active']=row['profile']==selected
        return {'items':rows,'unassigned_checkpoints':unassigned,'active_profile':selected,'encoder':encoder,'encoder_error':encoder_error,
                'current_profile':next((r['profile'] for r in rows if r['compatible']),None)}

    def activate_profile(self,profile):
        with psycopg.connect(self.dsn,autocommit=True,row_factory=dict_row,connect_timeout=5) as conn:
            with conn.transaction():
                lock(conn)
                row=conn.execute("SELECT * FROM rag.embedding_profiles WHERE profile=%s AND modality='text' AND dimension=1024",(profile,)).fetchone()
                if row is None:raise ValueError('Profil texte inconnu.')
                conn.execute('UPDATE rag.km_embedding_settings SET active_text_profile=%s WHERE singleton',(profile,))
        return {'active_profile':profile}

    def rename_profile(self,profile,label):
        label=label.strip()
        if not 1<=len(label)<=80:raise ValueError('Le nom doit contenir 1 à 80 caractères.')
        with psycopg.connect(self.dsn,autocommit=True,row_factory=dict_row,connect_timeout=5) as conn:
            with conn.transaction():
                lock(conn)
                if not conn.execute("SELECT 1 FROM rag.embedding_profiles WHERE profile=%s AND modality='text'",(profile,)).fetchone():
                    raise ValueError('Profil inconnu.')
                conn.execute('INSERT INTO rag.km_profile_labels(profile,label) VALUES(%s,%s) ON CONFLICT(profile) DO UPDATE SET label=EXCLUDED.label',(profile,label))
        return {'profile':profile,'label':label}

    def profile_documents(self,profile,limit=100,offset=0):
        with psycopg.connect(self.dsn,autocommit=True,row_factory=dict_row,connect_timeout=5) as conn:
            total=conn.execute('SELECT count(*) AS n FROM rag.km_profile_documents WHERE profile=%s',(profile,)).fetchone()['n']
            rows=conn.execute("""
                SELECT d.document_id,d.filepath,d.published_at,d.metadata,count(e.chunk_id) AS chunks
                FROM rag.km_profile_documents d
                LEFT JOIN rag.km_profile_units u ON u.profile=d.profile AND u.document_id=d.document_id AND u.kind='chunk'
                LEFT JOIN rag.km_text_embeddings e ON e.profile=u.profile AND e.chunk_id=u.unit_id
                WHERE d.profile=%s GROUP BY d.profile,d.document_id,d.filepath,d.published_at,d.metadata
                ORDER BY d.filepath,d.document_id LIMIT %s OFFSET %s""",(profile,limit,offset)).fetchall()
        return {'items':rows,'profile':profile,'total':total,'limit':limit,'offset':offset}
    def embedding_provenance(self,profile,document_id,limit=100,offset=0):
        with psycopg.connect(self.dsn,autocommit=True,row_factory=dict_row,connect_timeout=5) as conn:
            with conn.transaction():
                conn.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
                header=conn.execute("SELECT * FROM rag.embedding_profiles WHERE profile=%s AND modality='text'",(profile,)).fetchone()
                doc=conn.execute('SELECT * FROM rag.km_profile_documents WHERE profile=%s AND document_id=%s',(profile,document_id)).fetchone()
                if not header or not doc:return None
                total=conn.execute("""SELECT count(*) AS n FROM rag.km_text_embeddings e JOIN rag.km_profile_units u
                    ON u.profile=e.profile AND u.unit_id=e.chunk_id WHERE e.profile=%s AND u.document_id=%s""",(profile,document_id)).fetchone()['n']
                chunks=conn.execute("""SELECT e.chunk_id,e.input_hash,e.indexed_at,
                    u.record->>'upstream_id' AS upstream_chunk_id,
                    u.record->>'token_count' AS token_count,u.record->>'tokenizer_id' AS tokenizer_id,
                    u.record->>'transformation_id' AS transformation_id
                    FROM rag.km_text_embeddings e JOIN rag.km_profile_units u ON u.profile=e.profile AND u.unit_id=e.chunk_id
                    WHERE e.profile=%s AND u.document_id=%s ORDER BY e.chunk_id LIMIT %s OFFSET %s""",(profile,document_id,limit,offset)).fetchall()
        return {'profile':profile,'encoder':header,'document':doc,
                'chunks':{'items':chunks,'total':total,'limit':limit,'offset':offset}}

    def deletion_preview(self,profile,scope,document_id=None,chunk_id=None):
        from km.embedding_management import plan
        with psycopg.connect(self.dsn,autocommit=True,row_factory=dict_row,connect_timeout=5) as conn:
            with conn.transaction():
                conn.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
                return plan(conn,profile,scope,document_id,chunk_id)

    def delete_embeddings(self,profile,scope,expected,confirmation,document_id=None,chunk_id=None):
        from km.embedding_management import remove
        with psycopg.connect(self.dsn,autocommit=True,row_factory=dict_row,connect_timeout=5) as conn:
            with conn.transaction():
                return remove(conn,profile,scope,expected,confirmation,document_id,chunk_id)

    def deletion_history(self,profile,limit=50,offset=0):
        with psycopg.connect(self.dsn,autocommit=True,row_factory=dict_row,connect_timeout=5) as conn:
            return {'items':conn.execute("""SELECT id,profile,scope,target,encoder_fingerprint,counts,created_at
                FROM rag.km_embedding_deletions WHERE profile=%s ORDER BY created_at DESC,id LIMIT %s OFFSET %s""",(profile,limit,offset)).fetchall(),
                'profile':profile,'limit':limit,'offset':offset}
    def published_inputs(self, document_ids, profile=None):
        # Read committed publications only; no encoder/model or inference needed.
        result={}
        if not document_ids:return result
        with psycopg.connect(self.dsn,autocommit=True,row_factory=dict_row,connect_timeout=5) as conn:
            profile=profile or active(conn)
            for row in conn.execute("""
                SELECT d.document_id,d.filepath,d.metadata->>'pdfparse_cache_digest' AS cache_digest,e.chunk_id,e.input_hash
                FROM rag.km_profile_documents d JOIN rag.km_profile_units u USING(profile,document_id)
                JOIN rag.km_text_embeddings e ON e.profile=u.profile AND e.chunk_id=u.unit_id
                WHERE d.document_id=ANY(%s) AND d.profile=%s
                """,(list(set(document_ids)),profile)):
                result.setdefault((row['document_id'],row['filepath']),{'inputs':{},'cache_digest':row['cache_digest']})['inputs'][row['chunk_id']]=row['input_hash']
        return result

    def schema_ready(self):
        with psycopg.connect(self.dsn,autocommit=True,connect_timeout=5) as conn:
            required = [
                'rag.km_chunk_indexing', 'rag.km_text_embeddings', 'rag.km_embedding_settings',
                'rag.km_embedding_deletions', 'rag.km_corpus_syncs', 'rag.km_corpus_files',
                'rag.km_corpus_caches', 'rag.km_cache_chunks', 'rag.km_profile_cache_choices',
                'rag.km_chunk_job_files', 'rag.embedding_profiles',
                'rag.km_profile_documents', 'rag.km_profile_units',
            ]
            return conn.execute(
                "SELECT bool_and(to_regclass(name) IS NOT NULL) "
                "AND to_regproc('rag.rag_search_profile') IS NOT NULL "
                "FROM unnest(%s::text[]) name",
                (required,),
            ).fetchone()[0]

    def recover_interrupted(self):
        # Keep the technical jobs API available so schema diagnostics remain readable.
        try:
            if not self.schema_ready():return False
            with psycopg.connect(self.dsn, autocommit=True, row_factory=dict_row, connect_timeout=5) as conn:
                if conn.execute("SELECT pg_try_advisory_lock(hashtextextended('km-embedding-writer',0)) AS ok").fetchone()['ok']:
                    conn.execute("UPDATE rag.km_chunk_indexing SET status='interrupted',last_error='Writer interrupted',updated_at=now() WHERE status='encoding'")
            return True
        except psycopg.Error:
            return False



def reset_text_profile(dsn):
    raise RuntimeError('Reset global désactivé : gérer et sélectionner les profils dans Indexation ; aucune donnée supprimée.')

if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['reset-text-profile'])
    parser.add_argument('--confirm', required=True, choices=['RESET-TEXT-VECTORS'])
    parser.parse_args()
    reset_text_profile(os.environ['KB_DATABASE_URL'])
