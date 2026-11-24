"""Static frontend and fixed same-origin gateways. No registry/model access."""
import base64
import hmac
import os
import re
from contextlib import asynccontextmanager
from http.cookiejar import CookieJar, DefaultCookiePolicy
from pathlib import Path
from urllib.parse import quote

import anyio
import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, PlainTextResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from src.logging import RequestIdMiddleware, set_user, upstream_headers

ROOT = Path(__file__).parent
DIST = Path(os.environ.get('FRONTEND_DIST_DIR') or os.environ.get('KM_FRONTEND_DIST') or (ROOT / 'dist'))
TARGETS = {'athena': os.environ.get('FRONTEND_AGENT_URL', 'http://km-agent:8086'),
           'rag': os.environ.get('FRONTEND_RAG_URL', 'http://km-api:8000'),
           'knowledge': os.environ.get('FRONTEND_KB_URL', ''),
           'files': os.environ.get('FRONTEND_FILESERVER_URL', ''),
           'index': os.environ.get('FRONTEND_INDEX_URL', ''),
           'ingestion': os.environ.get('FRONTEND_INGESTION_URL', 'http://ingestion:8012')}
class RejectUpstreamCookies(DefaultCookiePolicy):
    def set_ok(self, cookie, request):
        return False


@asynccontextmanager
async def lifespan(app):
    async with httpx.AsyncClient(timeout=130, trust_env=False, follow_redirects=False,
                                 cookies=CookieJar(policy=RejectUpstreamCookies())) as client:
        app.state.client = client
        yield


class UpstreamStream(StreamingResponse):
    def __init__(self, upstream, headers):
        super().__init__(upstream.aiter_raw(), status_code=upstream.status_code, headers=headers)
        self.upstream = upstream

    async def __call__(self, scope, receive, send):
        try:
            await super().__call__(scope, receive, send)
        finally:
            with anyio.CancelScope(shield=True):
                await self.upstream.aclose()


app = FastAPI(title='KM frontend', version='1.4.0', docs_url=None, redoc_url=None, lifespan=lifespan)


def configured_environment():
    return (os.environ.get('KB_ENV') or os.environ.get('ENVIRONMENT') or 'dev').strip().lower()


@app.middleware('http')
async def console_auth(request: Request, call_next):
    if request.url.path == '/health':
        return await call_next(request)

    env = configured_environment()
    is_prod = env in ('production', 'prod')
    is_dev = env in ('dev', 'development', 'test', 'testing')

    if not is_prod and not is_dev:
        return PlainTextResponse(f'Invalid environment configuration: {env}', status_code=500)

    expected_user = os.environ.get('KM_CONSOLE_USER', 'admin')
    expected_password = (os.environ.get('KM_CONSOLE_PASSWORD') or '').strip()

    if is_prod:
        if not expected_password or len(expected_password) < 16:
            return PlainTextResponse('KM_CONSOLE_PASSWORD must be configured with >= 16 characters in production', status_code=500)

    # In dev, if no password is configured, bypass
    if not is_prod and not expected_password:
        return await call_next(request)

    auth = request.headers.get('authorization', '')
    if not auth.startswith('Basic '):
        return Response('Unauthorized', status_code=401, headers={'WWW-Authenticate': 'Basic realm="KM Console"'})

    try:
        decoded = base64.b64decode(auth[6:].strip(), validate=True)
        user, separator, pwd = decoded.partition(b':')
    except ValueError:
        return Response('Invalid credentials', status_code=401, headers={'WWW-Authenticate': 'Basic realm="KM Console"'})

    if not (separator and hmac.compare_digest(user, expected_user.encode())
            and hmac.compare_digest(pwd, expected_password.encode())):
        return Response('Unauthorized', status_code=401, headers={'WWW-Authenticate': 'Basic realm="KM Console"'})

    set_user(expected_user)
    return await call_next(request)


@app.middleware('http')
async def security(request, call_next):
    if request.method not in {'GET', 'HEAD'}:
        origin = request.headers.get('origin')
        if origin and origin.rstrip('/') != str(request.base_url).rstrip('/'):
            return PlainTextResponse('Foreign origin refused', status_code=403)
    response = await call_next(request)
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['Cache-Control'] = 'no-store'
    response.headers['Content-Security-Policy'] = (
        "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
        "font-src 'self' data:; img-src 'self' data: blob:; worker-src 'self' blob:; frame-src 'self'; "
        "frame-ancestors 'self'; object-src 'none'; base-uri 'self'"
    )
    return response


@app.get('/health')
def health():
    return {'status': 'ok', 'frontend': 'react'}


