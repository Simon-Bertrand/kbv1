"""Cache-source chunks and global, profile-specific SQL progress. No PDF parsing."""
import hashlib
from psycopg.types.json import Jsonb
from . import sql_client
from .cache import CacheReader
from .inventory import bind_bundle
from .cache_contract import validate


def refresh_cache(conn,scope,root,upstream,file,key,metadata):
    """Validate one explicit cache and retain its exact exported records in SQL."""
    try:
        with conn.transaction():
            bundle=CacheReader(root,upstream).load(file['path'],cache_key=key)
            if bundle is None:raise ValueError('Cache incompatible avec le PDF actuel.')
            bundle=bind_bundle(bundle,metadata)
            validate(bundle)
            records=bundle['chunks']
            if len({r['id'] for r in records})!=len(records):raise ValueError('Identités de chunks dupliquées.')
            digest=bundle['metadata']['pdfparse_cache_digest']
            def rows():
                for ordinal,record in enumerate(records):
                    text=record.get('embedding_text')
                    if not isinstance(text,str) or not text.strip():raise ValueError('Chunk sans embedding_text.')
                    pages=record.get('body_pages') or record.get('pages') or []
                    if not pages:
                        pages=sorted({p.get('page_no') for p in record.get('locations',[]) if type(p.get('page_no')) is int and p['page_no']>0})
                    if not isinstance(pages,list) or any(type(page) is not int or page<1 for page in pages):raise ValueError('Localisations de pages invalides.')
                    yield (scope,file['id'],key,record['id'],bundle['document_id'],record.get('upstream_id',record['id']),
                           digest,hashlib.sha256(text.encode()).hexdigest(),ordinal,Jsonb(pages),Jsonb(record))
            with conn.cursor() as cursor:
                cursor.executemany(sql_client.UPSERT_CACHED_CHUNK,rows())
            # Stale records are retained but excluded by this digest; no cache files are touched.
            conn.execute(sql_client.MARK_CACHE_VALID,
                         (digest,bundle['document_id'],len(records),scope,file['id'],key))
        return len(records),True
    except (ValueError,RuntimeError,OSError,KeyError,TypeError) as exc:
        conn.execute(sql_client.MARK_CACHE_INVALID,
                     ((type(exc).__name__+': '+str(exc))[:700],scope,file['id'],key))
        return 0,False


# A cache choice is explicit per file and profile. A unique valid textual cache
# can be used without a choice; an ambiguous or invalid explicit choice cannot.


