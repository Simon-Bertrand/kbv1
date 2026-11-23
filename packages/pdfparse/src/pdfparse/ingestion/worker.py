"""Internal HTTP worker for immutable PDF selections submitted by ingestion."""
import asyncio
import codecs
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

    include_logs = request.headers.get("x-ingestion-log-stream") == "1"

    async def stream():
        try:
            with tempfile.TemporaryDirectory(prefix='pdfparse-job-') as folder:
                manifest = Path(folder) / 'files.json'
                manifest.write_bytes(payload)
                proc = await asyncio.create_subprocess_exec(
                    *extraction_command(root, manifest),
                    start_new_session=True,
                    env={**os.environ, "PYTHONUNBUFFERED": "1"} if include_logs else None,
                    stdout=asyncio.subprocess.PIPE if include_logs else None,
                    stderr=asyncio.subprocess.STDOUT if include_logs else None,
                )
                try:
                    if proc.stdout is None:
                        while proc.returncode is None:
                            yield '\n'
                            await asyncio.sleep(1)
                    else:
                        # Keep a single pending read across heartbeats and drain output before the exit frame.
                        decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
                        read = asyncio.create_task(proc.stdout.read(4096))
                        try:
                            while True:
                                done, _ = await asyncio.wait((read,), timeout=1)
                                if not done:
                                    yield '\n'
                                    continue
                                chunk = read.result()
                                message = decoder.decode(chunk, final=not chunk)
                                if message:
                                    print(message, end="", flush=True)
                                    yield json.dumps({"log": message}) + '\n'
                                if not chunk:
                                    break
                                read = asyncio.create_task(proc.stdout.read(4096))
                        finally:
                            read.cancel()
                            await asyncio.gather(read, return_exceptions=True)
                    await proc.wait()
                    yield json.dumps({'exit_code': proc.returncode}) + '\n'
                finally:
                    with CancelScope(shield=True):
                        if proc.stdout is None:
                            await stop(proc)
                        else:
                            await asyncio.gather(stop(proc), proc.stdout.read())
        finally:
            slot.release()

    return StreamingResponse(stream(), media_type='application/x-ndjson')