app.mount('/assets', StaticFiles(directory=DIST / 'assets', check_dir=False), name='frontend-assets')


async def relay(request, base, path, management=False, stream=False, query_params=None, *, target):
    if not base:
        raise HTTPException(503, 'Ce service documentaire n’est pas configuré.')
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > 1024 * 1024:
            raise HTTPException(413)
    headers = {**upstream_headers(), 'accept': request.headers.get('accept', '*/*')}
    headers.update({k:request.headers[k] for k in ('range','if-range','if-none-match') if k in request.headers})
    if request.headers.get('content-type'):
        headers['content-type'] = request.headers['content-type']
    if management and request.method not in {'GET', 'HEAD'}:
        headers.update({'origin': base, 'x-rag-action': 'manage'})
    if target == 'index':
        token = os.environ.get('KM_INDEXER_ADMIN_TOKEN') or os.environ.get('KM_ADMIN_TOKEN') or os.environ.get('KM_INDEXER_TOKEN', '')
        if token:
            headers['authorization'] = f'Bearer {token}'
    elif target == 'athena':
        token = os.environ.get('KM_AGENT_ADMIN_TOKEN') or os.environ.get('KM_ADMIN_TOKEN', '')
        if token:
            headers['authorization'] = f'Bearer {token}'
        headers['x-authenticated-user'] = 'km-console'
    elif target in {'rag', 'knowledge'}:
        token = os.environ.get('KM_API_TOKEN', '')
        if token:
            headers['authorization'] = f'Bearer {token}'
    elif target == 'ingestion':
        token = os.environ.get('KM_INDEXER_TOKEN', '')
        if token:
            headers['authorization'] = f'Bearer {token}'
    elif target == 'files' and configured_environment() == 'dev':
        user = os.environ.get('FILESERVER_DEV_USER', 'dev')
        password = os.environ.get('FILESERVER_DEV_PASSWORD', 'dev')
        credentials = base64.b64encode(f'{user}:{password}'.encode()).decode('ascii')
        headers['authorization'] = f'Basic {credentials}'
    client = request.app.state.client
    upstream = None
    streaming = False
    try:
        upstream = await client.send(client.build_request(
            request.method, base+'/'+path, params=request.query_params if query_params is None else query_params,
            content=bytes(body), headers=headers), stream=True)
        if 300 <= upstream.status_code < 400 and upstream.status_code != 304:
            raise HTTPException(502, 'Upstream redirect refused')
        response_headers = {k: upstream.headers[k] for k in
                            ('content-type', 'content-disposition', 'content-range', 'accept-ranges',
                             'etag', 'last-modified', 'retry-after') if k in upstream.headers}
        if target == 'ingestion' and path == 'api/events':
            response_headers['x-accel-buffering'] = 'no'
        if stream or path.endswith(('/source', '/image', '/crop', '/events')):
            for key in ('content-length', 'content-encoding'):
                if key in upstream.headers:
                    response_headers[key] = upstream.headers[key]
            response = UpstreamStream(upstream, response_headers)
            streaming = True
            return response
        data = bytearray()
        async for chunk in upstream.aiter_bytes():
            data.extend(chunk)
            if len(data) > 16 * 1024 * 1024:
                raise HTTPException(502, 'Upstream response too large')
        return Response(bytes(data), status_code=upstream.status_code, headers=response_headers)
    except httpx.HTTPError as exc:
        raise HTTPException(503, 'Service indisponible') from exc
    finally:
        if upstream is not None and not streaming:
            with anyio.CancelScope(shield=True):
                await upstream.aclose()


