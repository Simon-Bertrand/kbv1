"""Sequential existing-cache -> E5 -> PostgreSQL jobs; never starts PdfParse."""
from contextlib import asynccontextmanager
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import fcntl
import hmac
import json
import os
import re
from typing import Literal
from pathlib import Path
import sqlite3
import threading
import uuid
import psycopg
from fastapi.responses import JSONResponse
from km.diagnostics import database_diagnostic

from fastapi import Depends, FastAPI, HTTPException, Header, Query
from fastapi import Request as HTTPRequest
from pydantic import BaseModel, Field
from km.pdfparse_client import PdfParseCacheClient
from .indexing import Publisher
from km.encoder_client import EncoderClient, EncoderUnavailable
from km._service_logging import RequestIdMiddleware, accept_upstream, submit_with_context

def now():
    return datetime.now(timezone.utc).isoformat()


def job_summary(job):
    row={key:value for key,value in job.items() if key not in ('document_ids','document_paths','cache_selections','results','document_results','chunk_plan','ready_plan')}
    for key in ('error','error_path'):
        if isinstance(row.get(key),str):row[key]=row[key][:2000]
    failures=[r for r in job.get('document_results',[]) if r.get('status')=='failed']
    row['document_results']=[{key:(value[:2000] if isinstance(value,str) else value) for key,value in r.items()} for r in failures[:20]]
    row['results_truncated']=len(failures)>20
    return row

class Request(BaseModel):
    document_ids: list[str] = Field(min_length=1, max_length=10000)
    use_cache: Literal[True] = True
    cache_only: Literal[True] = True
    cache_selections: dict[str,str] = Field(default_factory=dict,max_length=10000)
    profile: str | None = Field(default=None,max_length=128)

