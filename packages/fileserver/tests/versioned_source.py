"""Verify authenticated delivery of adjacent and legacy PDF snapshots."""
import base64
import hashlib
import hmac
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import Request, urlopen
from uuid import uuid4


name = 'kb-versions-http-' + uuid4().hex[:12]
root = Path(__file__).resolve().parents[1]
secret = 'isolated-http-fixture-' + 'x' * 32
legacy_in_corpus = '--legacy-in-corpus' in sys.argv


def b64(value):
    return base64.urlsafe_b64encode(value).decode().rstrip('=')


def token(path):
    now = int(time.time())
    header = b64(b'{"alg":"HS256","typ":"JWT"}')
    payload = b64(json.dumps({
        'iss': 'webui', 'aud': 'fileserver', 'sub': 'actor', 'path': path,
        'iat': now, 'exp': now + 300,
    }).encode())
    unsigned = header + '.' + payload
    return unsigned + '.' + b64(hmac.new(secret.encode(), unsigned.encode(), hashlib.sha256).digest())


with tempfile.TemporaryDirectory(prefix='kb-http-') as folder:
    fixture = Path(folder)
    fixture.chmod(0o755)
    raw = fixture / 'raw'
    legacy = raw / '_versions' if legacy_in_corpus else fixture / 'versions'
    legacy.mkdir(parents=True)
    original = b'%PDF-original-version-abcdef'
    digest = hashlib.sha256(original).hexdigest()
    version = f'{digest}.pdf'
    (legacy / version).write_bytes(original)

    logical = 'docs/Notice.PDF'
    source = raw / logical
    source.parent.mkdir(parents=True)
    source.write_bytes(b'%PDF-updated-version-different')
    adjacent = Path(f'{source}.pdfparsecache') / 'versions'
    adjacent.mkdir(parents=True)
    (adjacent / version).write_bytes(original)
    (adjacent.parent / 'manifest.json').write_text('{}')

    args = ['docker', 'run', '--rm', '-d', '--name', name, '-p', '127.0.0.1::8080',
            '-e', 'FILESERVER_JWT_SECRET=' + secret, '-e', 'WEBUI_PUBLIC_ORIGIN=http://localhost:4173']
    if legacy_in_corpus:
        args.extend(['-e', 'FILESERVER_LEGACY_VERSIONS_ROOT='])
    for src, dst in [(root / 'nginx.conf', '/etc/nginx/nginx.conf'),
                     (root / 'file-delivery.conf', '/etc/nginx/file-delivery.conf'),
                     (root / 'njs/explorer.js', '/etc/nginx/njs/explorer.js'),
                     (raw, '/srv/data'), (raw if legacy_in_corpus else legacy, '/srv/source-versions')]:
        args.extend(['-v', f'{src}:{dst}:ro'])
    args.append('knowledgebase/fileserver:local')
    try:
        subprocess.run(args, check=True, capture_output=True)
        port = subprocess.check_output([
            'docker', 'inspect', '--format', '{{(index (index .NetworkSettings.Ports "8080/tcp") 0).HostPort}}', name,
        ], text=True).strip()
        base = 'http://127.0.0.1:' + port
        for _ in range(40):
            try:
                with urlopen(base + '/health', timeout=1):
                    break
            except OSError:
                time.sleep(0.1)
        else:
            raise RuntimeError(subprocess.check_output(['docker', 'logs', name], stderr=subprocess.STDOUT, text=True))

        for relative in (f'_versions/{version}', f'_versions/{logical}/{version}'):
            url = base + '/files/' + quote(relative) + '?token=' + token(relative)
            with urlopen(url) as response:
                assert response.status == 200 and response.read() == original
            with urlopen(Request(url, headers={'Range': 'bytes=0-7'})) as response:
                assert response.status == 206 and response.read() == original[:8]
            with urlopen(Request(url, method='HEAD')) as response:
                assert response.status == 200 and response.headers['Content-Length'] == str(len(original))

        proof = f'_versions/{logical}/{version}'
        forbidden = (
            (base + '/files/' + proof, {401}),
            (base + '/files/' + logical + '?token=' + token(proof), {403}),
            (base + '/files/' + logical + '.pdfparsecache/manifest.json?token=' + token(proof), {404}),
        )
        for url, statuses in forbidden:
            try:
                with urlopen(url):
                    raise AssertionError('Unauthorized delivery')
            except HTTPError as error:
                assert error.code in statuses, (url, error.code)
        print('PDF snapshots: adjacent and legacy delivery, Range, HEAD and JWT isolation verified.')
    finally:
        subprocess.run(['docker', 'rm', '-f', name], capture_output=True)