@app.api_route('/api/{target}/{path:path}', methods=['GET', 'HEAD', 'POST', 'DELETE'])
async def api(target: str, path: str, request: Request):
    if target == 'manage':
        raise HTTPException(404)
    if target == 'index':
        if path not in {'health', 'catalog/status', 'catalog/sync', 'catalog/chunks', 'catalog/files', 'catalog/cache', 'catalog/cache-choice', 'chunk-jobs', 'chunk-jobs/stop', 'chunk-jobs/files', 'documents', 'candidates', 'caches', 'jobs', 'chunks', 'profiles', 'profiles/documents', 'profiles/register', 'profiles/activate', 'profiles/rename', 'profiles/provenance', 'profiles/deletions', 'profiles/delete-preview', 'profiles/delete-embeddings'}:
            raise HTTPException(404)
        if request.method not in {'GET', 'HEAD'} and not (request.method == 'POST' and path in {'jobs','catalog/sync','catalog/cache-choice','chunk-jobs','chunk-jobs/stop','candidates','profiles/register','profiles/activate','profiles/rename','profiles/delete-preview','profiles/delete-embeddings'}):
            raise HTTPException(405)
        return await relay(request, TARGETS['index'], path, target=target)
    if target == 'ingestion':
        if path not in {'health', 'status', 'dag', 'files', 'events', 'pipeline/run', 'pipeline/stop'}:
            raise HTTPException(404)
        if request.method not in {'GET', 'HEAD'} and not (request.method == 'POST' and path in {'pipeline/run', 'pipeline/stop'}):
            raise HTTPException(405)
        upstream_path = 'health' if path == 'health' else f'api/{path}'
        return await relay(request, TARGETS['ingestion'], upstream_path, stream=(path == 'events'), target=target)
    if target == 'files':
        if request.method not in {'GET', 'HEAD'}:
            raise HTTPException(405)
        if path not in {'list', 'zip', 'raw'}:
            raise HTTPException(404)
        values = request.query_params.getlist('path')
        value = values[0] if len(values) == 1 else '' if not values else None
        if value is None or len(value) > 2048 or any(ord(c) < 32 or ord(c) == 127 for c in value) or '\\' in value:
            raise HTTPException(422, 'Chemin de fichier invalide.')
        parts = value.split('/') if value else []
        if len(parts) > 32 or any(not p or p.startswith('.') or '.pdfparsecache' in p for p in parts):
            raise HTTPException(422, 'Chemin de fichier invalide.')
        if path == 'raw':
            if not parts:
                raise HTTPException(422, 'Un fichier est requis.')
            response = await relay(request, TARGETS['files'], 'files/'+'/'.join(quote(p, safe='') for p in parts),
                                   stream=True, query_params={}, target=target)
            # Untrusted corpus content must never execute as the application origin.
            response.headers['content-type'] = 'application/octet-stream'
            response.headers['content-disposition'] = "attachment; filename*=UTF-8''"+quote(parts[-1], safe='')
            return response
        return await relay(request, TARGETS['files'], 'api/'+path, stream=path=='zip', query_params={'path':value},
                           target=target)
    allowed = {
        'knowledge': r'(health|metadata/(atlas|filesystem)|documents|documents/d[a-f0-9]{32}(/pages|/source|/metadata)?|pages/[cv][a-f0-9]{32}(/image)?)',
        'athena': r'(health|ready|runs|runs/[a-f0-9]{32}(/(events|event-detail|archive))?|eval/(template|compare|datasets(/[a-f0-9]{32})?|campaigns(/[a-f0-9]{32}(/(stop|attach|export)|/rows/[a-f0-9]{32}(/review)?)?)?))',
        'rag': r'(health|search|search/visual|metadata/columns|documents|documents/[a-zA-Z0-9-]+(/pages)?|pages/[a-zA-Z0-9-]+(/image)?|passages/[a-zA-Z0-9-]+)'
    }
    if target not in allowed or not re.fullmatch(allowed[target],path):
        raise HTTPException(404)
    valid = (request.method in {'GET','HEAD'}
        or request.method=='POST' and ((target,path) in {
            ('athena','runs'),('rag','search'),('rag','search/visual'),('rag','metadata/columns')}
            or target=='athena' and re.fullmatch(r'eval/(datasets|campaigns|campaigns/[a-f0-9]{32}/(stop|attach)|campaigns/[a-f0-9]{32}/rows/[a-f0-9]{32}/review)',path))
        or request.method=='DELETE' and target=='athena' and re.fullmatch(r'runs/[a-f0-9]{32}',path))
    if not valid: raise HTTPException(405)
    return await relay(request, TARGETS[target], path, target=target,
                       stream=target == 'athena' and path.endswith('/archive'))




@app.api_route('/{path:path}',methods=['GET','HEAD'])
async def route(path:str,request:Request):
    if path.rstrip('/') in {'','documents','atlas'} or re.fullmatch(r'viewer/d[a-f0-9]{32}',path.rstrip('/')):
        return FileResponse(DIST/'index.html')
    if path.rstrip('/')=='pdfparse':
        from fastapi.responses import RedirectResponse
        port=int(os.environ.get('PDFPARSE_PORT','8000'))
        if not 1<=port<=65535: raise HTTPException(503)
        return RedirectResponse(f'http://127.0.0.1:{port}/',status_code=307)
    if re.fullmatch(r'view/(documents/d[a-f0-9]{32}/(pages|source)|pages/[cv][a-f0-9]{32}(/image)?)',path):
        return await relay(request, TARGETS['knowledge'], path, target='knowledge')
    raise HTTPException(404)


app.add_middleware(RequestIdMiddleware, service="km-frontend")