class ChunkCatalog:
    def __init__(self,catalog):self.catalog=catalog

    def context(self,conn,profile,query=''):
        self.catalog.require_ready()
        if not profile:raise ValueError('Choisir un profil d’embedding.')
        row=conn.execute(sql_client.TEXT_PROFILE_BY_NAME,(profile,)).fetchone()
        if not row:raise ValueError('Profil texte inconnu.')
        return {'root':self.catalog.scope,'profile':profile,'fingerprint':row['encoder_fingerprint'],'query':query}

    def chunks(self,profile,query='',state='',limit=100,offset=0):
        if state not in ('','missing','pending','computed','published','failed','interrupted','encoding'):raise ValueError('État de chunk invalide.')
        if not 1<=limit<=200 or offset<0:raise ValueError('Pagination invalide.')
        with self.catalog.connection() as conn:
            conn.execute(sql_client.READ_ONLY_SNAPSHOT)
            p=self.context(conn,profile,query)
            summary={name:0 for name in ('pending','computed','published','failed','interrupted','encoding')}
            for row in conn.execute(sql_client.CHUNK_STATE_COUNTS,p):summary[row['state']]=row['n']
            p.update(state=state,limit=limit,offset=offset)
            total=sum(summary.values()) if not state else sum(summary[s] for s in ('pending','failed','interrupted')) if state=='missing' else summary[state]
            rows=conn.execute(sql_client.list_chunk_states(state),p).fetchall()
            pdfs=conn.execute(sql_client.COUNT_BLOCKED_FILES,p).fetchone()
        return {'items':rows,'total':total,'summary':summary,'pdfs':pdfs,'profile':profile,'limit':limit,'offset':offset}

    def files(self,profile,query='',state='',limit=100,offset=0):
        if state not in ('','missing','pending','partial','computed','published','blocked','ambiguous','no_cache'):raise ValueError('État de PDF invalide.')
        if not 1<=limit<=200 or offset<0:raise ValueError('Pagination invalide.')
        with self.catalog.connection() as conn:
            conn.execute(sql_client.READ_ONLY_SNAPSHOT)
            p=self.context(conn,profile,query)
            summary={r['state']:r['n'] for r in conn.execute(sql_client.FILE_STATE_COUNTS,p)}
            p.update(state=state,limit=limit,offset=offset)
            rows=conn.execute(sql_client.list_file_states(state),p).fetchall()
            for row in rows:
                row['caches']=conn.execute(sql_client.LIST_FILE_CACHES,
                    (self.catalog.scope,row['file_id'])).fetchall()
        total=sum(summary.values()) if not state else sum(summary.get(s,0) for s in ('pending','partial','computed')) if state=='missing' else summary.get(state,0)
        return {'items':rows,'summary':summary,'total':total,'profile':profile,'limit':limit,'offset':offset}

    def choose(self,profile,file_id,key):
        with self.catalog.connection() as conn:
            self.context(conn,profile)
            row=conn.execute(sql_client.VALID_CACHE_CHOICE,
                (self.catalog.scope,file_id,key)).fetchone()
            if not row:raise ValueError('Cache textuel valide absent ; synchroniser le corpus.')
            conn.execute(sql_client.UPSERT_CACHE_CHOICE,
                (self.catalog.scope,file_id,profile,key))
        return {'file_id':file_id,'cache_key':key,'profile':profile}

    def cache_info(self,file_id,key):
        self.catalog.require_ready()
        with self.catalog.connection() as conn:
            row=conn.execute(sql_client.CACHE_INFO,
                (self.catalog.scope,file_id,key)).fetchone()
        if not row:raise ValueError('Cache absent du catalogue.')
        return row

    def plan(self,profile,limit,query='',excluded=(),cutoff=None):
        with self.catalog.connection() as conn:
            conn.execute(sql_client.READ_ONLY_SNAPSHOT)
            p=self.context(conn,profile,query)
            p.update(limit=limit,excluded=list(excluded),cutoff=cutoff)
            rows=conn.execute(sql_client.PLAN_PENDING_CHUNKS,p).fetchall()
            ready=conn.execute(sql_client.PLAN_COMPUTED_FILES,p).fetchall()
        return rows,ready

    def enqueue_all(self,job_id,profile,query,cutoff):
        with self.catalog.connection() as conn:
            p=self.context(conn,profile,query);p.update(job=job_id,cutoff=cutoff)
            conn.execute(sql_client.ENQUEUE_ALL_JOB_FILES,p)
            return conn.execute(sql_client.COUNT_JOB_WORK,(job_id,)).fetchone()

    def enqueue_plan(self,job_id,profile,rows,ready):
        groups={}
        for row in rows+ready:
            group=groups.setdefault(row['file_id'],{'base':row,'ids':[]})
            if row.get('chunk_id'):group['ids'].append(row['chunk_id'])
        with self.catalog.connection() as conn:
            with conn.cursor() as cursor:
                cursor.executemany(sql_client.ENQUEUE_SELECTED_JOB_FILE,
                    ((job_id,self.catalog.scope,profile,file_id,g['base']['file_path'],g['base']['cache_key'],g['base']['cache_digest'],len(g['ids']),Jsonb(g['ids'])) for file_id,g in groups.items()))

    def next_file(self,job_id):
        with self.catalog.connection() as conn:
            row=conn.execute(sql_client.NEXT_JOB_FILE,
                             (job_id,self.catalog.scope)).fetchone()
            if row:conn.execute(sql_client.START_JOB_FILE,(job_id,row['file_id']))
        return row

    def finish_file(self,job_id,file_id,status,computed,published,error=None):
        with self.catalog.connection() as conn:
            conn.execute(sql_client.FINISH_JOB_FILE,
                         (status,computed,published,error,job_id,file_id,self.catalog.scope))

    def job_files(self,job_id,limit=100,offset=0):
        with self.catalog.connection() as conn:
            total=conn.execute(sql_client.COUNT_FAILED_JOB_FILES,(job_id,self.catalog.scope)).fetchone()['n']
            rows=conn.execute(sql_client.LIST_FAILED_JOB_FILES,(job_id,self.catalog.scope,limit,offset)).fetchall()
        return {'items':rows,'total':total,'limit':limit,'offset':offset}
