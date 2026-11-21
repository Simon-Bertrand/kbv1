"""PdfParse/PostgreSQL API consumed by Athena's existing HTTP toolbox."""
from contextlib import ExitStack, asynccontextmanager
import hmac
import json
import math
import os
import re
from pathlib import Path, PurePosixPath
from urllib.parse import quote, urlsplit

import httpx
import psycopg
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field
from starlette.background import BackgroundTask

from typing import Annotated, Literal

class VisualSearchRequest(BaseModel):
    question: str | None = Field(default=None, min_length=1, max_length=2000)
    page_id: str | None = None
    filters: dict[str, str | bool] = Field(default_factory=dict)
    metadata_filters: list[dict] = Field(default_factory=list, max_length=16)
    document_ids: list[str] = Field(default_factory=list, max_length=200)
    limit: int = Field(default=10, ge=1, le=100)

class ChunkMetadataRequest(BaseModel):
    chunk_ids: list[Annotated[str, Field(pattern=r"^c[a-f0-9]{32}$")]] = Field(min_length=1, max_length=100)


from km.store import Knowledgebase
from km.encoder_client import EncoderClient
from km.search_contract import SearchRequest, search_scope
from .inventory import adjacent_source_version, confined, external_cache_parent, source_relative
from .source_archive import archive_pdf
from ._service_logging import RequestIdMiddleware, accept_upstream


def asset_path(record, root, upstream_root):
    raw = record.get('asset_path')
    if not raw:
        raise FileNotFoundError('Image unavailable')
    path = PurePosixPath(raw)
    if not path.is_absolute():
        path = PurePosixPath(record.get('assets_dir') or '') / path
    relative = source_relative(str(path), upstream_root)
    if record.get('pdfparse_external_asset') is True:
        assets = source_relative(record.get('assets_dir'), upstream_root)
        adjacent = assets.parent.parent
        if (assets.name != 'assets' or not re.fullmatch('[a-f0-9]{64}', assets.parent.name)
                or not adjacent.name.endswith('.pdfparsecache')):
            raise ValueError('Invalid external cache asset')
        pdf = adjacent.with_name(adjacent.name.removesuffix('.pdfparsecache'))
        base = external_cache_parent(PurePosixPath(upstream_root) / pdf)
        if base is None or base.is_symlink():
            raise FileNotFoundError('External PDF cache unavailable')
        candidate = confined(base, relative.relative_to(adjacent).as_posix())
    else:
        candidate = confined(root, relative.as_posix())
    if not candidate.is_file():
        raise FileNotFoundError('Image unavailable')
    # Serve only real PNG/JPEG assets, never arbitrary cache files.
    with candidate.open('rb') as stream:
        signature = stream.read(8)
    if not (signature == b'\x89PNG\r\n\x1a\n' or signature.startswith(b'\xff\xd8\xff')):
        raise ValueError('Unsupported image')
    return candidate

class IncompatibleProfile(RuntimeError):
    """The selected profile was produced by another encoder."""


def query_vector(encoder, profile, question, dimension):
    if not profile or not encoder:
        raise RuntimeError('Embedding profile or encoder not configured')
    if profile.get('configuration',{}).get('legacy_unverified'):
        raise IncompatibleProfile('Profil historique non vérifié ; choisir le profil de l’encodeur chargé.')
    if profile['dimension'] != dimension or profile['encoder_fingerprint'] != encoder.fingerprint:
        raise IncompatibleProfile('L’encodeur chargé ne correspond pas au profil actif. Choisir son profil dans Indexation ou utiliser la recherche lexicale.')
    vectors = encoder.encode([question], query=True)
    if len(vectors) != 1 or len(vectors[0]) != dimension or not all(
        type(v) in (float,int) and math.isfinite(v) for v in vectors[0]
    ) or sum(v*v for v in vectors[0]) == 0:
        raise RuntimeError('Invalid query vector')
    return json.dumps(vectors[0], allow_nan=False)

