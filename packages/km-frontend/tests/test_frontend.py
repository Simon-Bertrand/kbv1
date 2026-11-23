"""HTTP and static assets for the KM console."""
import importlib.util
import json
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
DOCUMENT = 'd' + 'a'*32
RUN = 'a'*32
spec = importlib.util.spec_from_file_location('frontend_server', ROOT/'server.py')
server = importlib.util.module_from_spec(spec)
spec.loader.exec_module(server)


@pytest.fixture
def gateway(monkeypatch):
    monkeypatch.setenv('KB_ENV', 'dev')
    monkeypatch.delenv('KM_CONSOLE_PASSWORD', raising=False)
    calls = []
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args): pass
        def do_GET(self):
            calls.append(('GET', self.path, None, dict(self.headers)))
            self.send_response(200)
            self.send_header('Content-Type', 'image/png' if self.path.endswith('/image') else 'application/pdf' if self.path.endswith('/source') else 'application/json')
            self.send_header('Set-Cookie', 'upstream-private=value; Path=/')
            self.end_headers()
            self.wfile.write(b'p' * (17 * 1024 * 1024) if self.path.endswith(('/image', '/archive')) else b'%PDF-synthetic' if self.path.endswith('/source') else b'{"ok":true}')
        def do_POST(self):
            body = self.rfile.read(int(self.headers['Content-Length']))
            data = json.loads(body) if body else None
            calls.append(('POST', self.path, data, dict(self.headers)))
            self.send_response(200); self.end_headers(); self.wfile.write(b'{"ok":true}')
    upstream = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    base = f'http://127.0.0.1:{upstream.server_port}'
    for target in server.TARGETS:
        monkeypatch.setitem(server.TARGETS, target, base)
    thread = threading.Thread(target=upstream.serve_forever, daemon=True)
    thread.start()
    with TestClient(server.app) as client:
        yield client, calls, base
    upstream.shutdown(); upstream.server_close(); thread.join()


@pytest.mark.parametrize('path', ['/', '/documents', '/atlas', f'/viewer/{DOCUMENT}'])
def test_screen_routes(gateway, path):
    client, calls, _ = gateway
    response = client.get(path)
    assert response.status_code == 200
    assert 'type="module"' in response.text
    assert 'unsafe-inline' not in response.headers['content-security-policy'].split('script-src')[1].split(';')[0]
    policy = response.headers['content-security-policy']
    assert "font-src 'self' data:;" in policy
    assert "img-src 'self' data: blob:;" in policy
    assert "default-src 'self';" in policy
    assert calls == []


def test_templates_and_styles_preserve_console():
    shell = (ROOT/'src/shell/view.jsx').read_text()
    views = ''.join(p.read_text() for p in (ROOT/'src/features').glob('*/view.jsx'))
    ids = re.findall(r'\bid="([^"]+)"', shell + views)
    assert len(ids) == len(set(ids))
    assert {'askForm', 'answerSources', 'traceSummary', 'traceInspectorDialog', 'loadMoreDetail',
            'exportTrace', 'ragForm', 'ragScope', 'ragResults', 'evJudge', 'evAutomatic'} <= set(ids)
    for name in ('console', 'management', 'evaluation'):
        assert 'var(--' in (ROOT/f'src/styles/{name}.css').read_text()


@pytest.mark.parametrize('name', ['viewer', 'atlas'])
def test_standalone_views(name):
    markup = (ROOT/f'src/pages/{name}/view.jsx').read_text()
    assert 'export default' in markup and 'dangerouslySetInnerHTML' not in markup
    current_css = (ROOT/f'src/pages/{name}/style.css').read_text()
    assert current_css
    if name == 'atlas':
        assert '#more' in current_css


def test_file_gateway_bounds_paths_and_forces_safe_downloads(gateway):
    client,calls,_=gateway
    for path in ('../secret','.env','a//b','a/.pdfparsecache/x','a\\\\b'):
        before=len(calls)
        assert client.get('/api/files/list',params={'path':path}).status_code==422
        assert len(calls)==before
    assert client.post('/api/files/list',json={}).status_code==405
    assert client.get('/api/files/list?path=a&path=b').status_code==422
    response=client.get('/api/files/raw',params={'path':'Plans/été #?.html'},headers={'Range':'bytes=0-3'})
    assert response.status_code==200
    assert response.headers['content-type']=='application/octet-stream'
    assert response.headers['content-disposition'].startswith('attachment;')
    assert calls[-1][1]=='/files/Plans/%C3%A9t%C3%A9%20%23%3F.html'
    assert calls[-1][3]['range']=='bytes=0-3'
    assert client.get('/api/files/zip',params={'path':'Plans'}).status_code==200
    assert calls[-1][1]=='/api/zip?path=Plans'

def test_postgres_viewer_uses_document_handles(gateway):
    client,calls,_=gateway
    ident='d'+'a'*32
    assert client.get('/api/knowledge/documents').status_code==200
    assert calls[-1][1]=='/documents'
    assert client.get('/view/documents/'+ident+'/pages').status_code==200
    assert calls[-1][1]=='/view/documents/'+ident+'/pages'
    assert client.get('/view/documents/a-b/pages').status_code==404
    assert client.post('/api/knowledge/documents',json={}).status_code==405

