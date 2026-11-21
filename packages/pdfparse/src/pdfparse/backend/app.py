"""FastAPI transport around the existing document pipeline and job queue."""
import asyncio
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import partial
import hashlib
import os
import logging
from pathlib import Path
from threading import RLock
from time import perf_counter
from typing import Literal
from uuid import uuid4
from urllib.parse import urlparse
from starlette.middleware.trustedhost import TrustedHostMiddleware

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, Response, JSONResponse
from fastapi.staticfiles import StaticFiles

from .presentation import result_view, settings_view
from .schemas import Document, Documents, Folder, FolderListing, Result, Settings, Status
from .schemas import RuntimeConfiguration, RuntimeOptions, RuntimeStatus
from .schemas import Job, JobEvent
from .schemas import SourceRoot, SourceRootSelection
from pdfparse.progress import Stage, observe_progress, report_progress, observe_inference
from pdfparse.logging import RequestIdMiddleware
from . import runtime as runtime_services
from pdfparse.enrich import AssetStore
from pdfparse.logging import set_user, submit_with_context

_LOG = logging.getLogger('pdfparse.backend')
_FOLDER_WORKERS = 4


def _job_error(error: BaseException) -> str:
    if isinstance(error, BaseExceptionGroup):
        return ' · '.join(_job_error(cause) for cause in error.exceptions)
    return str(error)


def validate_startup_environment(*, root: Path | None, assets_dir: Path | str | None) -> None:
    """Valide les variables d'environnement au démarrage selon KB_ENV ('dev' ou 'production')."""
    kb_env = os.environ.get('KB_ENV', 'dev').strip().lower()
    if kb_env not in ('dev', 'development', 'production', 'prod'):
        raise ValueError(f"KB_ENV invalide : '{kb_env}'. Valeurs attendues : 'dev' ou 'production'.")
    is_prod = kb_env in ('production', 'prod')

    if is_prod:
        # En production : FAIL-FAST strict sur les variables critiques
        if not root:
            source_env = os.environ.get('PDFPARSE_SOURCE_ROOT') or os.environ.get('DATA_DIR')
            if not source_env:
                raise RuntimeError(
                    "[FATAL ERROR] [PROD MODE] PDFPARSE_SOURCE_ROOT ou DATA_DIR est obligatoire en production."
                )
        if root is not None and not root.is_dir():
            raise RuntimeError(
                f"[FATAL ERROR] [PROD MODE] Le répertoire racine PDF n'existe pas : {root}"
            )

        allowed_raw = os.environ.get('ALLOWED_HOSTS', '').strip()
        if not allowed_raw or allowed_raw == '*':
            raise RuntimeError(
                "[FATAL ERROR] [PROD MODE] ALLOWED_HOSTS doit être explicitement restreint en production (ne peut être '*' ou vide)."
            )

        if not assets_dir and not os.environ.get('PDFPARSE_ASSETS_DIR'):
            raise RuntimeError(
                "[FATAL ERROR] [PROD MODE] PDFPARSE_ASSETS_DIR doit être explicitement configuré en production."
            )
    else:
        # En dev : Permissif avec avertissements explicites
        if not os.environ.get('KB_ENV'):
            _LOG.warning("[STARTUP WARNING] KB_ENV non défini, mode 'dev' appliqué par défaut.")

        allowed_raw = os.environ.get('ALLOWED_HOSTS', '').strip()
        if not allowed_raw or allowed_raw == '*':
            _LOG.warning(
                "[STARTUP WARNING] [DEV MODE] ALLOWED_HOSTS est défini à '*' (ou absent). Tous les hôtes sont autorisés. Interdit en production."
            )

        if not root and not os.environ.get('PDFPARSE_SOURCE_ROOT') and not os.environ.get('DATA_DIR'):
            _LOG.warning(
                "[STARTUP WARNING] [DEV MODE] Ni PDFPARSE_SOURCE_ROOT ni DATA_DIR n'est configuré. Le serveur démarre sans répertoire racine PDF pré-chargé."
            )

        if not assets_dir and not os.environ.get('PDFPARSE_ASSETS_DIR'):
            _LOG.warning(
                "[STARTUP WARNING] [DEV MODE] PDFPARSE_ASSETS_DIR non configuré, repli sur le dossier par défaut : %s",
                runtime_services.DEFAULT_ASSETS_DIR,
            )


@dataclass
class Entry:
    path: Path
    fingerprint: tuple[int, ...]
    document: Document
    content_sha256: str | None = None


@dataclass
class CachedResult:
    payload: bytes
    store: AssetStore | None
    assets: dict[str, str]
    size_bytes: int
    cache_directory: Path | None = None