def create_app(*, store=None, encoder=None, visual_encoder=None, reranker=None):
    @asynccontextmanager
    async def lifespan(app):
        with ExitStack() as stack:
            if isinstance(app.state.store, Knowledgebase) or (
                    app.state.store is None and os.environ.get('KB_DATABASE_URL')):
                stack.enter_context(db().pooled())
            if app.state.encoder is None and os.environ.get('ENCODER_URL'):
                owned = EncoderClient()
                app.state.encoder = owned
                stack.callback(setattr, app.state, 'encoder', None)
                stack.callback(owned.close)
            if app.state.reranker is None and os.environ.get('RERANKER_URL'):
                from .reranker_client import RerankerClient
                owned_reranker = RerankerClient()
                app.state.reranker = owned_reranker
                stack.callback(setattr, app.state, 'reranker', None)
                stack.callback(owned_reranker.close)
            yield

    app = FastAPI(title='KM PostgreSQL API', lifespan=lifespan, docs_url=None, redoc_url=None)
    app.state.store = store
    app.state.encoder, app.state.visual_encoder = encoder, visual_encoder
    app.state.reranker = reranker

    class SourceArchiveRequest(BaseModel):
        path: str = Field(min_length=1, max_length=4096)
        sha256: str = Field(pattern=r"^[a-f0-9]{64}$")

    @app.post('/internal/source-versions')
    def preserve_source(body: SourceArchiveRequest):
        versions = os.environ.get('SOURCE_VERSIONS_DIR')
        if not versions:
            raise HTTPException(503, 'Archivage des PDF non configuré.')
        try:
            archive_pdf(
                Path(os.environ.get('KB_CORPUS_ROOT', '/corpus')),
                os.environ.get('KB_UPSTREAM_ROOT', '/data'),
                Path(versions), body.path, body.sha256,
            )
        except FileNotFoundError as exc:
            raise HTTPException(404, 'PDF source absent du corpus.') from exc
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        return {'sha256': body.sha256}

    @app.middleware('http')
    async def api_auth_middleware(request: Request, call_next):
        if request.url.path in {'/health', '/ready'}:
            return await call_next(request)

        from fastapi.responses import JSONResponse
        raw_env = (os.environ.get('KB_ENV') or '').strip()
        if not raw_env:
            raw_env = (os.environ.get('ENVIRONMENT') or 'dev').strip()
        kb_env = raw_env.lower()
        is_prod = kb_env in ('production', 'prod')
        is_dev = kb_env in ('dev', 'development', 'test', 'testing')

        if not is_prod and not is_dev:
            return JSONResponse({'detail': f'Invalid environment configuration: {kb_env}'}, status_code=500)

        if request.url.path.rstrip('/') == '/internal/source-versions':
            token = (os.environ.get('KM_INDEXER_TOKEN') or '').strip()
            if is_prod and len(token) < 16:
                return JSONResponse({'detail': 'Archivage des PDF non configuré.'}, status_code=503)
            if token and not hmac.compare_digest(
                request.headers.get('authorization', '').encode(), f'Bearer {token}'.encode()
            ):
                return JSONResponse({'detail': 'Unauthorized'}, status_code=401)
            return await call_next(request)

        api_token = (os.environ.get('KM_API_TOKEN') or '').strip()
        admin_token = (
            os.environ.get('KM_INDEXER_ADMIN_TOKEN')
            or os.environ.get('KM_ADMIN_TOKEN')
            or os.environ.get('KM_CONSOLE_TOKEN')
            or ''
        ).strip()

        if is_prod:
            if not api_token or len(api_token) < 16:
                return JSONResponse({'detail': 'KM_API_TOKEN must be configured with >= 16 characters in production'}, status_code=500)
            if admin_token and len(admin_token) < 16:
                return JSONResponse({'detail': 'KM_INDEXER_ADMIN_TOKEN must be configured with >= 16 characters in production'}, status_code=500)
        else:
            if not api_token and not admin_token:
                return await call_next(request)

        auth = request.headers.get('authorization', '')
        if not auth.startswith('Bearer '):
            return JSONResponse({'detail': 'Unauthorized: missing or malformed Bearer token'}, status_code=401)

        token = auth[7:].strip().encode()
        valid = any(hmac.compare_digest(token, candidate.encode()) for candidate in (api_token, admin_token) if candidate)

        if not valid:
            return JSONResponse({'detail': 'Unauthorized: invalid bearer token'}, status_code=401)

        accept_upstream(request.headers)
        return await call_next(request)

    rerank_candidates = int(os.environ.get('RERANKER_CANDIDATES', '50'))
    rerank_failure_mode = os.environ.get('RERANKER_FAILURE_MODE', 'fallback')
    direct_search = os.environ.get('RERANKER_DIRECT_SEARCH', '').lower() in ('true', '1')
    if not 1 <= rerank_candidates <= 100 or rerank_failure_mode not in ('fallback', 'error'):
        raise ValueError('Invalid reranker candidate budget or failure mode')
    def db():
        if app.state.store is None:
            app.state.store = Knowledgebase(os.environ.get('KB_DATABASE_URL', ''), km=os.environ.get('KB_KM_SCHEMA')=='1')
        return app.state.store

    @app.exception_handler(psycopg.Error)
    async def database_error(request, exc):
        from fastapi.responses import JSONResponse
        if isinstance(exc, psycopg.errors.InvalidParameterValue):
            return JSONResponse({'detail': 'Invalid retrieval parameters'}, status_code=422)
        # Never expose DSNs, SQL diagnostics or source contents.
        from .diagnostics import database_diagnostic
        return JSONResponse(database_diagnostic(exc), status_code=503)

    @app.exception_handler(ValueError)
    async def invalid_source(request, exc):
        from fastapi.responses import JSONResponse
        return JSONResponse({'detail': 'Invalid source or knowledgebase configuration'}, status_code=422)

    @app.get('/ready')
    def ready():
        db().ready()
        return {'status': 'ready'}

    @app.get('/health')
    def health():
        extensions = db().ready()
        km = bool(getattr(db(), 'km', False))
        text_profile=db().profile('text')
        visual = False
        if app.state.visual_encoder and (text_profile or not km):
            visual_profile = db().profile('visual')
            if visual_profile:
                try:
                    visual = (visual_profile['dimension'] == 768
                              and visual_profile['encoder_fingerprint'] == app.state.visual_encoder.fingerprint
                              and not visual_profile.get('configuration', {}).get('legacy_unverified'))
                except (RuntimeError, ValueError):
                    pass
        semantic=False
        transition=False
        if text_profile and app.state.encoder:
            try:
                fingerprint = app.state.encoder.fingerprint
                semantic = (text_profile['dimension'] == 1024 and text_profile['encoder_fingerprint'] == fingerprint
                            and not text_profile.get('configuration', {}).get('legacy_unverified'))
                transition = not semantic
            except (RuntimeError,ValueError):pass
        return {'status': 'ok', 'backend': 'postgres', 'extensions': extensions,
                'active_text_profile':text_profile['profile'] if text_profile and 'profile' in text_profile else None,
                'capabilities': {'metadata': km, 'search_scope': km, 'visual_search_scope': km and visual,
                                 'lexical': True, 'semantic': semantic, 'hybrid': semantic or transition,
                                 'visual': visual}}

    def metadata_store():
        if not getattr(db(),'km',False):
            raise HTTPException(404,'Métadonnées KM non configurées')
        from km.metadata import Metadata
        return Metadata(db())

    @app.get('/metadata/atlas')
    def filesystem_atlas(limit: int = Query(default=200,ge=1,le=200), offset: int = Query(default=0,ge=0,le=100000)):
        from .filesystem_atlas import FilesystemAtlas
        try:
            return FilesystemAtlas(os.environ.get('KB_CORPUS_ROOT','/corpus')).page(limit,offset)
        except (OSError, RuntimeError) as exc:
            raise HTTPException(503,'Métadonnées filesystem indisponibles : vérifier le montage du corpus') from exc

    @app.get('/metadata/filesystem')
    def filesystem_metadata(path: str = Query(min_length=1,max_length=2048)):
        from .filesystem_atlas import FilesystemAtlas
        try:
            return FilesystemAtlas(os.environ.get('KB_CORPUS_ROOT','/corpus')).detail(path)
        except (OSError, ValueError, UnicodeError) as exc:
            raise HTTPException(422,'Chemin ou métadonnées filesystem invalides') from exc

    @app.get('/documents/{ident}/metadata')
    def metadata_read(ident:str):
        result=metadata_store().read(ident)
        if result is None:
            raise HTTPException(404,'Métadonnées de ce document absentes')
        return result

    @app.post('/metadata/columns')
    def metadata_columns():
        return {'columns':metadata_store().columns()}

    @app.post('/metadata/distinct')
    def metadata_distinct(body:dict):
        return metadata_store().distinct(body.get('column'),body.get('limit',100))

    @app.post('/metadata/query')
    def metadata_query(body:dict):
        return metadata_store().select(body.get('filters'),body.get('limit',200))

    @app.get('/document-types')
    def document_types():
        return {'items': db().document_types()}

    @app.post('/chunks/metadata')
    def chunk_metadata(request: ChunkMetadataRequest):
        return {'items': db().chunk_document_metadata(request.chunk_ids)}

    @app.post('/search')
    def search(request: SearchRequest):
        vector = None
        try:
            mode = request.mode
            if mode != 'lexical':
                try:
                    vector = query_vector(app.state.encoder, db().profile('text'), request.question, 1024)
                except IncompatibleProfile:
                    if mode != 'hybrid':
                        raise
                    mode = 'lexical'
            reranker = app.state.reranker
            candidate_limit = max(request.limit, rerank_candidates) if reranker else request.limit
            scope = search_scope(request)
            store = db()
            fingerprint = app.state.encoder.fingerprint if vector else None
            result = None
            direct_failure = False
            type_scope = request.type_document_ids or request.perimeter_code or request.type_document_labels or request.date_filters
            if direct_search and reranker and getattr(store, 'km', False) and not type_scope:
                from .reranker_client import DirectSearchUnsupported, RerankerUnavailable

                try:
                    result = reranker.search(
                        {**request.model_dump(exclude={'type_document_ids', 'perimeter_code', 'type_document_labels', 'date_filters'}),
                         'mode': mode, 'candidates': candidate_limit,
                         'vector': json.loads(vector) if vector else None, 'fingerprint': fingerprint,
                         'failure_mode': rerank_failure_mode},
                    )
                except DirectSearchUnsupported:
                    pass  # The score-only protocol remains valid with an older reranker.
                except RerankerUnavailable:
                    if rerank_failure_mode == 'error':
                        raise
                    direct_failure = True

            if result is None:
                result = store.search(request.question, mode, request.limit if direct_failure else candidate_limit,
                                      request.filters, vector, fingerprint=fingerprint, **scope)
                if scope:
                    result['scope_applied'] = True
                if direct_failure:
                    result['reranking'] = {'applied': False, 'reason': 'unavailable'}
                elif reranker:
                    from .reranker_client import rerank_hits

                    result = rerank_hits(result, request.question, request.limit, reranker,
                                         failure_mode=rerank_failure_mode)
            if mode != request.mode:
                result['retrieval'] = {'requested': request.mode, 'effective': mode,
                                       'reason': 'active_profile_encoder_mismatch'}
            return result
        except RuntimeError as exc:
            raise HTTPException(503, str(exc)) from exc

    @app.post('/search/visual')
    def search_visual(request: VisualSearchRequest):
        if request.question is None or request.page_id is not None:
            raise HTTPException(422, 'Text visual query required')
        if request.metadata_filters and not getattr(db(), 'km', False):
            raise HTTPException(422, 'Filtres métier disponibles uniquement dans KM')
        if app.state.visual_encoder is None:
            return {'results': [], 'visual_not_indexed': True, 'backend': 'postgres'}
        try:
            vector = query_vector(app.state.visual_encoder, db().profile('visual'), request.question, 768)
            scope = search_scope(request)
            result = db().search(request.question, 'semantic', request.limit, request.filters, vector, visual=True,
                                 fingerprint=app.state.visual_encoder.fingerprint, **scope)
            return {**result, 'scope_applied': True} if scope else result
        except RuntimeError as exc:
            raise HTTPException(503, str(exc)) from exc

    @app.get('/documents')
    def documents(offset: int=Query(0,ge=0), limit: int=Query(50,ge=1,le=200)):
        return db().documents(offset, limit)

    def get_document(ident):
        result = db().document(ident)
        if result is None:
            raise HTTPException(404, 'Document not found')
        return result

    @app.get('/documents/{ident}')
    def document(ident: str):
        return get_document(ident)

    @app.get('/documents/{ident}/source-reference')
    def source_reference(ident: str, source_sha256: str | None = None):
        # Metadata only: WebUI signs a Fileserver JWT; PDF bytes never pass here.
        doc = get_document(ident)
        relative = source_relative(doc['filepath'], os.environ.get('KB_UPSTREAM_ROOT', '/data'))
        if (relative.suffix.lower() != '.pdf' or
                any(p.startswith('.') or '.pdfparsecache' in p for p in relative.parts)):
            raise HTTPException(404, 'Source unavailable')
        sha = source_sha256 if source_sha256 is not None else doc.get('metadata', {}).get('pdfparse_source_sha256')
        if not isinstance(sha, str) or not re.fullmatch('[a-f0-9]{64}', sha):
            raise HTTPException(404, 'Verified PDF version unavailable; rerun ingestion')
        root = os.environ.get('KB_CORPUS_ROOT', '/corpus')
        try:
            if adjacent_source_version(root, relative.as_posix(), sha).is_file():
                return {'document_id': ident, 'relative_path': f'_versions/{relative.as_posix()}/{sha}.pdf'}
        except FileNotFoundError:
            pass
        return {'document_id': ident, 'relative_path': f'_versions/{sha}.pdf'}

    @app.get('/documents/{ident}/physical-pages/{number}')
    def physical_page(ident: str, number: int):
        try:
            result = db().physical_page(ident, number)
        except RuntimeError as exc:
            raise HTTPException(413, str(exc)) from exc
        if result is None:
            raise HTTPException(404, 'Aucun contenu publié associé à cette page')
        return result

    @app.get('/documents/{ident}/pages')
    def pages(ident: str, offset: int=Query(0,ge=0), limit: int=Query(50,ge=1,le=200),
              summary: bool = False):
        result = db().units(ident, offset, limit)
        if result is None:
            raise HTTPException(404, 'Document not found')
        if summary:
            items = []
            for item in result['items']:
                record = item.get('record') or {}
                headings = record.get('headings') or record.get('titles') or []
                if isinstance(headings, str):
                    headings = [headings]
                headings = [heading[:160] for heading in headings if isinstance(heading, str)][:3]
                if not headings:
                    text = '\n'.join(block.get('text', '') for block in item.get('blocks', [])
                                     if isinstance(block, dict))
                    headings = [line.strip()[:160] for line in text.splitlines() if line.strip()][:2]
                items.append({**{key: item[key] for key in ('id', 'document_id', 'locator', 'index', 'image')
                                  if key in item},
                              'headings': headings,
                              'record': {key: record[key] for key in ('body_pages', 'pages', 'locations')
                                         if key in record}})
            result = {**result, 'items': items}
        return result

    def get_unit(ident):
        result = db().unit(ident)
        if result is None:
            raise HTTPException(404, 'Record not found')
        return result

    @app.get('/pages/{ident}')
    @app.get('/passages/{ident}')
    def page(ident: str):
        return get_unit(ident)

    @app.get('/pages/{ident}/image')
    def image(ident: str):
        item = get_unit(ident)
        if not item['image']:
            raise HTTPException(404, 'Image unavailable')
        try:
            path = asset_path(item['record'], os.environ.get('KB_CORPUS_ROOT', '/corpus'),
                              os.environ.get('KB_UPSTREAM_ROOT', '/data'))
            return FileResponse(path)
        except (ValueError, OSError) as exc:
            raise HTTPException(404, 'Image unavailable') from exc

    @app.get('/view/documents/{ident}/pages')
    def viewer_pages(ident: str, offset: int=Query(0,ge=0), limit: int=Query(50,ge=1,le=200)):
        return dict(pages(ident, offset, limit), document=get_document(ident))

    @app.get('/view/pages/{ident}')
    def viewer_page(ident: str):
        item = get_unit(ident)
        blocks = [dict(b, key=str(i), level=0, source_type='PdfParse',
                       bbox_valid=False, reading_order=i) for i,b in enumerate(item['blocks'])]
        return dict(item, blocks=blocks, gaps=[], geometry_available=False,
                    extraction_method='PdfParse', coordinate_space=None,
                    image_url='/view/pages/'+ident+'/image' if item['image'] else None)

    @app.get('/view/pages/{ident}/image')
    def viewer_image(ident: str):
        return image(ident)

    @app.api_route('/view/documents/{ident}/source', methods=['GET','HEAD'])
    @app.api_route('/documents/{ident}/source', methods=['GET','HEAD'])
    async def source(ident: str, request: Request):
        if os.environ.get('KB_ENV') != 'dev':
            raise HTTPException(404, 'Source proxy available only in development')
        doc = get_document(ident)
        relative = source_relative(doc['filepath'], os.environ.get('KB_UPSTREAM_ROOT', '/data'))
        if (relative.suffix.lower() != '.pdf' or
                any(p.startswith('.') or '.pdfparsecache' in p for p in relative.parts)):
            raise HTTPException(404, 'Source unavailable')
        base = os.environ.get('KB_FILESERVER_URL', 'http://fileserver:8080')
        parsed = urlsplit(base)
        if parsed.scheme not in ('http','https') or not parsed.hostname or parsed.username or parsed.password or parsed.path not in ('','/') or parsed.query or parsed.fragment:
            raise HTTPException(503, 'Invalid fileserver configuration')
        client = httpx.AsyncClient(
            trust_env=False, follow_redirects=False, timeout=30,
            auth=httpx.BasicAuth(os.environ.get('FILESERVER_DEV_USER', 'dev'),
                                 os.environ.get('FILESERVER_DEV_PASSWORD', 'dev')))
        headers = {k: request.headers[k] for k in ('range','if-range','if-none-match') if k in request.headers}
        try:
            upstream = await client.send(client.build_request(request.method, base.rstrip('/')+'/files/'+quote(str(relative), safe='/'),
                                                               headers=headers), stream=True)
            if 300 <= upstream.status_code < 400 and upstream.status_code != 304:
                await upstream.aclose()
                raise HTTPException(502, 'Fileserver redirect refused')
        except (httpx.HTTPError, HTTPException) as exc:
            await client.aclose()
            if isinstance(exc, HTTPException):
                raise
            raise HTTPException(503, 'Fileserver unavailable') from exc
        async def close():
            await upstream.aclose()
            await client.aclose()
        response_headers = {k:upstream.headers[k] for k in (
            'content-type','content-length','content-range','accept-ranges','etag','last-modified') if k in upstream.headers}
        return StreamingResponse(upstream.aiter_raw(), status_code=upstream.status_code,
                                 headers=response_headers, background=BackgroundTask(close))
    app.add_middleware(RequestIdMiddleware, service='km-api')
    return app

app = create_app()