class Jobs:
    def __init__(self, root, parser, publisher):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.lock = (self.root / 'writer.lock').open('a')
        fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if hasattr(publisher, 'recover_interrupted'):
            publisher.recover_interrupted()
        self.path = self.root / 'jobs.sqlite3'
        self.parser, self.publisher = parser, publisher
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='km-index')
        self.mutex = threading.RLock()
        self.stop_event = threading.Event()
        with self.connection() as conn:
            conn.execute('CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, payload TEXT NOT NULL)')
            if 'summary' not in {row[1] for row in conn.execute('PRAGMA table_info(jobs)')}:
                conn.execute('ALTER TABLE jobs ADD COLUMN summary TEXT')
            conn.execute("CREATE INDEX IF NOT EXISTS jobs_submission_key ON jobs(json_extract(payload,'$.idempotency_key'))")
            rows = conn.execute("SELECT id,payload FROM jobs WHERE json_extract(payload,'$.status') IN ('running','queued')").fetchall()
            for ident, raw in rows:
                payload = json.loads(raw)
                if payload['status'] in ('running','queued'):
                    payload.update(status='interrupted', finished_at=now(), stage='interrupted',
                                   error='Service restarted; relaunch explicitly to resume')
                    conn.execute('UPDATE jobs SET payload=?,summary=? WHERE id=?',
                                 (json.dumps(payload),json.dumps(job_summary(payload)),ident))

    def connection(self):
        return sqlite3.connect(self.path, timeout=5)

    def list(self):
        with self.connection() as conn:
            return [json.loads(r[0]) for r in conn.execute('SELECT payload FROM jobs ORDER BY rowid DESC LIMIT 100')]

    def summaries(self):
        with self.connection() as conn:
            result=[]
            for ident,raw in conn.execute('SELECT id,summary FROM jobs ORDER BY rowid DESC LIMIT 20').fetchall():
                if raw is None:
                    payload=conn.execute('SELECT payload FROM jobs WHERE id=?',(ident,)).fetchone()[0]
                    raw=json.dumps(job_summary(json.loads(payload)))
                    conn.execute('UPDATE jobs SET summary=? WHERE id=?',(raw,ident))
                row=json.loads(raw)
                if row.get('unit')=='chunk' and row.get('status') in ('running','queued'):
                    row['stop_requested']=self.stop_event.is_set()
                result.append(row)
            return result

    def find_by_submission_key(self, key):
        with self.connection() as conn:
            row = conn.execute(
                "SELECT payload FROM jobs WHERE json_extract(payload,'$.idempotency_key')=? ORDER BY rowid DESC LIMIT 1",
                (key,),
            ).fetchone()
            return json.loads(row[0]) if row else None

    def get(self, ident):
        with self.connection() as conn:
            row = conn.execute('SELECT payload FROM jobs WHERE id=?', (ident,)).fetchone()
            if not row:
                return None
            return json.loads(row[0])

    def save(self, job):
        if job.get('unit')=='chunk':job['stop_requested']=self.stop_event.is_set()
        with self.connection() as conn:
            conn.execute('INSERT INTO jobs(id,payload,summary) VALUES (?,?,?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload,summary=excluded.summary',
                         (job['id'], json.dumps(job, allow_nan=False),json.dumps(job_summary(job),allow_nan=False)))

    def submit(self, ids, use_cache=True, cache_only=True, profile=None, cache_selections=None):
        if hasattr(self.publisher,'schema_ready') and not self.publisher.schema_ready():
            raise RuntimeError('Schéma KM incomplet. Vérifier l’initialisation du volume PostgreSQL avant d’indexer.')
        if use_cache is not True or cache_only is not True:
            raise ValueError('Seuls les caches existants sont autorisés ; PdfParse ne sera pas lancé.')
        cache_selections=cache_selections or {}
        if not set(cache_selections)<=set(ids) or any(not re.fullmatch('[a-f0-9]{64}',key) for key in cache_selections.values()):
            raise ValueError('Choix de cache invalide ou associé à un autre PDF.')
        if len(set(ids)) != len(ids):
            raise ValueError('Duplicate documents')
        # Refresh authoritative PdfParse inventory before accepting work.
        catalog=getattr(self,'catalog',None)
        with self.mutex:
            if any(r['status'] in ('running','queued') for r in self.list()):
                raise RuntimeError('A KM indexing job is already active')
            if catalog is not None and catalog.status()['status'] == 'running':
                raise RuntimeError('Synchronisation en cours ; attendre sa fin.')
            catalogue = catalog.lookup(ids) if catalog else self.parser.documents()
            if catalog:
                self.parser.entries.update({row['id']:row for row in catalogue})
            available = {r['id'] for r in catalogue}
            if not set(ids) <= available:
                raise ValueError('Unknown PdfParse document')
            assigned=self.publisher.ensure_current_profile(profile) if hasattr(self.publisher,'ensure_current_profile') else None
            job = {'id': uuid.uuid4().hex, 'document_ids': ids, 'status': 'queued', 'stage': 'queued',
                   'created_at': now(), 'finished_at': None, 'completed': 0, 'processed':0, 'failed_count':0, 'document_results':[], 'document_paths':{r['id']:r.get('path',r['id']) for r in catalogue if r['id'] in ids}, 'total': len(ids), 'results': [], 'use_cache': use_cache, 'cache_only':True,'cache_selections':cache_selections,'profile':assigned['profile'] if assigned else None}
            self.save(job)
            submit_with_context(self.executor, self.execute, job)
            return dict(job)

    def execute(self, job):
        job.update(status='running', started_at=now())
        self.save(job)
        def progress(stage):
            if isinstance(stage, dict):
                if stage.get('profile'):job['profile']=stage['profile']
                job['chunk_progress'] = stage
                job['stage'] = stage['stage']
            else:
                job['stage'] = stage
            self.save(job)
        job.setdefault('document_results',[])
        job.setdefault('failed_count',0)
        job.setdefault('processed',0)
        def failure(exc):
            item={'error_type':type(exc).__name__}
            if isinstance(exc,PermissionError):
                item['error_path']=str(exc.filename) if exc.filename else None
                detail='Accès refusé au cache, corpus ou état technique. Consulter python3 -m scripts.ops.manage doctor et le chemin indiqué.'
            elif isinstance(exc,psycopg.Error):
                detail=database_diagnostic(exc)['detail']
            else:
                detail=str(exc) if type(exc) in (RuntimeError,ValueError) else type(exc).__name__
            item['error']=detail[:500]
            return item
        try:
            for ident in job['document_ids']:
                job['current_document_id']=ident
                job.pop('chunk_progress',None)
                outcome={'document_id':ident,'path':job.get('document_paths',{}).get(ident,ident)}
                try:
                    progress('cache')
                    bundle=self.parser.prepare(ident,progress,use_cache=True,cache_only=True,
                        **({'cache_key':job['cache_selections'][ident]} if ident in job.get('cache_selections',{}) else {}))
                    bundle={**bundle,'metadata':{**bundle.get('metadata',{}),'km_indexing_job_id':job['id']}}
                    from .chunk_jobs import archive_source
                    archive_source(bundle)
                    result=self.publisher.publish(bundle,progress,verify_source=lambda:self.parser.verify_source(bundle),
                        **({'profile':job['profile']} if job.get('profile') else {}))
                    job['results'].append(result)
                    job['completed']+=1
                    outcome.update(status='succeeded',chunks=result.get('chunks',0))
                except Exception as exc:
                    outcome.update(status='failed',stage=job['stage'],**failure(exc))
                    job['failed_count']+=1
                outcome['finished_at']=now()
                job['document_results'].append(outcome)
                job['processed']+=1
                # Persist every outcome before proceeding to the next document.
                self.save(job)
            job.update(status='partial' if job['failed_count'] and job['completed'] else 'failed' if job['failed_count'] else 'succeeded',stage='complete')
            if job['failed_count']:
                first=next(row for row in job['document_results'] if row['status']=='failed')
                job.update(error_type=first['error_type'],error_path=first.get('error_path'),
                    error=first['error'] if job['total']==1 else f"{job['failed_count']} PDF en erreur ; les autres documents ont été traités.")
        except Exception as exc:
            # Technical state failures cannot safely continue; embeddings already saved remain reusable.
            job.update(status='failed',**failure(exc))
        finally:
            job['finished_at']=now()
            self.save(job)

    def close(self):
        self.stop_event.set()
        self.executor.shutdown(wait=True)
        self.parser.close()
        self.publisher.encoder.close()
        self.lock.close()