def test_gateways_and_payloads(gateway, monkeypatch):
    client, calls, _ = gateway
    monkeypatch.setenv('KM_API_TOKEN', 'fixture-km-reader')
    monkeypatch.setenv('KM_INDEXER_ADMIN_TOKEN', 'fixture-indexer-admin')
    assert client.get('/api/rag/health').json() == {'ok':True}
    assert calls[-1][1] == '/health'
    assert client.get(f'/view/documents/{DOCUMENT}/pages?offset=200&limit=200').status_code == 200
    assert calls[-1][1].endswith('?offset=200&limit=200')
    payload = {'document_ids':[DOCUMENT]}
    assert client.post('/api/index/jobs', json=payload, headers={'Origin':'http://testserver'}).status_code == 200
    assert calls[-1][2] == payload
    assert calls[-1][3]['authorization'] == 'Bearer fixture-indexer-admin'
    assert client.post('/api/rag/metadata/columns').status_code == 200
    assert calls[-1][1] == '/metadata/columns'
    assert calls[-1][3]['authorization'] == 'Bearer fixture-km-reader'
    assert client.get(f'/view/documents/{DOCUMENT}/source').content == b'%PDF-synthetic'


def test_refuses_foreign_origins_paths_and_unknown_routes(gateway):
    client, calls, _ = gateway
    assert client.post('/jobs', json={}, headers={'Origin':'http://foreign'}).status_code == 403
    assert client.post('/runner/claim', json={}).status_code == 405
    assert client.get('/api/other/health').status_code == 404
    assert client.get('/api/manage/files/selection').status_code == 404
    assert client.post('/api/manage/status', json={}).status_code == 404
    assert client.post('/api/rag/documents', json={}).status_code == 405
    assert client.post('/api/manage/runner/claim', json={}).status_code == 404
    assert client.post('/status', json={}).status_code == 405
    assert client.get('/src/../server.py').status_code == 404
    assert client.get('/not-an-app-page').status_code == 404
    assert calls == []


def test_module_assets(gateway):
    client, _, _ = gateway
    index = client.get('/').text
    assets = re.findall(r'(?:src|href)="(/assets/[^"]+)"', index)
    assert assets
    for path in assets:
        assert client.get(path).status_code == 200
    assert client.get('/src/main.jsx').status_code == 404
    assert client.get('/server.py').status_code == 404


@pytest.mark.parametrize('path', [
    'documents/a-b/pages?offset=0&limit=20', 'passages/a-b',
    'pages/a-b',
])
def test_complete_read_contract(gateway, path):
    client, calls, _ = gateway
    assert client.get('/api/rag/'+path).status_code == 200
    assert calls[-1][1] == '/'+path
    assert client.post('/api/rag/'+path, json={}).status_code == 405


def test_oversize_request_rejected_before_forwarding(gateway):
    client, calls, _ = gateway
    assert client.post('/api/rag/search', content=b'x'*(1024*1024+1)).status_code == 413
    assert calls == []


@pytest.mark.parametrize('path, content_type', [
    ('/api/rag/pages/p1/image', 'image/png'),
    (f'/api/athena/runs/{RUN}/archive', 'application/json'),
])
def test_images_and_archives_stream_beyond_the_json_response_limit(gateway, path, content_type):
    client, calls, _ = gateway
    response = client.get(path)
    assert response.status_code == 200
    assert response.headers['content-type'] == content_type
    assert len(response.content) == 17 * 1024 * 1024


def test_shared_client_does_not_forward_upstream_cookies(gateway):
    client, calls, _ = gateway
    assert client.get('/api/rag/health').status_code == 200
    assert client.get('/api/knowledge/documents').status_code == 200
    assert all('Cookie' not in headers for _, _, _, headers in calls)


def test_shared_client_streams_two_large_responses_concurrently(gateway):
    client, _, _ = gateway
    paths = ['/api/rag/pages/p1/image', f'/api/athena/runs/{RUN}/archive']
    with ThreadPoolExecutor(2) as requests:
        responses = list(requests.map(client.get, paths))
    assert all(response.status_code == 200 for response in responses)
    assert all(len(response.content) == 17 * 1024 * 1024 for response in responses)


def test_ingestion_api_relay_routes(gateway):
    client, calls, _ = gateway
    res_status = client.get('/api/ingestion/status')
    assert res_status.status_code == 200
    assert calls[-1][1] == '/api/status'

    res_dag = client.get('/api/ingestion/dag')
    assert res_dag.status_code == 200
    assert calls[-1][1] == '/api/dag'

    res_run = client.post('/api/ingestion/pipeline/run', json={'force': True})
    assert res_run.status_code == 200
    assert calls[-1][0] == 'POST'
    assert calls[-1][1] == '/api/pipeline/run'
    assert calls[-1][2] == {'force': True}

