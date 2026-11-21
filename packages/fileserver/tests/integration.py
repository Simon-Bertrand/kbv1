"""Real HTTP checks, reusable against Docker or a local NGINX test instance.

Use only with a disposable data directory: --prepare creates test fixtures.
"""
import argparse
import io
import json
from pathlib import Path
import time
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zipfile import ZipFile


def prepare(root):
    root.mkdir(parents=True, exist_ok=True)
    root.chmod(0o755)  # mktemp defaults to 0700; NGINX workers must traverse it.
    for directory in ('docs/empty', 'docs/sub é #?%', 'docs/.git',
                      'docs/should.pdfparsecache-xxx', 'empty', 'many'):
        (root / directory).mkdir(parents=True, exist_ok=True)
    files = {
        'docs/test.pdf': b'hello staticzip',
        'docs/zero.bin': b'',
        'docs/sub é #?%/utf é #?%+.txt': 'été\n'.encode(),
        'docs/sub é #?%/binary.bin': bytes(range(256)) * 8192,
        'docs/.git/config': b'secret',
        'docs/should.pdfparsecache-xxx/private.txt': b'secret',
        'docs/.env': b'secret',
        'docs/file.pdfparsecache.json': b'secret',
        'docs/odd\\name.txt': b'skip',
        'docs/odd\nname.txt': b'skip',
    }
    for name, data in files.items():
        (root / name).write_bytes(data)
    for i in range(100):
        (root / 'many' / f'{i:03}.txt').write_bytes(str(i).encode())
    link = root / 'docs/link'
    if not link.is_symlink():
        link.symlink_to('/etc/passwd')


def check(base, root):
    def get(route, headers=None, method='GET'):
        request = Request(base.rstrip('/') + route, headers=headers or {}, method=method)
        try:
            response = urlopen(request, timeout=30)
        except HTTPError as error:
            response = error
        with response:
            return response.status, response.headers, response.read()

    def zip_request(folder, **kwargs):
        time.sleep(0.55)  # Respect the production ZIP rate limit.
        return get('/api/zip?' + urlencode({'path': folder}), **kwargs)

    def expected(folder):
        directory = root / folder
        prefix = directory.name if folder else 'storage'
        result = {prefix + '/': b''}
        for path in directory.rglob('*'):
            relative = path.relative_to(directory)
            if path.is_symlink() or any(
                p.startswith('.') or '.pdfparsecache' in p or '\\' in p
                or any(ord(c) < 32 or ord(c) == 127 for c in p)
                for p in relative.parts
            ):
                continue
            name = prefix + '/' + relative.as_posix()
            result[name + '/' if path.is_dir() else name] = b'' if path.is_dir() else path.read_bytes()
        return result

    def archive(folder, headers=None):
        status, response_headers, body = zip_request(folder, headers=headers)
        assert status == 200, (folder, status, body[:300])
        assert response_headers.get_content_type() == 'application/zip'
        assert 'attachment;' in response_headers['Content-Disposition']
        assert body.startswith(b'PK\x03\x04'), body[:100]
        assert int(response_headers['Content-Length']) == len(body)
        with ZipFile(io.BytesIO(body)) as z:
            assert z.testzip() is None
            wanted = expected(folder)
            assert len(z.namelist()) == len(wanted), (z.namelist(), wanted.keys())
            assert set(z.namelist()) == set(wanted), (z.namelist(), wanted.keys())
            for name, data in wanted.items():
                assert z.read(name) == data, name
        print('PASS ZIP:', folder or '(root)', flush=True)
        return len(body)

    for route in ('/', '/app.js', '/style.css'):
        assert get(route)[0] == 200, route
    for route in ('/nope.js', '/_index/docs/', '/_zipfiles/docs/test.pdf',
                  '/files/docs/.git/config', '/files/docs/should.pdfparsecache-xxx/private.txt'):
        assert get(route)[0] == 404, route
    assert get('/files/docs/link')[0] != 200
    status, _, data = get('/api/list?path=docs')
    assert status == 200
    names = {entry['name'] for entry in json.loads(data)['entries']}
    assert names == {'empty', 'sub é #?%', 'test.pdf', 'zero.bin'}, names
    status, _, data = get('/api/list?' + urlencode({'path': 'docs/sub é #?%'}))
    assert status == 200, (status, data)
    assert {e['name'] for e in json.loads(data)['entries']} == {'utf é #?%+.txt', 'binary.bin'}
    status, _, data = get('/files/docs/test.pdf', {'Range': 'bytes=0-4'})
    assert status == 206 and data == b'hello'
    length = archive('docs')
    status, headers, body = zip_request('docs', method='HEAD')
    assert status == 200 and body == b''
    assert int(headers['Content-Length']) == length
    for folder in ('docs/sub é #?%', 'empty', 'many', ''):
        archive(folder)
    archive('docs', {'Range': 'bytes=0-4'})  # Unknown CRC means a full ZIP response.
    for folder in ('missing', 'docs/../empty', 'docs/.git', 'docs/link'):
        status, headers, body = zip_request(folder)
        assert status == 404, (folder, status)
        assert headers.get_content_type() == 'application/json'
        assert 'X-Archive-Files' not in headers
        assert 'error' in json.loads(body)
    status, _, _ = get('/')
    assert status == 200
    print('PASS HTTP: UI, listings, Range, ZIP bytes/CRC, exclusions, errors', flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-dir', required=True, type=Path)
    parser.add_argument('--prepare', action='store_true')
    parser.add_argument('--url')
    args = parser.parse_args()
    if args.prepare:
        prepare(args.data_dir)
    if args.url:
        check(args.url, args.data_dir)
