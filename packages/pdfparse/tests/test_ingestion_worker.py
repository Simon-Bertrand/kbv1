import asyncio
import hashlib
import sys

import httpx
import pytest

from pdfparse.ingestion import worker


@pytest.mark.asyncio
async def test_worker_processes_only_archived_selection(tmp_path, monkeypatch):
    source = tmp_path / 'test.pdf'
    source.write_bytes(b'new source')
    digest = hashlib.sha256(b'snapshot').hexdigest()
    archive = tmp_path / 'test.pdf.pdfparsecache' / 'versions'
    archive.mkdir(parents=True)
    (archive / (digest + '.pdf')).write_bytes(b'snapshot')
    monkeypatch.setenv('DATA_DIR', str(tmp_path))
    monkeypatch.setattr(worker, 'slot', asyncio.Lock())

    def command(root, manifest):
        assert root == tmp_path
        script = 'import json, sys; assert json.load(open(sys.argv[1])) == {sys.argv[2]: sys.argv[3]}'
        return sys.executable, '-c', script, str(manifest), str(source), digest

    monkeypatch.setattr(worker, 'extraction_command', command)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=worker.app), base_url='http://worker') as client:
        response = await client.post('/extract', json={str(source): digest})
        assert response.status_code == 200
        assert response.text.strip() == '{"exit_code": 0}'
        response = await client.post('/extract', json={str(source): '0' * 64})
        assert response.status_code == 422
        assert not worker.slot.locked()


@pytest.mark.asyncio
async def test_worker_stops_child_if_ingestion_disconnects(tmp_path, monkeypatch):
    from contextlib import closing
    import os
    import socket

    import uvicorn

    pid_path = tmp_path / 'pid'
    monkeypatch.setenv('DATA_DIR', str(tmp_path))
    monkeypatch.setattr(worker, 'slot', asyncio.Lock())
    script = 'import os, pathlib, sys, time; pathlib.Path(sys.argv[1]).write_text(str(os.getpid())); time.sleep(60)'
    monkeypatch.setattr(worker, 'extraction_command',
                        lambda *_: (sys.executable, '-c', script, str(pid_path)))
    with closing(socket.socket()) as listener:
        listener.bind(('127.0.0.1', 0))
        port = listener.getsockname()[1]
        server = uvicorn.Server(uvicorn.Config(worker.app, log_level='error', lifespan='off'))
        server_task = asyncio.create_task(server.serve(sockets=[listener]))
        try:
            async with asyncio.timeout(8):
                while not server.started:
                    await asyncio.sleep(.01)
                async with httpx.AsyncClient(base_url=f'http://127.0.0.1:{port}', trust_env=False) as client:
                    async with client.stream('POST', '/extract', json={}) as response:
                        assert response.status_code == 200
                        while not pid_path.exists():
                            await asyncio.sleep(.01)
                        pid = int(pid_path.read_text())
                    while worker.slot.locked():
                        await asyncio.sleep(.01)
                    with pytest.raises(ProcessLookupError):
                        os.kill(pid, 0)
        finally:
            server.should_exit = True
            await server_task
