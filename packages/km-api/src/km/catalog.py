"""SQL catalogue of read-only filesystem observations, refreshed explicitly."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path, PurePosixPath
import re
import uuid

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from . import sql_client
from .inventory import Inventory, cache_entries, confined, read_bytes, relative_path, metadata_digest
import json
from ._service_logging import submit_with_context


class CorpusCatalog:
    def __init__(self, dsn, root, upstream='/data'):
        self.dsn, self.root = dsn, Path(root)
        self.upstream=upstream
        self.scope = str(self.root.absolute())
        self.lock_name = 'km-corpus-sync:' + self.scope
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='km-corpus-sync')

    def connection(self):
        return psycopg.connect(self.dsn, connect_timeout=5, row_factory=dict_row)

    def ready(self):
        with self.connection() as conn:
            return conn.execute(sql_client.CATALOG_READY).fetchone()['ok']

    def require_ready(self):
        if not self.ready():
            raise RuntimeError('Catalogue SQL incomplet. Vérifier l’initialisation du volume PostgreSQL.')

    def recover(self):
        if not self.ready():
            return
        with self.connection() as conn:
            if conn.execute(sql_client.CORPUS_LOCK,
                            (self.lock_name,)).fetchone()['ok']:
                conn.execute(sql_client.INTERRUPT_RECOVERED_SYNC, (self.scope,))

    def status(self):
        self.require_ready()
        with self.connection() as conn:
            row = conn.execute(sql_client.LATEST_CORPUS_SYNC, (self.scope,)).fetchone()
        return row or {'status': 'never', 'pdf_count': 0, 'directories': 0, 'cache_count': 0}

    def status_by_id(self, ident):
        self.require_ready()
        with self.connection() as conn:
            return conn.execute(
                sql_client.CORPUS_SYNC_BY_ID,
                (self.scope, ident),
            ).fetchone()

    def start(self, *, allow_empty=False):
        self.require_ready()
        conn = self.connection()
        try:
            conn.autocommit = True
            if not conn.execute(sql_client.CORPUS_LOCK,
                                (self.lock_name,)).fetchone()['ok']:
                raise RuntimeError('Une synchronisation du corpus est déjà en cours.')
            conn.execute(sql_client.INTERRUPT_CORPUS_SYNC, (self.scope,))
            ident = uuid.uuid4().hex
            row = conn.execute(sql_client.INSERT_CORPUS_SYNC, (ident,self.scope)).fetchone()
            submit_with_context(self.executor, self._run, conn, ident, allow_empty=allow_empty)
            return row
        except BaseException:
            conn.close()
            raise

    def _caches(self, relative):
        pdf = confined(self.root, relative)
        result=[]
        for entry, _ in cache_entries(pdf, PurePosixPath(self.upstream) / relative):
            manifest, error, state = None, None, 'observed'
            try:
                manifests=[entry/n for n in ('manifest.json','manisfest.json') if (entry/n).exists()]
                if len(manifests)!=1:
                    raise ValueError('Manifeste absent ou ambigu.')
                manifest=json.loads(read_bytes(manifests[0]).decode('utf-8-sig'))
                if not isinstance(manifest,dict) or manifest.get('format')!='pdfparse.result_cache' or manifest.get('schema_version')!=2:
                    raise ValueError('Format du manifeste inconnu.')
            except OSError as exc:
                state,error='unreadable',type(exc).__name__
            except (ValueError,UnicodeError) as exc:
                state,error='invalid_manifest',str(exc)[:500]
            result.append((entry.name, f'{relative}.pdfparsecache/{entry.name}', state, manifest, error))
        return result

    def _run(self,conn,ident, *, allow_empty=False):
        inventory=Inventory(self.root,self.upstream)
        count=caches=0
        detail={'chunks_count':0,'valid_caches':0,'invalid_caches':0}
        from .chunk_catalog import refresh_cache
        try:
            conn.autocommit=False
            root_before = self.root.stat()
            for source in inventory.iter_documents(include_metadata=True):
                row=dict(source)
                metadata=row.pop('_metadata',None)
                entries=self._caches(row['path'])
                row['cache_count']=len(entries)
                row['metadata_digest'] = metadata_digest(metadata)
                row['status']='cached' if entries else 'idle'
                conn.execute(sql_client.UPSERT_CORPUS_FILE,
                             (self.scope,row['id'],row['path'],Jsonb(row),Jsonb(metadata),ident))
                for key,path,state,manifest,error in entries:
                    conn.execute(sql_client.UPSERT_CORPUS_CACHE,
                                 (self.scope,row['id'],key,path,state,Jsonb(manifest),error,ident))
                    chunks,valid=refresh_cache(conn,self.scope,self.root,self.upstream,row,key,metadata)
                    detail['chunks_count']+=chunks
                    detail['valid_caches' if valid else 'invalid_caches']+=1
                count+=1
                caches+=len(entries)
                self._progress(conn,ident,inventory,count,caches,detail)
                conn.commit()
            root_after = self.root.stat()
            if (root_before.st_dev, root_before.st_ino) != (root_after.st_dev, root_after.st_ino):
                raise ValueError('Le montage du corpus a changé pendant la synchronisation.')
            if count == 0 and not allow_empty and conn.execute(sql_client.COUNT_PRESENT_CORPUS_FILES,
                                        (self.scope,)).fetchone()['n']:
                raise ValueError('Aucun PDF observé : vérifier DATA_DIR. Pour un corpus volontairement vide, relancer avec allow_empty=true.')
            # Absences become visible only after a successful full traversal.
            for table in ('km_corpus_files','km_corpus_caches'):
                conn.execute(sql_client.mark_corpus_absent(table),
                             (self.scope,ident))
            self._progress(conn,ident,inventory,count,caches,detail)
            conn.execute(sql_client.FINISH_CORPUS_SYNC,(ident,))
            conn.commit()
        except BaseException as exc:
            conn.rollback()
            conn.execute(sql_client.FAIL_CORPUS_SYNC,
                         ((type(exc).__name__+': '+str(exc))[:1000],ident))
            conn.commit()
        finally:
            conn.close()

    @staticmethod
    def _progress(conn,ident,inventory,count,caches,detail):
        conn.execute(sql_client.SYNC_PROGRESS,
                     (inventory.diagnostics.get('directories',0),count,caches,detail['chunks_count'],detail['valid_caches'],detail['invalid_caches'],ident))

    def page(self, folder='', query='', limit=100, offset=0):
        self.require_ready()
        if not 1<=limit<=200 or offset<0:
            raise ValueError('Pagination invalide.')
        prefix=relative_path(folder).as_posix().rstrip('/')+'/' if folder else ''
        with self.connection() as conn:
            params=(self.scope,prefix,query)
            total=conn.execute(sql_client.COUNT_CORPUS_MATCHES,params).fetchone()['n']
            rows=conn.execute(sql_client.LIST_CORPUS_MATCHES,
                              (*params,limit,offset)).fetchall()
            missing=conn.execute(sql_client.COUNT_ABSENT_CORPUS_FILES,
                                 (self.scope,)).fetchone()['n']
        state=self.status()
        return {'items':[r['summary'] for r in rows],'total':total,'limit':limit,'offset':offset,
                'diagnostics':{'provider':'postgres-catalog','pdf_count':total,'directories':state.get('directories',0),
                               'missing_files':missing,'hint':'Cliquer sur Synchroniser le corpus pour découvrir les fichiers et caches.' if state['status']=='never' else None},
                'sync':state}

    def lookup(self, ids):
        self.require_ready()
        with self.connection() as conn:
            rows=conn.execute(sql_client.CORPUS_FILES_BY_IDS,
                              (self.scope,list(ids))).fetchall()
        return [row['summary'] for row in rows]

    def close(self):
        self.executor.shutdown(wait=True)