def _fingerprint(stat) -> tuple[int, ...]:
    return stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns


def _pdf_sha(path: Path) -> str:
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def create_app(*, pipeline=None, root: Path | str | None = None, cache_capacity: int = 8,
               cache_max_bytes: int = 128 * 1024 * 1024, models=None, runtime_configuration: RuntimeConfiguration | None = None,
               assets_dir: Path | str | None = None, load_models_on_startup: bool = True) -> FastAPI:
    validate_startup_environment(root=Path(root) if root else None, assets_dir=assets_dir)
    from pdfparse.enrich import FileSystemAssetStore
    asset_store = FileSystemAssetStore(assets_dir if assets_dir is not None else
                                     os.environ.get('PDFPARSE_ASSETS_DIR') or runtime_services.DEFAULT_ASSETS_DIR)
    initial_root = root if root is not None else os.environ.get('PDFPARSE_SOURCE_ROOT')
    root = Path(initial_root).expanduser().resolve(strict=True) if initial_root else None
    if ((root is not None and not root.is_dir()) or type(cache_capacity) is not int or cache_capacity < 1
            or type(cache_max_bytes) is not int or cache_max_bytes < 1):
        raise ValueError('A source directory and positive cache capacity/byte budget are required')
    entries: dict[str, Entry] = {}
    results: OrderedDict[str, CachedResult] = OrderedDict()
    cached_bytes = 0
    jobs: OrderedDict[str, Job] = OrderedDict()
    lock = RLock()
    executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='pdfparse')
    shared_pipeline = pipeline
    injected_pipeline = pipeline is not None
    if models is None:
        from pdfparse import ModelRegistry
        models = ModelRegistry()
    runtime = RuntimeStatus(status='loaded' if pipeline else 'unloaded', configuration=runtime_configuration,
                            requested=runtime_configuration if not load_models_on_startup else None)
    pending_documents = 0
    batch_job: Job | None = None

    def forget_result(document_id):
        nonlocal cached_bytes
        old = results.pop(document_id, None)
        if old is not None:
            cached_bytes -= old.size_bytes

    def disk_result(entry):
        if runtime.status in ('loading', 'unloading', 'error') or runtime.configuration is None:
            return None
        from pdfparse.ingestion.cache import load_cached_run
        try:
            if shared_pipeline is None:
                return None
            configuration = shared_pipeline.configuration
            profile = runtime_services.effective_runtime_configuration(runtime.configuration, configuration.chunking)
            return load_cached_run(entry.path, profile, cache_dir=configuration.cache_dir)
        except (OSError, ValueError):
            return None

    def refresh_cached_status(entry):
        if entry.document.status in (Status.QUEUED, Status.PROCESSING, Status.ERROR):
            return
        available = disk_result(entry) is not None
        cached = results.get(entry.document.id)
        if cached is not None and cached.cache_directory is not None:
            if ((runtime.configuration is not None and not available)
                    or not (cached.cache_directory / 'manifest.json').is_file()
                    or not (cached.cache_directory / 'assets').is_dir()):
                forget_result(entry.document.id)
        entry.document.status = Status.READY if available or entry.document.id in results else Status.IDLE

    def publish_result(document_id, result, engine=None):
        nonlocal cached_bytes
        view = result_view(engine, result)
        for asset in view.assets:
            if asset.available:
                asset.url = f'/api/documents/{document_id}/assets/{asset.id}'
        payload = view.model_dump_json().encode('utf-8')
        assets = {asset.id: asset.asset_path for asset in view.assets if asset.available}
        size_bytes = len(payload) + sum(len(key.encode()) + len(value.encode()) for key, value in assets.items())
        if size_bytes > cache_max_bytes:
            raise RuntimeError('Le résultat dépasse le budget du cache HTTP ; augmenter cache_max_bytes ou traiter un PDF plus petit.')
        store = (FileSystemAssetStore(result.document.assets_dir) if result.document.assets_dir
                 else engine.configuration.asset_store)
        directory = (result.document.assets_dir.parent if result.document.assets_dir
                     and (engine is None or engine.configuration.cache) else None)
        forget_result(document_id)
        results[document_id] = CachedResult(payload, store, assets, size_bytes, directory)
        cached_bytes += size_bytes
        while len(results) > cache_capacity or cached_bytes > cache_max_bytes:
            old_id = next(iter(results))
            old = results[old_id]
            forget_result(old_id)
            if old.cache_directory is None:
                entries[old_id].document.status = Status.IDLE
        entries[document_id].content_sha256 = result.document.document_id
        entries[document_id].document.status = Status.READY

    def cached_result_available(document_id):
        cached = results.get(document_id)
        if cached is not None and cached.cache_directory is not None:
            if not ((cached.cache_directory / 'manifest.json').is_file()
                    and (cached.cache_directory / 'assets').is_dir()):
                forget_result(document_id)
                entries[document_id].document.status = Status.IDLE
                entries[document_id].content_sha256 = None
                return False
        if cached is None:
            result = disk_result(entries[document_id])
            if result is not None:
                publish_result(document_id, result)
                return True
            entries[document_id].document.status = Status.IDLE
        return cached is not None

    def clear_results():
        nonlocal cached_bytes
        results.clear()
        cached_bytes = 0

    def cache_info():
        with lock:
            return {'entries': len(results), 'bytes': cached_bytes,
                    'max_entries': cache_capacity, 'max_bytes': cache_max_bytes}

    @asynccontextmanager
    async def lifespan(app):
        if load_models_on_startup and shared_pipeline is None and runtime_configuration is not None:
            schedule_runtime(runtime_configuration, validate_in_worker=True)
        yield
        executor.shutdown(wait=True, cancel_futures=True)

    app = FastAPI(title='PdfParse Studio', version='1.0.0', lifespan=lifespan, docs_url=None, redoc_url=None)
    from .offline_docs import install_offline_docs
    install_offline_docs(app, '/api-docs')
    app.mount('/api-docs', StaticFiles(directory=Path(__file__).parent / 'static' / 'api-docs'), name='api-docs')
    app.state.cache_info = cache_info
    allowed_hosts = [host.strip().lower() for host in os.environ.get('ALLOWED_HOSTS', '*').split(',') if host.strip()]
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=allowed_hosts, www_redirect=False)

    @app.middleware('http')
    async def configured_origin(request: Request, call_next):
        import base64
        import hmac
        username = os.environ.get('PDFPARSE_ADMIN_USER', '')
        password = os.environ.get('PDFPARSE_ADMIN_PASSWORD', '')
        if not username or len(password) < 16:
            return JSONResponse({'detail': 'Studio authentication not configured'}, status_code=503)
        authorization = request.headers.get('authorization', '')
        try:
            if not authorization.startswith('Basic '): raise ValueError()
            supplied = base64.b64decode(authorization[6:], validate=True)
        except (ValueError, __import__('binascii').Error):
            supplied = b''
        if not hmac.compare_digest(supplied, (username + ':' + password).encode()):
            return JSONResponse({'detail': 'Authentication required'}, status_code=401,
                                headers={'WWW-Authenticate': 'Basic realm="pdfparse", charset="UTF-8"'})
        set_user(username)
        origin = request.headers.get('origin')
        if request.url.path.startswith('/api/') and origin:
            try:
                parsed = urlparse(origin)
                hostname = parsed.hostname or ''
                allowed = parsed.scheme in ('http', 'https') and any(
                    pattern == '*' or hostname == pattern.strip('[]')
                    or (pattern.startswith('*.') and hostname.endswith(pattern[1:]))
                    for pattern in allowed_hosts
                )
            except ValueError:
                allowed = False
            if not allowed:
                return JSONResponse({'detail': 'Origin host is not allowed'}, status_code=403)
        return await call_next(request)


    def is_corpus_path(path: Path) -> bool:
        return (path.is_relative_to(root) and not path.is_relative_to(root / '_versions')
                and all('.pdfparsecache' not in part for part in path.relative_to(root).parts))

    def resolve(path: str) -> Path:
        if root is None:
            raise HTTPException(409, 'Choisissez le dossier racine des fichiers PDF.')
        candidate = (root / path).resolve()
        if not candidate.is_relative_to(root):
            raise HTTPException(403, 'Le chemin sort du dossier autorisé.')
        if not is_corpus_path(candidate):
            raise HTTPException(404, 'Chemin introuvable.')
        if not candidate.exists():
            raise HTTPException(404, 'Chemin introuvable.')
        return candidate

    def get_pipeline(*, for_processing=False):
        nonlocal shared_pipeline
        with lock:
            if shared_pipeline is None:
                if for_processing and runtime.configuration is not None:
                    runtime_services.validate_selection(models, runtime.configuration)
                    shared_pipeline = runtime_services.build_pipeline(models, runtime.configuration, asset_store=asset_store)
                    runtime.status = 'loaded'
                    return shared_pipeline
                from pdfparse import PdfParseConfiguration, PdfParsePipeline
                configuration = (PdfParseConfiguration(
                    chunking=runtime_services.build_chunking(runtime.configuration.chunking),
                    asset_store=asset_store,
                ) if runtime.configuration is not None else PdfParseConfiguration(asset_store=asset_store))
                engine = PdfParsePipeline(configuration)
                if runtime.configuration is not None:
                    return engine  # Settings inspection must not replace the selected inference profile.
                shared_pipeline = engine
            return shared_pipeline

    def entry_for(document_id: str) -> Entry:
        entry = entries.get(document_id)
        if entry is None:
            raise HTTPException(404, 'Document inconnu ; actualisez la liste.')
        resolve(str(entry.path.relative_to(root)))
        if not entry.path.is_file():
            raise HTTPException(404, 'PDF introuvable.')
        stat = entry.path.stat()
        if _fingerprint(stat) != entry.fingerprint:
            raise HTTPException(409, 'Le PDF a changé ; actualisez la liste.')
        return entry

    def new_job(kind: str, name: str, document_id: str | None = None) -> Job:
        # Called under lock. Active jobs are never evicted.
        if len(jobs) >= 100:
            oldest = next(key for key, job in jobs.items() if job.status in ('succeeded', 'failed', 'cancelled'))
            del jobs[oldest]
        job = Job(id=uuid4().hex, kind=kind, name=name, document_id=document_id,
                  created_at=datetime.now(timezone.utc))
        jobs[job.id] = job
        return job

    def update_job(job: Job, stage: Stage, detail: str | None = None):
        with lock:
            if (job.stage, job.detail) == (stage, detail):
                return
            job.stage, job.detail = stage, detail
            job.events.append(JobEvent(stage=stage, detail=detail, at=datetime.now(timezone.utc)))
            del job.events[:-200]

    @contextmanager
    def track_job(job: Job):
        with lock:
            job.status, job.started_at = 'running', datetime.now(timezone.utc)
        failure = None
        stage, changed = None, perf_counter()
        def progress(next_stage, detail):
            nonlocal stage, changed
            now = perf_counter()
            with lock:
                if stage is not None:
                    job.stage_seconds[stage] = job.stage_seconds.get(stage, 0.) + now - changed
                stage, changed = next_stage.value, now
                update_job(job, next_stage, detail)
        def inference(record):
            with lock:
                job.inference_count += 1
                job.inferences.append(record)
                del job.inferences[:-500]
        try:
            with observe_progress(progress), observe_inference(inference):
                yield
        except Exception as error:
            failure = _job_error(error)
        with lock:
            if stage is not None:
                job.stage_seconds[stage] = job.stage_seconds.get(stage, 0.) + perf_counter() - changed
            if failure is not None:
                job.status = 'failed'
            else:
                job.status = 'cancelled' if job.stop_requested else 'succeeded'
            job.error, job.finished_at = failure, datetime.now(timezone.utc)

    def execute_job(job: Job, operation, *args):
        with track_job(job):
            operation(*args)

    @contextmanager
    def processing_document(entry: Entry):
        try:
            with lock:
                if entry_for(entry.document.id) is not entry:
                    raise RuntimeError('Le PDF a changé depuis sa mise en file.')
                entry.document.status = Status.PROCESSING
            report_progress(Stage.CONFIGURATION, 'Préparation du traitement')
            yield
        except Exception as error:
            with lock:
                entry.document.status = Status.ERROR
                entry.document.error = _job_error(error)
            raise

    def finish_document(entry: Entry, result, engine):
        document_id = entry.document.id
        report_progress(Stage.RENDERING, 'Préparation du Markdown et des vues')
        with lock:
            if entries.get(document_id) is not entry:
                raise RuntimeError('Le PDF a changé pendant le traitement.')
            entry_for(document_id)
            if runtime.configuration is not None and engine.configuration.cache:
                from pdfparse.ingestion.cache import remember_runtime_configuration
                profile = runtime_services.effective_runtime_configuration(runtime.configuration, engine.configuration.chunking)
                remember_runtime_configuration(entry.path, engine, profile)
            publish_result(document_id, result, engine)

    def process(entry: Entry):
        with processing_document(entry):
            engine = get_pipeline(for_processing=True)
            finish_document(entry, engine.run(entry.path), engine)

    def process_queued_document(entry: Entry):
        nonlocal pending_documents
        try:
            process(entry)
        finally:
            with lock:
                pending_documents -= 1

    def change_runtime(selection: RuntimeConfiguration | None, previous: RuntimeConfiguration | None,
                       validate_in_worker: bool = False):
        nonlocal shared_pipeline, injected_pipeline
        engine, failure = None, None
        try:
            if validate_in_worker and selection is not None:
                report_progress(Stage.CONFIGURATION, 'Validation du profil initial')
                runtime_services.validate_selection(models, selection)
            if (selection is not None and previous is not None and shared_pipeline is not None
                    and selection.model_dump(exclude={'chunking'}) == previous.model_dump(exclude={'chunking'})):
                from pdfparse import PdfParsePipeline
                report_progress(Stage.CONFIGURATION, 'Configuration du découpage ; modèles réutilisés')
                configuration = shared_pipeline.configuration.model_copy(update={
                    'chunking': runtime_services.build_chunking(selection.chunking),
                })
                engine = PdfParsePipeline(configuration, extractor=shared_pipeline.extractor)
            else:
                report_progress(Stage.UNLOADING, 'Libération des modèles et de la mémoire')
                # Drop the converter and its injected runtimes before loading new weights.
                with lock:
                    shared_pipeline = None
                    injected_pipeline = False
                models.unload()
                runtime_services.release_memory()
                engine = runtime_services.build_pipeline(models, selection, asset_store=asset_store) if selection is not None else None
        except Exception as error:
            failure = str(error)
            with lock:
                shared_pipeline = None
                injected_pipeline = False
            models.unload()
        finally:
            # Exception tracebacks have been released before collecting failed runtimes.
            runtime_services.release_memory()
        with lock:
            shared_pipeline = engine
            runtime.configuration = (selection or runtime.requested) if failure is None else None
            runtime.error = failure
            runtime.status = 'error' if failure is not None else 'loaded' if selection is not None else 'unloaded'
        if failure is not None:
            raise RuntimeError(failure)

    def schedule_runtime(selection: RuntimeConfiguration | None, *, validate_in_worker: bool = False):
        with lock:
            if (pending_documents or runtime.status in ('loading', 'unloading')
                    or (batch_job is not None and batch_job.status in ('queued', 'running'))):
                raise HTTPException(409, 'Attendez la fin du traitement ou du changement de modèles.')
            if selection is not None and not validate_in_worker:
                try:
                    runtime_services.validate_selection(models, selection)
                except (ValueError, KeyError) as error:
                    raise HTTPException(422, str(error)) from error
            runtime.status = 'loading' if selection is not None else 'unloading'
            current = runtime.configuration or runtime.requested or RuntimeConfiguration()
            previous = runtime.configuration
            runtime.requested = selection or RuntimeConfiguration(chunking=current.chunking)
            runtime.configuration, runtime.error = None, None
            runtime.revision += 1
            clear_results()
            for entry in entries.values():
                entry.document.status, entry.document.error = Status.IDLE, None
            response = runtime.model_copy(deep=True)
            job = new_job('runtime', 'Chargement des modèles' if selection is not None else 'Déchargement des modèles')
            submit_with_context(executor, execute_job, job, change_runtime, selection, previous, validate_in_worker)
            return response

    @app.get('/api/jobs', response_model=list[Job], operation_id='listJobs')
    def list_jobs():
        with lock:
            return [job.model_copy(deep=True) for job in reversed(jobs.values())]

    @app.post('/api/jobs/{job_id}/stop', response_model=Job, operation_id='stopFolderJob')
    def stop_folder_job(job_id: str):
        with lock:
            job = jobs.get(job_id)
            if job is None or job.kind != 'batch':
                raise HTTPException(404, 'Lot d’extraction introuvable.')
            if job.status in ('queued', 'running'):
                job.stop_requested = True
            return job.model_copy(deep=True)

    @app.get('/api/runtime/options', response_model=RuntimeOptions, operation_id='getRuntimeOptions')
    def get_runtime_options():
        from .defaults import default_runtime_configuration
        options = runtime_services.model_options(models)
        devices = runtime_services.available_devices()
        with lock:
            return RuntimeOptions(models=options, devices=devices, runtime=runtime.model_copy(deep=True),
                                  defaults=default_runtime_configuration())

    @app.get('/api/runtime', response_model=RuntimeStatus, operation_id='getRuntime')
    def get_runtime():
        with lock:
            return runtime.model_copy(deep=True)

    @app.post('/api/runtime', response_model=RuntimeStatus, status_code=202, operation_id='loadRuntime')
    def load_runtime(selection: RuntimeConfiguration):
        return schedule_runtime(selection)

    @app.delete('/api/runtime', response_model=RuntimeStatus, status_code=202, operation_id='unloadRuntime')
    def unload_runtime():
        return schedule_runtime(None)

    @app.get('/api/source', response_model=SourceRoot, operation_id='getSourceRoot')
    def get_source_root():
        with lock:
            return SourceRoot(root=str(root) if root is not None else None)

    @app.post('/api/source', response_model=SourceRoot, operation_id='selectSourceRoot')
    def select_source_root(selection: SourceRootSelection):
        nonlocal root
        with lock:
            if root is not None:
                raise HTTPException(409, 'Le dossier racine est déjà défini pour ce serveur.')
        try:
            candidate = Path(selection.path).expanduser().resolve(strict=True)
            if not candidate.is_dir():
                raise HTTPException(400, 'Un dossier est attendu.')
            with os.scandir(candidate):
                pass
        except FileNotFoundError as error:
            raise HTTPException(404, 'Chemin introuvable.') from error
        except PermissionError as error:
            raise HTTPException(403, 'Dossier non accessible.') from error
        except (OSError, RuntimeError, ValueError) as error:
            raise HTTPException(400, 'Chemin de dossier invalide.') from error
        with lock:
            if root is not None:
                raise HTTPException(409, 'Le dossier racine est déjà défini pour ce serveur.')
            root = candidate
            return SourceRoot(root=str(root))

    @app.get('/api/folders', response_model=FolderListing, operation_id='listFolders')
    def list_folders(path: str = ''):
        directory = resolve(path)
        if not directory.is_dir():
            raise HTTPException(400, 'Un dossier est attendu.')
        try:
            folders = [Folder(name=child.name, path=str(child.relative_to(root)))
                       for child in sorted(directory.iterdir(), key=lambda p: p.name.casefold())
                       if child.is_dir() and is_corpus_path(child.resolve())]
        except PermissionError as error:
            raise HTTPException(403, 'Dossier non accessible.') from error
        return FolderListing(root=str(root), path=str(directory.relative_to(root)),
                             parent=str(directory.parent.relative_to(root)) if directory != root else None,
                             folders=folders)

    def resolve_folder(folder: str) -> Path:
        directory = resolve(folder)
        if not directory.is_dir():
            raise HTTPException(400, 'Un dossier est attendu.')
        return directory

    def find_pdfs(directory: Path, recursive: bool = True, search: str = '') -> list[tuple[str, Path]]:
        found = []
        needle = search.casefold()
        try:
            # Traverse without entering caches or following directory symlinks.
            for current, folders, files in os.walk(directory):
                folders[:] = sorted(name for name in folders if '.pdfparsecache' not in name
                                    and (Path(current) != root or name != '_versions'))
                for name in files:
                    path = Path(current) / name
                    if path.suffix.lower() != '.pdf' or not path.is_file():
                        continue
                    relative = str(path.relative_to(root))
                    if needle not in relative.casefold() or not is_corpus_path(path.resolve()):
                        continue
                    try:
                        stat = path.stat()
                        size = stat.st_size
                    except OSError:
                        continue
                    found.append((relative, path, size))
                if not recursive:
                    break
        except PermissionError as error:
            raise HTTPException(403, 'Dossier non accessible.') from error
        found.sort(key=lambda item: (item[2], item[0].casefold(), item[0]))
        return [(item[0], item[1]) for item in found]

    def register_document(relative: str, path: Path) -> Entry:
        # Called under lock.
        document_id = hashlib.sha256(relative.encode()).hexdigest()[:24]
        stat = path.stat()
        fingerprint = _fingerprint(stat)
        previous = entries.get(document_id)
        if previous is not None and previous.fingerprint == fingerprint:
            return previous
        forget_result(document_id)
        entry = Entry(path, fingerprint, Document(
            id=document_id, name=path.name, path=relative, size=stat.st_size,
            version=hashlib.sha256(repr(fingerprint).encode()).hexdigest()[:24]))
        entries[document_id] = entry
        return entry

    @app.get('/api/documents', response_model=Documents, operation_id='listDocuments')
    def list_documents(folder: str = '', recursive: bool = True,
                       page: int = Query(1, ge=1), page_size: int = Query(50, ge=1, le=200),
                       search: str = ''):
        directory = resolve_folder(folder)
        found = find_pdfs(directory, recursive, search)
        total = len(found)
        page = min(page, max(1, (total + page_size - 1) // page_size))
        documents = []
        for relative, path in found[(page - 1) * page_size:page * page_size]:
            with lock:
                try:
                    entry = register_document(relative, path)
                except FileNotFoundError:
                    continue  # A source can disappear while the corpus is being listed.
                refresh_cached_status(entry)
                documents.append(entry.document.model_copy())
        return Documents(folder=str(directory.relative_to(root)), documents=documents,
                         total=total, page=page, page_size=page_size)

    def needs_processing(entry: Entry) -> bool:
        document_id = entry.document.id
        if entry.document.status == Status.READY:
            cached_result_available(document_id)
        if entry.document.status == Status.READY and _pdf_sha(entry.path) != entry.content_sha256:
            forget_result(document_id)
            entry.document.status, entry.document.error = Status.IDLE, None
        return entry.document.status not in (Status.QUEUED, Status.PROCESSING, Status.READY)

    def queue_document(entry: Entry) -> Job:
        entry.document.status, entry.document.error = Status.QUEUED, None
        return new_job('document', entry.document.path, entry.document.id)

    def claim_folder_document(batch: Job, relative: str, path: Path) -> tuple[Job, Entry] | Literal[False] | None:
        with lock:
            if batch.stop_requested:
                return False
            resolve(relative)
            entry = register_document(relative, path)
            refresh_cached_status(entry)
            if not needs_processing(entry):
                return None
            job = queue_document(entry)
            jobs.move_to_end(batch.id)
            return job, entry

    def build_folder_pipeline(queued):
        if runtime.configuration is not None:
            # Resolve a lazy Studio profile first. The queued registry reuses its loaded models.
            get_pipeline(for_processing=True)
            return runtime_services.build_pipeline(queued, runtime.configuration, asset_store=asset_store)
        from pdfparse import PdfParseConfiguration, PdfParsePipeline
        engine = PdfParsePipeline(PdfParseConfiguration(asset_store=asset_store))
        engine.extractor._scheduler = queued.scheduler
        return engine

    def process_folder(batch: Job, folder: str):
        from pdfparse.models.scheduling import QueuedModelRegistry, available_cpu_count, await_future, cpu_thread_budget

        with lock:
            if batch.stop_requested:
                return
        found = find_pdfs(resolve_folder(folder))
        total = len(found)
        report_progress(Stage.EXTRACTION, f'0/{total} PDF traités')
        paths = iter(found)
        failed = skipped = completed = 0
        last_error = ''

        async def consume(queued, bridge):
            nonlocal failed, skipped, completed, last_error
            scheduler = queued.scheduler
            engine = None
            for relative, path in paths:
                with lock:
                    if batch.stop_requested:
                        break
                claimed = None
                try:
                    claimed = await scheduler.run_cpu(claim_folder_document, batch, relative, path)
                    if claimed is False:
                        return
                    if claimed is None:
                        skipped += 1
                        continue
                    job, entry = claimed
                    with track_job(job):
                        if injected_pipeline:
                            await await_future(submit_with_context(bridge, process, entry))
                        else:
                            with processing_document(entry):
                                if engine is None:
                                    engine = await scheduler.run_cpu(build_folder_pipeline, queued)
                                result = await engine.run_async(entry.path, scheduler=scheduler, bridge_executor=bridge)
                                await scheduler.run_cpu(finish_document, entry, result, engine)
                    if job.status == 'failed':
                        failed += 1
                        last_error = f'{relative} · {job.error}'
                except (HTTPException, OSError, RuntimeError) as error:
                    failed += 1
                    last_error = f'{relative} · {error}'
                finally:
                    if claimed is not False:
                        completed += 1
                        report_progress(Stage.EXTRACTION, f'{completed}/{total} · {relative}')

        if found:
            # An injected pipeline can own arbitrary state; keep its existing execution contract.
            workers = 1 if injected_pipeline else min(_FOLDER_WORKERS, total)
            cpu_count = available_cpu_count()
            cpu_workers = min(8, cpu_count)
            cpu_threads = max(1, cpu_count // cpu_workers)

            async def run_folder(queued, bridge):
                async with asyncio.TaskGroup() as group:
                    for _ in range(workers):
                        group.create_task(consume(queued, bridge))

            with (cpu_thread_budget(cpu_threads),
                  QueuedModelRegistry(models, cpu_workers=cpu_workers, cpu_threads=cpu_threads) as queued,
                  ThreadPoolExecutor(max_workers=workers, thread_name_prefix='pdfparse-docling') as bridge):
                asyncio.run(run_folder(queued, bridge))

        with lock:
            stopped = batch.stop_requested
        prefix = 'Extraction arrêtée · ' if stopped else ''
        report_progress(Stage.RENDERING, f'{prefix}{completed - failed - skipped}/{total} PDF analysés · '
                        f'{skipped} déjà analysés ou en file · {failed} échecs')
        if failed and not stopped:
            raise RuntimeError(f'Dernière erreur : {last_error}')

    def check_processing_runtime():
        if runtime.status in ('loading', 'unloading', 'error'):
            raise HTTPException(409, 'Chargez une configuration valide ou déchargez les modèles avant de traiter un PDF.')

    @app.post('/api/documents/process', response_model=Job, status_code=202, operation_id='processFolder')
    def schedule_folder(folder: str = ''):
        nonlocal batch_job
        directory = resolve_folder(folder)
        relative = str(directory.relative_to(root))
        name = f'Analyse du dossier · {relative}' if relative != '.' else 'Analyse du dossier source'
        with lock:
            check_processing_runtime()
            if batch_job is not None and batch_job.status in ('queued', 'running'):
                if batch_job.name == name:
                    return batch_job.model_copy(deep=True)
                raise HTTPException(409, 'Attendez la fin de l’analyse du dossier en cours.')
            batch_job = new_job('batch', name)
            submit_with_context(executor, execute_job, batch_job, process_folder, batch_job, relative)
            return batch_job.model_copy(deep=True)

    @app.get('/api/documents/{document_id}', response_model=Document, operation_id='getDocument')
    def get_document(document_id: str):
        with lock:
            entry = entry_for(document_id)
            refresh_cached_status(entry)
            return entry.document.model_copy()

    @app.get('/api/documents/{document_id}/pdf', response_class=FileResponse, operation_id='getPdf',
             responses={200: {'content': {'application/pdf': {'schema': {'type': 'string', 'format': 'binary'}}}}})
    def get_pdf(document_id: str):
        with lock:
            path = entry_for(document_id).path
        return FileResponse(path, media_type='application/pdf', filename=path.name, content_disposition_type='inline',
                            headers={'ETag': '"' + hashlib.sha256(repr(_fingerprint(path.stat())).encode()).hexdigest() + '"'})

    @app.post('/api/documents/{document_id}/process', response_model=Document, status_code=202, operation_id='processDocument')
    def process_document(document_id: str):
        nonlocal pending_documents
        with lock:
            check_processing_runtime()
            entry = entry_for(document_id)
            if not needs_processing(entry):
                return entry.document.model_copy()
            if pending_documents >= 20:
                raise HTTPException(429, 'La file de traitement est pleine.')
            job = queue_document(entry)
            pending_documents += 1
            submit_with_context(executor, execute_job, job, process_queued_document, entry)
            return entry.document.model_copy()

    @app.get('/api/documents/{document_id}/result', response_model=Result, operation_id='getResult')
    def get_result(document_id: str):
        with lock:
            entry_for(document_id)
            if not cached_result_available(document_id):
                raise HTTPException(409, 'Le document doit être traité avant de consulter ses résultats.')
            results.move_to_end(document_id)
            return Response(results[document_id].payload, media_type='application/json')

    @app.get('/api/documents/{document_id}/assets/{asset_id}', response_class=Response,
             operation_id='getAsset', responses={200: {'content': {'image/png': {'schema': {'type': 'string', 'format': 'binary'}}}}})
    def get_asset(document_id: str, asset_id: str, request: Request):
        with lock:
            entry_for(document_id)
            if not cached_result_available(document_id):
                raise HTTPException(409, 'Le document doit être traité avant de consulter ses images.')
            cached = results[document_id]
            locator = cached.assets.get(asset_id)
            if locator is None or cached.store is None:
                raise HTTPException(404, 'Image introuvable pour ce document.')
            results.move_to_end(document_id)
        headers = {'ETag': f'"{asset_id}"', 'Cache-Control': 'private, max-age=3600, immutable',
                   'X-Content-Type-Options': 'nosniff'}
        if request.headers.get('if-none-match') == headers['ETag']:
            return Response(status_code=304, headers=headers)
        try:
            content = cached.store.get(locator)
        except (FileNotFoundError, KeyError, ValueError) as error:
            with lock:
                if results.get(document_id) is cached:
                    forget_result(document_id)
                    entries[document_id].document.status = Status.IDLE
            raise HTTPException(404, 'Image du cache indisponible ; relancez le traitement.') from error
        return Response(content, media_type='image/png', headers=headers)

    @app.get('/api/settings', response_model=Settings, operation_id='getSettings')
    def get_settings():
        with lock:
            if runtime.status in ('loading', 'unloading', 'error'):
                raise HTTPException(409, 'Configuration indisponible pendant le changement de modèles ou après un échec de chargement.')
            return settings_view(get_pipeline(), cache_capacity)

    # Mount only a built frontend. API/OpenAPI routes are registered first.
    dist = Path(__file__).parent.parent / 'front' / 'dist'
    if dist.is_dir():
        app.mount('/', StaticFiles(directory=dist, html=True), name='front')
    app.add_middleware(RequestIdMiddleware, service='pdfparse')
    return app