def create_app(jobs=None, catalog=None):
    @asynccontextmanager
    async def lifespan(app):
        owned = jobs is None
        if owned:
            corpus = Path(os.environ.get('KB_DATA_DIR', '/corpus'))
            versions = os.environ.get('SOURCE_VERSIONS_DIR') or corpus / '_versions'
            encoder = EncoderClient(token=os.environ.get('KM_ENCODER_TOKEN') or os.environ.get('KM_INDEXER_TOKEN'))
            parser = PdfParseCacheClient(corpus,
                              upstream_root=os.environ.get('KB_UPSTREAM_ROOT','/data'),
                              source_versions=versions)
            app.state.jobs = Jobs(os.environ.get('KM_STATE_DIR','/state'), parser,
                                  Publisher(os.environ['KB_DATABASE_URL'], encoder, km=os.environ.get('KB_KM_SCHEMA')=='1'))
        if app.state.catalog is None and owned:
            from km.catalog import CorpusCatalog
            app.state.catalog=CorpusCatalog(app.state.jobs.publisher.dsn,app.state.jobs.parser.root,app.state.jobs.parser.upstream_root)
        if app.state.catalog is not None:
            try:app.state.catalog.recover()
            except psycopg.Error:pass
            app.state.jobs.catalog=app.state.catalog
        try:
            yield
        finally:
            if app.state.catalog is not None:app.state.catalog.close()
        if owned:
            app.state.jobs.close()
    app = FastAPI(title='KM Indexation', lifespan=lifespan, docs_url=None, redoc_url=None)
    app.state.jobs = jobs
    app.state.catalog = catalog

    @app.middleware('http')
    async def indexer_auth_middleware(request: HTTPRequest, call_next):
        if request.url.path == '/health':
            return await call_next(request)

        env = (os.environ.get('KB_ENV') or os.environ.get('ENVIRONMENT') or 'dev').strip().lower()
        is_prod = env in ('production', 'prod')
        is_dev = env in ('dev', 'development', 'test', 'testing')

        if not is_prod and not is_dev:
            return JSONResponse({'detail': f'Invalid environment configuration: {env}'}, status_code=500)

        writer_token = (os.environ.get('KM_INDEXER_TOKEN') or '').strip()
        admin_token = (
            os.environ.get('KM_INDEXER_ADMIN_TOKEN')
            or os.environ.get('KM_ADMIN_TOKEN')
            or os.environ.get('KM_CONSOLE_TOKEN')
            or ''
        ).strip()
        api_token = (os.environ.get('KM_API_TOKEN') or '').strip()

        path = request.url.path.rstrip('/')
        admin_paths = {
            '/profiles/activate',
            '/profiles/rename',
            '/profiles/delete-embeddings',
            '/profiles/delete-preview',
        }
        is_admin_route = path in admin_paths

        if is_prod:
            if not writer_token or len(writer_token) < 16:
                return JSONResponse({'detail': 'KM_INDEXER_TOKEN must be configured with >= 16 characters in production'}, status_code=500)
            if admin_token and len(admin_token) < 16:
                return JSONResponse({'detail': 'KM_INDEXER_ADMIN_TOKEN must be configured with >= 16 characters in production'}, status_code=500)
            if api_token and len(api_token) < 16:
                return JSONResponse({'detail': 'KM_API_TOKEN must be configured with >= 16 characters in production'}, status_code=500)
            if is_admin_route and not admin_token:
                return JSONResponse({'detail': 'KM_INDEXER_ADMIN_TOKEN must be configured with >= 16 characters in production for admin operations'}, status_code=500)
        else:
            if not writer_token and not admin_token and not api_token:
                return await call_next(request)

        auth = request.headers.get('authorization', '')
        if not auth.startswith('Bearer '):
            return JSONResponse({'detail': 'Unauthorized: missing or malformed Bearer token'}, status_code=401)

        token = auth[7:].strip()
        is_admin = bool(admin_token and (len(admin_token) >= 16 if is_prod else len(admin_token) > 0) and hmac.compare_digest(token, admin_token))
        is_writer = bool(writer_token and (len(writer_token) >= 16 if is_prod else len(writer_token) > 0) and hmac.compare_digest(token, writer_token))
        is_reader = bool(api_token and (len(api_token) >= 16 if is_prod else len(api_token) > 0) and hmac.compare_digest(token, api_token))

        if is_admin or is_writer or is_reader:
            accept_upstream(request.headers)

        if is_admin_route:
            if not is_admin:
                if is_writer or is_reader:
                    return JSONResponse({'detail': 'Forbidden: admin role required'}, status_code=403)
                return JSONResponse({'detail': 'Unauthorized: invalid bearer token'}, status_code=401)
            return await call_next(request)

        writer_paths = {
            '/catalog/sync',
            '/catalog/cache-choice',
            '/chunk-jobs',
            '/chunk-jobs/stop',
            '/jobs',
            '/profiles/register',
        }
        is_writer_route = request.method in {'POST', 'DELETE', 'PUT', 'PATCH'} and path in writer_paths

        if is_writer_route:
            if not (is_admin or is_writer):
                if is_reader:
                    return JSONResponse({'detail': 'Forbidden: writer role required'}, status_code=403)
                return JSONResponse({'detail': 'Unauthorized: invalid bearer token'}, status_code=401)
            return await call_next(request)

        # Reader route
        if not (is_admin or is_writer or is_reader):
            return JSONResponse({'detail': 'Unauthorized: invalid bearer token'}, status_code=401)

        return await call_next(request)

    @app.get('/health')
    def health():
        try:
            if hasattr(app.state.jobs.publisher,'schema_ready') and not app.state.jobs.publisher.schema_ready():
                return JSONResponse({'code':'schema_incomplete','detail':'Schéma KM incomplet. Vérifier l’initialisation du volume PostgreSQL.'},status_code=503)
        except psycopg.Error as exc:
            return JSONResponse(database_diagnostic(exc),status_code=503)
        return {
            'status': 'ok',
            'service': 'km-indexer',
            'functional_contract': 'km-indexer-v1',
            'workflow': 'cache-e5-postgres',
            'cache_only': True,
            'pdfparse_processing': False,
            'automatic_indexing': False,
            'capabilities': {
                'catalog': True,
                'chunk_indexing': True,
            },
        }

    def catalog_rows(folder='',query='',limit=100,offset=0):
        if app.state.catalog is None:
            items=app.state.jobs.parser.documents(folder)
            return {'items':items,'total':len(items),'limit':limit,'offset':offset,
                    'diagnostics':getattr(app.state.jobs.parser,'diagnostics',{})}
        value=app.state.catalog.page(folder,query,limit,offset)
        app.state.jobs.parser.entries.update({row['id']:row for row in value['items']})
        return value

    @app.get('/documents')
    def documents(folder: str=Query(default='',max_length=2048),query: str=Query(default='',max_length=500),
                  limit: int=Query(default=100,ge=1,le=200),offset: int=Query(default=0,ge=0)):
        try:return catalog_rows(folder,query,limit,offset)
        except (RuntimeError,ValueError) as exc:raise HTTPException(409,str(exc)) from exc

    @app.get('/catalog/status')
    def catalog_status():
        if app.state.catalog is None:raise HTTPException(503,'Catalogue indisponible.')
        try:return app.state.catalog.status()
        except RuntimeError as exc:raise HTTPException(409,str(exc)) from exc

    @app.get('/catalog/sync/{ident}')
    def catalog_sync_status(ident: str):
        if app.state.catalog is None:
            raise HTTPException(503, 'Catalogue indisponible.')
        if not re.fullmatch('[a-f0-9]{32}', ident):
            raise HTTPException(404, 'Synchronisation inconnue.')
        try:
            row = app.state.catalog.status_by_id(ident)
        except RuntimeError as exc:
            raise HTTPException(409, str(exc)) from exc
        if row is None:
            raise HTTPException(404, 'Synchronisation inconnue.')
        return row

    @app.post('/catalog/sync',status_code=202)
    def catalog_sync(allow_empty: bool = False):
        if app.state.catalog is None:raise HTTPException(503,'Catalogue indisponible.')
        try:
            with app.state.jobs.mutex:
                require_idle()
                return app.state.catalog.start(allow_empty=allow_empty)
        except RuntimeError as exc:raise HTTPException(409,str(exc)) from exc

    def active_jobs():
        getter=getattr(app.state.jobs,'summaries',None) or getattr(app.state.jobs,'list',lambda:[])
        return [r for r in getter() if r.get('status') in ('running','queued')]

    def require_idle():
        if active_jobs():raise RuntimeError('Un travail KM est actif ; attendre sa fin ou demander son arrêt.')

    def sources():
        if app.state.catalog is None:raise RuntimeError('Catalogue SQL indisponible.')
        from km.chunk_catalog import ChunkCatalog
        return ChunkCatalog(app.state.catalog)

    @app.get('/catalog/chunks')
    def source_chunks(profile: str=Query(min_length=1,max_length=128),query: str=Query(default='',max_length=500),
                      state: str='',limit: int=Query(default=100,ge=1,le=200),offset: int=Query(default=0,ge=0)):
        try:return sources().chunks(profile,query,state,limit,offset)
        except (ValueError,RuntimeError) as exc:raise HTTPException(409,str(exc)) from exc

    @app.get('/catalog/files')
    def source_files(profile: str=Query(min_length=1,max_length=128),query: str=Query(default='',max_length=500),
                     state: str='',limit: int=Query(default=100,ge=1,le=200),offset: int=Query(default=0,ge=0)):
        try:return sources().files(profile,query,state,limit,offset)
        except (ValueError,RuntimeError) as exc:raise HTTPException(409,str(exc)) from exc

    @app.get('/catalog/cache')
    def source_cache_info(file_id: str=Query(pattern='^[a-f0-9]{24}$'),cache_key: str=Query(pattern='^[a-f0-9]{64}$')):
        try:return sources().cache_info(file_id,cache_key)
        except (ValueError,RuntimeError) as exc:raise HTTPException(409,str(exc)) from exc

    class CacheChoice(BaseModel):
        profile: str=Field(min_length=1,max_length=128)
        file_id: str=Field(pattern='^[a-f0-9]{24}$')
        cache_key: str=Field(pattern='^[a-f0-9]{64}$')

    @app.post('/catalog/cache-choice')
    def choose_source_cache(body:CacheChoice):
        try:
            with app.state.jobs.mutex:
                require_idle()
                if app.state.catalog is None:
                    raise RuntimeError('Catalogue SQL indisponible.')
                if app.state.catalog.status()['status'] == 'running':
                    raise RuntimeError('Synchronisation en cours ; attendre sa fin.')
                return sources().choose(body.profile,body.file_id,body.cache_key)
        except (RuntimeError,ValueError) as exc:raise HTTPException(409,str(exc)) from exc

    class ChunkBatch(BaseModel):
        profile: str=Field(min_length=1,max_length=128)
        limit: int=Field(default=500,ge=1,le=10000)
        all_chunks: bool=False
        query: str=Field(default='',max_length=500)

    @app.post('/chunk-jobs',status_code=202)
    def submit_chunk_batch(body:ChunkBatch, request:HTTPRequest):
        try:
            from .chunk_jobs import submit
            idempotency_key = (request.headers.get('Idempotency-Key') or '').strip() or None
            return job_summary(submit(app.state.jobs,body.profile,body.limit,body.all_chunks,body.query,idempotency_key=idempotency_key))
        except (RuntimeError,ValueError) as exc:raise HTTPException(409,str(exc)) from exc

    class StopBatch(BaseModel):
        job_id: str=Field(pattern='^[a-f0-9]{32}$')

    @app.post('/chunk-jobs/stop')
    def stop_chunk_batch(body:StopBatch):
        jobs = app.state.jobs
        with jobs.mutex:
            job = jobs.get(body.job_id)
            if not job or job.get('unit') != 'chunk' or job['status'] not in ('queued', 'running'):
                raise HTTPException(409,'Ce lot de chunks n’est plus actif.')
            jobs.stop_event.set()
        return {'job_id':body.job_id,'stop_requested':True}

    @app.get('/chunk-jobs/files')
    def chunk_job_files(job_id: str=Query(pattern='^[a-f0-9]{32}$'),limit: int=Query(default=100,ge=1,le=100),offset: int=Query(default=0,ge=0)):
        try:return sources().job_files(job_id,limit,offset)
        except RuntimeError as exc:raise HTTPException(409,str(exc)) from exc

    @app.exception_handler(psycopg.Error)
    async def database_error(request,exc):
        return JSONResponse(database_diagnostic(exc),status_code=503)

    class ProfileChoice(BaseModel):
        profile: str = Field(min_length=1,max_length=128)
    class ProfileName(ProfileChoice):
        label: str = Field(min_length=1,max_length=80)

    class DeleteScope(ProfileChoice):
        scope: Literal['profile','document','chunk']
        document_id: str | None = Field(default=None,max_length=500)
        chunk_id: str | None = Field(default=None,max_length=500)
    class DeleteConfirmation(DeleteScope):
        expected: str = Field(pattern='^[a-f0-9]{64}$')
        confirmation: Literal['SUPPRIMER']

    @app.get('/caches')
    def caches(document_id: str=Query(pattern='^[a-f0-9]{24}$')):
        try:
            if app.state.catalog is not None:
                rows=app.state.catalog.lookup([document_id])
                if not rows:raise ValueError('PDF absent du catalogue ; synchroniser le corpus.')
                app.state.jobs.parser.entries.update({row['id']:row for row in rows})
            return app.state.jobs.parser.cache_choices(document_id)
        except (RuntimeError,ValueError,OSError) as exc:
            raise HTTPException(422,str(exc)) from exc

    @app.get('/profiles/provenance')
    def provenance(profile: str=Query(min_length=1,max_length=128),document_id: str=Query(min_length=1,max_length=500),
                   limit: int=Query(default=100,ge=1,le=100),offset: int=Query(default=0,ge=0,le=100000)):
        value=app.state.jobs.publisher.embedding_provenance(profile,document_id,limit,offset)
        if value is None:raise HTTPException(404,'Publication absente de ce profil.')
        return value

    @app.get('/profiles/deletions')
    def deletions(profile: str=Query(min_length=1,max_length=128),limit: int=Query(default=50,ge=1,le=100),offset: int=Query(default=0,ge=0,le=100000)):
        return app.state.jobs.publisher.deletion_history(profile,limit,offset)

    @app.post('/profiles/delete-preview')
    def delete_preview(body:DeleteScope):
        try:return app.state.jobs.publisher.deletion_preview(**body.model_dump())
        except (RuntimeError,ValueError) as exc:raise HTTPException(409,str(exc)) from exc

    @app.post('/profiles/delete-embeddings')
    def delete_embeddings(body:DeleteConfirmation):
        jobs=app.state.jobs
        with jobs.mutex:
            if any(row['status'] in ('running','queued') for row in jobs.list()):
                raise HTTPException(409,'Une indexation KM est en cours. Attendre sa fin avant de supprimer des embeddings.')
            try:return jobs.publisher.delete_embeddings(**body.model_dump())
            except (RuntimeError,ValueError) as exc:raise HTTPException(409,str(exc)) from exc

    @app.get('/profiles')
    def profiles():
        return app.state.jobs.publisher.profiles()

    @app.get('/profiles/documents')
    def profile_documents(profile: str=Query(min_length=1,max_length=128),
                          limit: int=Query(default=100,ge=1,le=100),offset: int=Query(default=0,ge=0,le=100000)):
        return app.state.jobs.publisher.profile_documents(profile,limit,offset)

    @app.post('/profiles/register')
    def register_profile():
        try:return app.state.jobs.publisher.ensure_current_profile()
        except (EncoderUnavailable, psycopg.OperationalError, psycopg.errors.UndefinedTable) as exc:
            raise HTTPException(503, 'Dépendance temporairement indisponible') from exc
        except (RuntimeError,ValueError) as exc:raise HTTPException(409,str(exc)) from exc

    @app.post('/profiles/activate')
    def activate_profile(body:ProfileChoice):
        try:return app.state.jobs.publisher.activate_profile(body.profile)
        except (RuntimeError,ValueError) as exc:raise HTTPException(409,str(exc)) from exc

    @app.post('/profiles/rename')
    def rename_profile(body:ProfileName):
        try:return app.state.jobs.publisher.rename_profile(body.profile,body.label)
        except (RuntimeError,ValueError) as exc:raise HTTPException(409,str(exc)) from exc

    @app.get('/candidates')
    def candidates(folder: str='',profile: str | None=Query(default=None,max_length=128),
                   query: str=Query(default='',max_length=500),limit: int=Query(default=100,ge=1,le=200),offset: int=Query(default=0,ge=0)):
        return compare_caches(folder,profile,{},query,limit,offset)

    def compare_caches(folder,profile,selections,query='',limit=100,offset=0):
        from .cache_inventory import describe_caches,compare_publications,cache_summary
        from datetime import timezone
        try:
            page=catalog_rows(folder,query,limit,offset)
            rows,signatures=describe_caches(app.state.jobs.parser,folder,selections,rows=page['items'])
            published=app.state.jobs.publisher.published_inputs([item['document_id'] for item in signatures], **({'profile':profile} if profile else {}))
            compare_publications(signatures,published)
            return {**page,'items':rows,'summary':cache_summary(rows),'scanned_at':now()}
        except psycopg.Error as exc:
            return JSONResponse(database_diagnostic(exc),status_code=503)
        except Exception as exc:
            raise HTTPException(503,'Comparaison impossible : vérifier les caches et PostgreSQL. Aucun traitement lancé.') from exc

    class CacheComparison(BaseModel):
        folder: str = Field(default='',max_length=2048)
        profile: str | None = Field(default=None,max_length=128)
        cache_selections: dict[str,str] = Field(default_factory=dict,max_length=10000)
        query: str = Field(default='',max_length=500)
        limit: int = Field(default=100,ge=1,le=200)
        offset: int = Field(default=0,ge=0)

    @app.post('/candidates')
    def compare_selected_caches(body:CacheComparison):
        if any(not re.fullmatch('[a-f0-9]{24}',ident) or not re.fullmatch('[a-f0-9]{64}',key) for ident,key in body.cache_selections.items()):
            raise HTTPException(422,'Sélection de cache invalide.')
        return compare_caches(body.folder,body.profile,body.cache_selections,body.query,body.limit,body.offset)

    @app.get('/jobs')
    def list_jobs():
        if hasattr(app.state.jobs, 'summaries'):
            items = app.state.jobs.summaries()
        else:
            items = [job_summary(job) for job in app.state.jobs.list()]
        active = [j for j in items if j.get('status') in ('running', 'queued')]
        inactive = [j for j in items if j.get('status') not in ('running', 'queued')]
        selected = active + inactive[:max(0, 20 - len(active))]
        return {'items': selected, 'total': len(items), 'active_count': len(active)}

    @app.get('/jobs/{ident}')
    def get_job(ident: str):
        job = app.state.jobs.get(ident)
        if not job:
            raise HTTPException(404, 'Travail introuvable.')
        if job.get('unit') == 'chunk' and job.get('status') in ('running', 'queued'):
            job['stop_requested'] = app.state.jobs.stop_event.is_set()
        return job_summary(job)

    @app.get('/chunks')
    def chunk_status(profile: str | None = Query(default=None,max_length=128), document_id: str | None = Query(default=None, max_length=500),
                     status: str | None = Query(default=None, pattern='^(pending|encoding|encoded|indexed|failed|interrupted)$'),
                     limit: int = Query(default=100, ge=1, le=200), offset: int = Query(default=0, ge=0)):
        try:
            return app.state.jobs.publisher.chunks(document_id=document_id,status=status,limit=limit,offset=offset,**({'profile':profile} if profile else {}))
        except psycopg.Error as exc:
            return JSONResponse(database_diagnostic(exc),status_code=503)
        except Exception as exc:
            raise HTTPException(503, 'Trace des chunks indisponible. Vérifier le schéma PostgreSQL.') from exc

    @app.post('/jobs', status_code=202)
    def submit(body: Request):
        try:
            return job_summary(app.state.jobs.submit(body.document_ids, body.use_cache, body.cache_only, body.profile, body.cache_selections))
        except psycopg.Error as exc:
            return JSONResponse(database_diagnostic(exc),status_code=503)
        except ValueError as exc:
            raise HTTPException(422,str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(409,str(exc)) from exc
    app.add_middleware(RequestIdMiddleware, service='km-indexer')
    return app

app = create_app()
