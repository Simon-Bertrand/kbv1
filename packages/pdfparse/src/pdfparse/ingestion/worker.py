"""Internal HTTP worker for immutable PDF selections submitted by ingestion."""
import asyncio
import json
import os
from pathlib import Path
import signal
import sys
import tempfile
from typing import Annotated

from anyio import CancelScope
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import Field, RootModel

from pdfparse.cli import selected_pdfs

app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
slot = asyncio.Lock()
Selection = RootModel[Annotated[dict[str, str], Field(max_length=100_000)]]


def extraction_command(root, manifest):
    return (str(Path(sys.executable).with_name('pdfparse-run')),
            '--data-dir', str(root), '--files-manifest', str(manifest))


async def stop(proc):
    try:
        os.killpg(proc.pid, signal.SIGINT)
    except ProcessLookupError:
        return
    try:
        await asyncio.wait_for(proc.wait(), 10)
    except TimeoutError:
        pass
    finally:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        await proc.wait()


@app.get('/health')
def health():
    return {'status': 'ok'}


@app.post('/extract')
async def extract(selection: Selection, request: Request):
    root = Path(os.environ.get('DATA_DIR', '/data')).resolve(strict=True)
    if slot.locked():
        raise HTTPException(409, 'PdfParse worker occupied')
    await slot.acquire()
    payload = json.dumps(selection.root).encode()
    try:
        with tempfile.TemporaryDirectory(prefix='pdfparse-selection-') as folder:
            manifest = Path(folder) / 'files.json'
            manifest.write_bytes(payload)
            try:
                await asyncio.to_thread(selected_pdfs, root, manifest)
            except (OSError, ValueError) as exc:
                raise HTTPException(422, 'Invalid archived PDF selection') from exc
    except BaseException:
        slot.release()
        raise

    async def stream():
        try:
            with tempfile.TemporaryDirectory(prefix='pdfparse-job-') as folder:
                manifest = Path(folder) / 'files.json'
                manifest.write_bytes(payload)
                proc = await asyncio.create_subprocess_exec(
                    *extraction_command(root, manifest),
                    start_new_session=True,
                )
                try:
                    while proc.returncode is None:
                        if await request.is_disconnected():
                            return
                        yield '\n'
                        await asyncio.sleep(1)
                    yield json.dumps({'exit_code': proc.returncode}) + '\n'
                finally:
                    with CancelScope(shield=True):
                        await stop(proc)
        finally:
            slot.release()

    return StreamingResponse(stream(), media_type='application/x-ndjson')
