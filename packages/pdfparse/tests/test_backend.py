"""HTTP contracts, filesystem scope, one pipeline call and read-only settings."""
import time
from pathlib import Path
from threading import Event

import pytest
pytest.importorskip('fastapi')
from fastapi.testclient import TestClient
from docling_core.types.doc import BoundingBox, DocItemLabel, DoclingDocument, ProvenanceItem

from pdfparse import PdfParseResult, PdfParseConfiguration, PdfParsePipeline
from pdfparse.backend.app import create_app
from pdfparse.ingestion import BasePdfExtractor
from tests.test_layout_pipeline import make_pdf


class Extractor(BasePdfExtractor):
    def __init__(self):
        self.calls = 0
        self.fail = False
        self.gate = Event()
        self.gate.set()

    def extract(self, path):
        self.calls += 1
        self.gate.wait(timeout=5)
        if self.fail:
            raise RuntimeError('fixture extraction failed')
        doc = DoclingDocument(name='fixture')
        doc.add_text(label=DocItemLabel.TITLE, text='Guide PDF')
        doc.add_heading(text='Description', level=1)
        for page in (1, 2):
            doc.add_text(label=DocItemLabel.TEXT, text=f'Information de la page {page}', prov=ProvenanceItem(
                page_no=page, bbox=BoundingBox(l=20, t=350, r=220, b=320, coord_origin='BOTTOMLEFT'), charspan=(0, 26)))
        return doc


@pytest.fixture
def studio(tmp_path, monkeypatch):
    monkeypatch.setenv("ALLOWED_HOSTS", "localhost,127.0.0.1,testserver,*.lan")
    (tmp_path / 'nested').mkdir()
    make_pdf(tmp_path / 'nested' / 'source.PDF', pages=2)
    (tmp_path / 'ignored.txt').write_text('not a pdf')
    extractor = Extractor()
    engine = PdfParsePipeline(PdfParseConfiguration(cache=False), extractor=extractor)
    with TestClient(create_app(root=tmp_path, pipeline=engine, cache_capacity=1)) as client:
        yield client, tmp_path, extractor


def wait_ready(client, document_id):
    for _ in range(500):
        response = client.get(f'/api/documents/{document_id}').json()
        if response['status'] not in ('queued', 'processing'):
            return response
        time.sleep(.02)
    pytest.fail('Worker did not finish')


def test_markdown_preserves_headers_and_verbatim_text_while_chunks_filter_them(tmp_path):
    from types import SimpleNamespace
    from pdfparse.backend.presentation import result_view
    pdf = tmp_path / 'letter.pdf'
    pdf.write_bytes(b'letter source')
    doc = DoclingDocument(name='letter')
    doc.add_text(label=DocItemLabel.PAGE_HEADER, text='ACME — 1914')
    body = 'Dear Sir,\n  Amount: 12.50 €\nYours sincerely.'
    doc.add_text(label=DocItemLabel.TEXT, text=body)
    doc.add_text(label=DocItemLabel.PAGE_FOOTER, text='Page 1')
    engine = PdfParsePipeline(PdfParseConfiguration(cache=False))
    enriched = engine.enrich_document(pdf, doc)
    chunks = engine.chunk_document(enriched)
    view = result_view(engine, PdfParseResult(document=enriched, chunks=chunks))
    assert view.markdown == f'ACME — 1914\n\n{body}\n\nPage 1'
    assert '\n'.join(chunk.text for chunk in chunks) == body


def test_browse_pdf_pages_process_result_and_cache(studio):
    client, root, extractor = studio
    folders = client.get('/api/folders').json()
    assert folders['root'] == str(root) and folders['folders'][0]['name'] == 'nested'
    assert client.get('/api/documents', params={'recursive': False}).json()['documents'] == []
    document = client.get('/api/documents').json()['documents'][0]
    assert document['path'] == 'nested/source.PDF'
    url = f"/api/documents/{document['id']}"
    assert client.get(f'{url}/pdf').content.startswith(b'%PDF')
    ranged = client.get(f'{url}/pdf', headers={'Range': 'bytes=0-9'})
    assert ranged.status_code == 206 and len(ranged.content) == 10
    assert client.get(f'{url}/result').status_code == 409
    assert client.post(f'{url}/process').status_code == 202
    assert wait_ready(client, document['id'])['status'] == 'ready'
    result = client.get(f'{url}/result').json()
    assert result['markdown'].startswith('# Guide PDF\n\n## Description')
    assert result['items'][0]['data']['document_id'] == result['document_id']
    assert result['chunks'][0]['locations'][1]['page_no'] == 2
    assert result['chunks'][0]['source_refs']
    client.post(f'{url}/process')
    assert extractor.calls == 1
    settings = client.get('/api/settings').json()
    assert settings['read_only'] and settings['pipeline_class'] == 'pdfparse.pipeline.PdfParsePipeline'
    assert any(handler['class'].endswith('handle_table') for handler in settings['configuration']['enrich']['handlers'].values())
    assert client.patch('/api/settings', json={}).status_code == 405


def test_scope_symlinks_and_changed_pdf(studio, tmp_path):
    client, root, _ = studio
    assert client.get('/api/folders', params={'path': '..'}).status_code == 403
    assert client.get('/api/folders', params={'path': 'missing'}).status_code == 404
    external = root.parent / f'{root.name}-external.pdf'
    external.write_bytes(b'%PDF-external')
    (root / 'outside.pdf').symlink_to(external)
    docs = client.get('/api/documents').json()['documents']
    assert len(docs) == 1
    assert client.get('/api/documents/unknown/pdf').status_code == 404
    path = root / docs[0]['path']
    path.write_bytes(path.read_bytes() + b'\nchanged')
    assert client.get(f"/api/documents/{docs[0]['id']}/result").status_code == 409
    assert client.get('/api/documents').json()['documents'][0]['status'] == 'idle'


@pytest.mark.parametrize('hostname', ['studio.lan', 'pdf.dev.lan', 'localhost', '127.0.0.1'])
def test_local_and_lan_hosts_and_origins(studio, hostname):
    client, _, _ = studio
    response = client.get('/api/folders', headers={
        'Host': f'{hostname}:8000', 'Origin': f'http://{hostname}:8000',
    })
    assert response.status_code == 200


@pytest.mark.parametrize('hostname', ['lan', 'notlan', 'studio.lan.example', 'unrelated.example'])
def test_non_lan_hosts_are_rejected(studio, hostname):
    client, _, _ = studio
    assert client.get('/api/folders', headers={'Host': hostname}).status_code == 400


@pytest.mark.parametrize('origin', [
    'http://lan', 'http://notlan', 'https://studio.lan.example', 'null',
    'http://[invalid', 'ftp://studio.lan',
])
def test_non_lan_or_invalid_origins_are_rejected(studio, origin):
    client, _, _ = studio
    assert client.get('/api/folders', headers={'Host': 'studio.lan', 'Origin': origin}).status_code == 403

def test_lan_host_and_origin_allow_model_loading(studio):
    client, _, _ = studio
    headers = {'Host': 'gpu.lan:8100', 'Origin': 'http://gpu.lan:8100'}
    assert client.get('/api/folders', headers=headers).status_code == 200
    # An invalid model request reaches validation instead of being rejected by origin.
    assert client.post('/api/runtime', headers=headers, json={'unknown': True}).status_code == 422


def test_hosts_are_configured_from_environment(tmp_path, monkeypatch):
    monkeypatch.setenv('ALLOWED_HOSTS', 'studio.example.org,*.internal')
    with TestClient(create_app(root=tmp_path)) as client:
        for host in ('studio.example.org', 'gpu.internal'):
            assert client.get('/api/folders', headers={
                'Host': host, 'Origin': f'https://{host}:8443',
            }).status_code == 200
        assert client.get('/api/folders', headers={'Host': 'localhost'}).status_code == 400
        assert client.get('/api/folders', headers={
            'Host': 'studio.example.org', 'Origin': 'https://unrelated.example',
        }).status_code == 403


def test_unrestricted_hosts_when_environment_unset(tmp_path, monkeypatch):
    monkeypatch.delenv('ALLOWED_HOSTS', raising=False)
    with TestClient(create_app(root=tmp_path)) as client:
        assert client.get('/api/folders', headers={
            'Host': 'custom.example', 'Origin': 'https://custom.example',
        }).status_code == 200


def test_recursive_document_pagination_has_no_corpus_limit(tmp_path):
    nested = tmp_path / 'nested'
    nested.mkdir()
    for index in range(10005):
        (nested / f'{index:05}.PDF').touch()
    cache = nested / 'source.pdf.pdfparsecache'
    cache.mkdir()
    (cache / 'ignore.pdf').touch()
    versions = tmp_path / '_versions'
    versions.mkdir()
    (versions / 'archived.pdf').touch()
    with TestClient(create_app(root=tmp_path)) as client:
        folders = client.get('/api/folders').json()['folders']
        assert [folder['name'] for folder in folders] == ['nested']
        assert client.get('/api/documents', params={'folder': '_versions'}).status_code == 404
        first = client.get('/api/documents', params={'page_size': 200}).json()
        assert first['total'] == 10005
        assert first['page'] == 1 and len(first['documents']) == 200
        last = client.get('/api/documents', params={'page': 51, 'page_size': 200}).json()
        assert len(last['documents']) == 5
        assert last['documents'][-1]['name'] == '10004.PDF'
        assert client.get('/api/documents', params={'page': 999, 'page_size': 200}).json()['page'] == 51
        match = client.get('/api/documents', params={'search': '10004'}).json()
        assert match['total'] == 1 and len(match['documents']) == 1
        assert client.get('/api/documents', params={'recursive': False}).json()['total'] == 0
        assert client.get('/api/documents', params={'page': 0}).status_code == 422
        assert client.get('/api/documents', params={'page_size': 201}).status_code == 422


def test_worker_deduplicates_requests_and_exposes_failure(studio):
    client, _, extractor = studio
    document = client.get('/api/documents').json()['documents'][0]
    url = f"/api/documents/{document['id']}/process"
    extractor.fail = True
    extractor.gate.clear()
    client.post(url)
    client.post(url)
    extractor.gate.set()
    outcome = wait_ready(client, document['id'])
    assert outcome['status'] == 'error' and 'fixture extraction failed' in outcome['error']
    assert extractor.calls == 1
    job = wait_job(client, client.get('/api/jobs').json()[0]['id'])
    assert job['status'] == 'failed' and job['stage'] == 'extraction'
    assert job['error'] == 'fixture extraction failed'
    assert job['finished_at'] and job['elapsed_seconds'] >= 0
    extractor.fail = False
    client.post(url)
    assert wait_ready(client, document['id'])['status'] == 'ready'
    assert extractor.calls == 2


def wait_job(client, job_id):
    for _ in range(200):
        job = next(job for job in client.get('/api/jobs').json() if job['id'] == job_id)
        if job['status'] in ('succeeded', 'failed', 'cancelled'):
            return job
        time.sleep(.01)
    pytest.fail('Job did not finish')


def test_jobs_show_queue_deduplicate_and_keep_stage_history_after_eviction(studio):
    client, root, extractor = studio
    make_pdf(root / 'second.pdf')
    documents = client.get('/api/documents').json()['documents']
    extractor.gate.clear()
    try:
        for doc in documents:
            client.post(f"/api/documents/{doc['id']}/process")
        client.post(f"/api/documents/{documents[0]['id']}/process")
        jobs = client.get('/api/jobs').json()
        assert len(jobs) == 2
        assert {job['status'] for job in jobs} == {'running', 'queued'}
        running = next(job for job in jobs if job['status'] == 'running')
        assert running['stage'] == 'extraction'
        assert running['started_at'] and running['finished_at'] is None
        assert running['elapsed_seconds'] >= 0
    finally:
        extractor.gate.set()
    finished = [wait_job(client, job['id']) for job in jobs]
    assert all(job['status'] == 'succeeded' and job['finished_at'] for job in finished)
    assert {job['document_id'] for job in finished} == {doc['id'] for doc in documents}
    for job in finished:
        assert [event['stage'] for event in job['events']] == ['configuration', 'extraction', 'enrichment', 'chunking', 'rendering']
    # History survives result-cache eviction and duration stops advancing.
    assert client.get('/api/jobs').json()[0]['elapsed_seconds'] == finished[0]['elapsed_seconds']
    evicted = next(doc for doc in documents if client.get(f"/api/documents/{doc['id']}").json()['status'] == 'idle')
    assert client.get(f"/api/documents/{evicted['id']}/result").status_code == 409


def test_openapi_export_has_stable_operations_and_binary_pdf(studio):
    schema = studio[0].get('/openapi.json').json()
    operations = {operation['operationId'] for route in schema['paths'].values() for operation in route.values()}
    assert operations == {'listFolders', 'listDocuments', 'getDocument', 'getPdf', 'getAsset',
                          'processDocument', 'processFolder', 'getResult', 'getSettings',
                          'getRuntimeOptions', 'getRuntime', 'loadRuntime', 'unloadRuntime', 'listJobs',
                          'getSourceRoot', 'selectSourceRoot', 'stopFolderJob'}
    assert 'application/pdf' in schema['paths']['/api/documents/{document_id}/pdf']['get']['responses']['200']['content']
    assert 'contextualized_text' in schema['components']['schemas']['ChunkView']['properties']


def test_folder_job_runs_the_recursive_corpus_beyond_the_queue_limit(studio, monkeypatch):
    client, root, extractor = studio
    (root / 'nested' / 'archive').mkdir()
    for index in range(21):
        make_pdf(root / 'nested' / 'archive' / f'{index:02}.pdf')
    make_pdf(root / 'outside.pdf')
    extract = extractor.extract

    def extract_with_one_failure(path):
        document = extract(path)
        if path.name == '00.pdf':
            raise RuntimeError('fixture extraction failed')
        return document

    monkeypatch.setattr(extractor, 'extract', extract_with_one_failure)
    extractor.gate.clear()
    try:
        url = '/api/documents/process?folder=nested'
        response = client.post(url)
        assert response.status_code == 202
        batch = response.json()
        assert batch['kind'] == 'batch'
        assert client.post(url).json()['id'] == batch['id']
        assert client.delete('/api/runtime').status_code == 409
    finally:
        extractor.gate.set()

    finished = wait_job(client, batch['id'])
    assert finished['status'] == 'failed'
    assert '21/22 PDF analysés' in finished['detail']
    assert 'nested/archive/00.pdf' in finished['error']
    jobs = [job for job in client.get('/api/jobs').json() if job['kind'] == 'document']
    assert len(jobs) == extractor.calls == 22
    assert all(job['name'].startswith('nested/') for job in jobs)
    assert any(job['name'] == 'nested/source.PDF' and job['status'] == 'succeeded' for job in jobs)


def test_stop_extraction_finishes_current_pdf_without_claiming_another(studio, monkeypatch):
    client, root, extractor = studio
    make_pdf(root / 'second.pdf')
    started = Event()
    original = extractor.extract

    def extract(path):
        started.set()
        return original(path)

    monkeypatch.setattr(extractor, 'extract', extract)
    extractor.gate.clear()
    try:
        batch = client.post('/api/documents/process').json()
        assert started.wait(5)
        stop = client.post(f"/api/jobs/{batch['id']}/stop")
        assert stop.status_code == 200
        assert stop.json()['stop_requested'] is True
        assert client.post(f"/api/jobs/{batch['id']}/stop").json()['stop_requested'] is True
    finally:
        extractor.gate.set()

    finished = wait_job(client, batch['id'])
    assert finished['status'] == 'cancelled'
    assert '1/2 PDF analysés' in finished['detail']
    assert extractor.calls == 1
    jobs = [job for job in client.get('/api/jobs').json() if job['kind'] == 'document']
    assert [job['status'] for job in jobs] == ['succeeded']
    assert client.post(f"/api/jobs/{jobs[0]['id']}/stop").status_code == 404


def test_documents_and_extraction_are_ordered_by_ascending_byte_size(tmp_path, monkeypatch):
    monkeypatch.setenv("ALLOWED_HOSTS", "localhost,127.0.0.1,testserver,*.lan")
    (tmp_path / 'heavy.pdf').write_bytes(b'%PDF-1.4\n' + b'H' * 8000 + b'\n%%EOF')
    (tmp_path / 'tiny.pdf').write_bytes(b'%PDF-1.4\n' + b'T' * 200 + b'\n%%EOF')
    (tmp_path / 'medium.pdf').write_bytes(b'%PDF-1.4\n' + b'M' * 2500 + b'\n%%EOF')

    extracted_order = []
    class TrackingExtractor(BasePdfExtractor):
        def extract(self, path):
            extracted_order.append(path.name)
            doc = DoclingDocument(name='fixture')
            doc.add_text(label=DocItemLabel.TITLE, text=f'Extracted {path.name}')
            return doc

    from pdfparse.ingestion.structural import StructuralChunkingModel
    engine = PdfParsePipeline(PdfParseConfiguration(cache=False, chunking=StructuralChunkingModel()), extractor=TrackingExtractor())
    with TestClient(create_app(root=tmp_path, pipeline=engine)) as client:
        docs = client.get('/api/documents').json()['documents']
        assert [doc['name'] for doc in docs] == ['tiny.pdf', 'medium.pdf', 'heavy.pdf']
        assert [doc['size'] for doc in docs] == [
            len(b'%PDF-1.4\n' + b'T' * 200 + b'\n%%EOF'),
            len(b'%PDF-1.4\n' + b'M' * 2500 + b'\n%%EOF'),
            len(b'%PDF-1.4\n' + b'H' * 8000 + b'\n%%EOF'),
        ]

        response = client.post('/api/documents/process')
        assert response.status_code == 202
        finished = wait_job(client, response.json()['id'])
        assert finished['status'] == 'succeeded'
        assert extracted_order == ['tiny.pdf', 'medium.pdf', 'heavy.pdf']


def test_effective_default_options_are_visible_without_loading_weights():
    from pdfparse.backend.presentation import settings_view
    settings = settings_view(PdfParsePipeline(PdfParseConfiguration(cache=False)), 8)
    assert settings.extraction_options['options']['do_ocr'] is False
    assert settings.extraction_options['options']['generate_picture_images'] is True
    assert settings.configuration['chunking']['backend']['chunk_size'] == 512
    assert settings.codecs['pdfparse.form']['schema_version'] == 2


def test_picture_only_result_explains_disabled_enrichment(tmp_path):
    from PIL import Image
    from docling_core.types.doc import ImageRef

    class PictureExtractor(BasePdfExtractor):
        def extract(self, path):
            doc = DoclingDocument(name='picture only')
            doc.add_picture(image=ImageRef.from_pil(Image.new('RGB', (30, 40)), dpi=72))
            return doc

    make_pdf(tmp_path / 'picture.pdf')
    engine = PdfParsePipeline(PdfParseConfiguration(cache=False), extractor=PictureExtractor())
    with TestClient(create_app(root=tmp_path, pipeline=engine)) as client:
        doc = client.get('/api/documents').json()['documents'][0]
        client.post(f"/api/documents/{doc['id']}/process")
        assert wait_ready(client, doc['id'])['status'] == 'ready'
        result = client.get(f"/api/documents/{doc['id']}/result").json()
        assert result['markdown'] == '' and result['chunks'] == []
        assert any('sans classification ni OCR/VLM' in notice for notice in result['notices'])


@pytest.mark.parametrize("default_profile", [False, True])
def test_studio_cli_models_route_picture_into_markdown_and_chunks(tmp_path, monkeypatch, default_profile):
    from types import SimpleNamespace
    import pdfparse
    import sys
    import uvicorn
    from PIL import Image
    from docling_core.types.doc import ImageRef
    from pdfparse.models.ocr import BaseOcrModel
    from pdfparse.models.vlm import BaseVlmModel
    from tests.test_picture_routing import Classifier
    from pdfparse.backend.__main__ import main

    calls, loaded, captured = [], [], []

    class PictureExtractor(BasePdfExtractor):
        def extract(self, path):
            doc = DoclingDocument(name='routed picture')
            doc.add_picture(image=ImageRef.from_pil(Image.new('RGB', (30, 40)), dpi=72))
            return doc

    class Ocr(BaseOcrModel):
        def predict(self, image):
            calls.append(('ocr', image))
            return 'Pression 12 kPa'

    class Vlm(BaseVlmModel):
        def predict(self, image, *, prompt=None):
            if default_profile:
                assert 'français' in prompt and 'Pression 12 kPa' not in prompt
            else:
                assert 'Pression 12 kPa' in prompt and 'OCR text and a source caption' in prompt
            calls.append(('vlm', image))
            return 'Un graphique montrant la pression.'

    classifier = Classifier('line_chart')
    classifier.labels = ('line_chart', 'bar_chart', 'engineering_drawing', 'other')
    from tests.layout_detectors import FixtureDetector
    services = {'layout': FixtureDetector(model_path=tmp_path), 'picture_classifier': classifier,
                'ocr': Ocr(model_path=tmp_path), 'vlm': Vlm(model_path=tmp_path)}
    class Registry:
        def unload(self):
            pass
        def load(self, category, name, **kwargs):
            loaded.append((category, name, kwargs))
            return services[category]
        def get(self, category, name):
            return SimpleNamespace(source=SimpleNamespace(revision='a' * 40))
    monkeypatch.setattr(pdfparse, 'ModelRegistry', lambda: Registry())
    from pdfparse.backend import runtime as runtime_services
    monkeypatch.setattr(runtime_services, 'validate_selection', lambda *args: None)
    real_pipeline = PdfParsePipeline
    monkeypatch.setattr(pdfparse, 'PdfParsePipeline', lambda configuration: real_pipeline(configuration.model_copy(update={'cache_identity': {'fixture': 'cli-v1'}}), extractor=PictureExtractor()))
    monkeypatch.setattr(uvicorn, 'run', lambda app, **kwargs: captured.append(app))
    argv = ['pdfparse-studio', '--root', str(tmp_path), '--assets-dir', str(tmp_path / 'assets')]
    if not default_profile:
        argv += ['--picture-classifier', 'dfc_v25', '--ocr', 'paddleocr_vl_1_6', '--vlm', 'qwen3_vl_4b',
                 '--device', 'cuda:0', '--ocr-settings', '{"batch_size": 1}',
                 '--figure-mode', 'ocr_vlm', '--fallback-mode', 'ocr_vlm']
    monkeypatch.setattr(sys, 'argv', argv)
    make_pdf(tmp_path / 'figure.pdf')
    main()
    assert not loaded  # Loading waits for startup or an explicit UI selection.
    with TestClient(captured[0]) as client:
        from tests.test_backend_runtime import wait_runtime
        if default_profile:
            from pdfparse.backend.defaults import default_runtime_configuration
            assert client.get('/api/runtime').json()['status'] == 'unloaded'
            assert not loaded
            assert client.post('/api/runtime', json=default_runtime_configuration().model_dump(mode='json')).status_code == 202
        assert wait_runtime(client)['status'] == 'loaded'
        assert len(loaded) == 3
        assert {category: options['device'] for category, _, options in loaded} == {
            'picture_classifier': 'cpu' if default_profile else 'cuda:0', 'ocr': 'cuda:0', 'vlm': 'cuda:0',
        }
        if default_profile:
            assert {category: name for category, name, _ in loaded} == {
                'picture_classifier': 'dfc_v25', 'ocr': 'mineru2_5_pro_2605', 'vlm': 'qwen3_5_4b',
            }
            runtime = client.get('/api/runtime').json()
            assert runtime['configuration']['layout'] is None
            parameters = {category: options['settings'] for category, _, options in loaded}
            assert parameters['vlm']['quantization'] == 'nf4'
            assert parameters['vlm']['max_pixels'] == 262144
            assert parameters['vlm']['max_new_tokens'] == 1024
            assert parameters['ocr']['batch_size'] == 1
            assert parameters['ocr']['alphabet'] == 'latin'
            assert parameters['ocr']['reject_repetition'] is True
        doc = client.get('/api/documents').json()['documents'][0]
        client.post(f"/api/documents/{doc['id']}/process")
        assert wait_ready(client, doc['id'])['status'] == 'ready'
        result = client.get(f"/api/documents/{doc['id']}/result").json()
        assert ('Pression 12 kPa' in result['markdown']) is (not default_profile)
        assert 'Un graphique' in result['chunks'][0]['text']
        assert result['notices'] == []
        pic = next(item for item in result['items'] if item['type'] == 'picture')
        assert pic['classification']['route'] == 'line_chart'
        assert pic['classification']['status'] == 'enriched'
        assert pic['classification']['transformation_id'].startswith('studio.figure-routing.v10:')
    assert classifier.calls == 1
    assert [call[0] for call in calls] == (['vlm'] if default_profile else ['ocr', 'vlm'])
    if not default_profile:
        assert calls[0][1] is calls[1][1]


def test_studio_cli_rejects_incomplete_figure_configuration(monkeypatch):
    import sys
    from pdfparse.backend.__main__ import main
    monkeypatch.setattr(sys, 'argv', ['pdfparse-studio', '--ocr', 'paddleocr_vl_1_6'])
    with pytest.raises(SystemExit) as error:
        main()
    assert error.value.code == 2


@pytest.mark.parametrize('host', [None, '0.0.0.0'])
def test_studio_cli_bind_address(tmp_path, monkeypatch, host):
    import sys
    import uvicorn
    from pdfparse.backend import __main__ as cli

    app, calls = object(), []
    monkeypatch.setattr(cli, 'create_app', lambda **kwargs: app)
    monkeypatch.setattr(uvicorn, 'run', lambda application, **kwargs: calls.append((application, kwargs)))
    argv = ['pdfparse-studio', '--native', '--root', str(tmp_path), '--port', '8080']
    if host is not None:
        argv += ['--host', host]
    monkeypatch.setattr(sys, 'argv', argv)
    cli.main()
    assert calls == [(app, {'host': host or '127.0.0.1', 'port': 8080,'log_config':None,'proxy_headers':False})]


def test_studio_cli_native_mode_does_not_load_the_default_profile(tmp_path, monkeypatch):
    import sys
    import uvicorn
    from pdfparse.backend import runtime
    from pdfparse.backend.__main__ import main
    def forbidden(*args, **kwargs):
        pytest.fail('Native mode must not load OCR/VLM models')
    monkeypatch.setattr(runtime, 'build_pipeline', forbidden)
    captured = []
    monkeypatch.setattr(uvicorn, 'run', lambda app, **kwargs: captured.append(app))
    monkeypatch.setattr(sys, 'argv', ['pdfparse-studio', '--root', str(tmp_path), '--native'])
    main()
    with TestClient(captured[0]) as client:
        assert client.get('/api/runtime').json()['status'] == 'unloaded'


def test_missing_root_requires_explicit_choice_and_then_confines_browsing(tmp_path, monkeypatch):
    monkeypatch.delenv('PDFPARSE_SOURCE_ROOT', raising=False)
    root = tmp_path / 'pdfs'
    root.mkdir()
    make_pdf(root / 'example.pdf')
    with TestClient(create_app()) as client:
        assert client.get('/api/source').json() == {'root': None}
        assert client.get('/api/folders').status_code == 409
        assert client.get('/api/documents').status_code == 409
        assert client.post('/api/source', json={'path': '   '}).status_code == 422
        assert client.post('/api/source', json={'path': str(tmp_path / 'missing')}).status_code == 404
        assert client.post('/api/source', json={'path': str(root / 'example.pdf')}).status_code == 400
        assert client.get('/api/source').json()['root'] is None
        response = client.post('/api/source', json={'path': str(root)})
        assert response.status_code == 200 and response.json() == {'root': str(root)}
        assert client.get('/api/source').json() == {'root': str(root)}
        assert client.get('/api/folders').json()['root'] == str(root)
        assert client.get('/api/documents').json()['documents'][0]['name'] == 'example.pdf'
        assert client.get('/api/documents', params={'folder': '..'}).status_code == 403
        assert client.post('/api/source', json={'path': str(tmp_path)}).status_code == 409


@pytest.mark.parametrize('environment', [False, True])
def test_initial_source_root_is_used_without_requiring_a_browser_choice(tmp_path, monkeypatch, environment):
    monkeypatch.delenv('PDFPARSE_SOURCE_ROOT', raising=False)
    if environment:
        monkeypatch.setenv('PDFPARSE_SOURCE_ROOT', str(tmp_path))
    with TestClient(create_app(root=None if environment else tmp_path)) as client:
        assert client.get('/api/source').json() == {'root': str(tmp_path)}
        assert client.get('/api/folders').status_code == 200
        assert client.post('/api/source', json={'path': str(tmp_path)}).status_code == 409


@pytest.mark.parametrize('source', ['default', 'environment', 'argument'])
def test_studio_assets_directory_is_explicit_and_independent_of_pdf_root(tmp_path, monkeypatch, source):
    from pdfparse.backend.runtime import DEFAULT_ASSETS_DIR
    monkeypatch.delenv('PDFPARSE_ASSETS_DIR', raising=False)
    expected = DEFAULT_ASSETS_DIR
    kwargs = {}
    if source in ('environment', 'argument'):
        expected = tmp_path / source
        monkeypatch.setenv('PDFPARSE_ASSETS_DIR', str(tmp_path / 'environment'))
    if source == 'argument':
        kwargs['assets_dir'] = expected
    monkeypatch.chdir(tmp_path)
    with TestClient(create_app(**kwargs)) as client:
        store = client.get('/api/settings').json()['configuration']['asset_store']
        assert store['class'].endswith('FileSystemAssetStore')
        assert store['root'] == str(expected)
        assert not expected.exists() or expected == DEFAULT_ASSETS_DIR


def test_studio_native_crops_survive_restart_with_stable_references(tmp_path, monkeypatch):
    import pdfparse
    from tests.test_backend_runtime import FigureExtractor
    make_pdf(tmp_path / 'figure.pdf')
    real_pipeline = PdfParsePipeline
    monkeypatch.setattr(pdfparse, 'PdfParsePipeline', lambda config: real_pipeline(config.model_copy(update={'cache_identity': {'fixture': 'native-crops-v1'}}), extractor=FigureExtractor()))
    assets = tmp_path / 'assets'
    results = []
    for _ in range(2):
        with TestClient(create_app(root=tmp_path, assets_dir=assets)) as client:
            entry = client.get('/api/documents').json()['documents'][0]
            url = f"/api/documents/{entry['id']}"
            client.post(url + '/process')
            assert wait_ready(client, entry['id'])['status'] == 'ready'
            data = client.get(url + '/result').json()
            item = next(item for item in data['items'] if item['type'] == 'picture')
            results.append((data['document_id'], item['ref'], item['data']['asset_path']))
            assert client.get(data['assets'][0]['url']).content.startswith(b'\x89PNG')
    assert results[0] == results[1]
    assert not list(assets.glob('*.png'))
    assert len(list(tmp_path.glob('*.pdf.pdfparsecache/*/assets/*.png'))) == 1


def test_disk_purge_invalidates_http_result_and_recomputes_crops(tmp_path):
    from pdfparse.ingestion.cache import prune_cache
    from tests.test_backend_runtime import FigureExtractor

    class CountingExtractor(FigureExtractor):
        calls = 0

        def extract(self, path):
            self.calls += 1
            return super().extract(path)

    path = tmp_path / 'figure.pdf'
    make_pdf(path)
    extractor = CountingExtractor()
    engine = PdfParsePipeline(PdfParseConfiguration(cache_identity={'fixture': 'purge-v1'}), extractor=extractor)
    with TestClient(create_app(root=tmp_path, pipeline=engine)) as client:
        entry = client.get('/api/documents').json()['documents'][0]
        base = f"/api/documents/{entry['id']}"
        client.post(base + '/process')
        assert wait_ready(client, entry['id'])['status'] == 'ready'
        asset_url = client.get(base + '/result').json()['assets'][0]['url']
        assert client.get(asset_url).status_code == 200
        prune_cache(path, max_entries=0)
        assert not list(tmp_path.glob('*.pdf.pdfparsecache/*/assets/*.png'))
        assert client.get(base + '/result').status_code == 409
        assert client.get(asset_url).status_code == 409
        client.post(base + '/process')
        assert wait_ready(client, entry['id'])['status'] == 'ready'
        assert extractor.calls == 2
        assert client.get(asset_url).content.startswith(b'\x89PNG')


def test_asset_inventory_serves_only_bound_images_and_uses_conditional_cache(tmp_path):
    from PIL import Image
    from docling_core.types.doc import ImageRef
    from pdfparse.enrich import MemoryAssetStore
    class Images(BasePdfExtractor):
        def extract(self, path):
            doc = DoclingDocument(name='images without text')
            doc.add_picture(image=ImageRef.from_pil(Image.new('RGB', (12, 18), 'blue'), dpi=72))
            doc.add_picture()  # Missing crop is represented explicitly.
            return doc
    class Store(MemoryAssetStore):
        reads = 0
        def get(self, locator):
            self.reads += 1
            return super().get(locator)
    store = Store()
    make_pdf(tmp_path / 'images.pdf')
    engine = PdfParsePipeline(PdfParseConfiguration(cache=False, asset_store=store), extractor=Images())
    with TestClient(create_app(root=tmp_path, pipeline=engine)) as client:
        doc = client.get('/api/documents').json()['documents'][0]
        base = f"/api/documents/{doc['id']}"
        assert client.get(base + '/assets/unknown').status_code == 409
        client.post(base + '/process')
        assert wait_ready(client, doc['id'])['status'] == 'ready'
        result = client.get(base + '/result').json()
        assert result['chunks'] == [] and len(result['assets']) == 2
        image, missing = result['assets']
        assert image['available'] and not missing['available'] and missing['url'] is None
        response = client.get(image['url'])
        assert response.status_code == 200 and response.headers['content-type'] == 'image/png'
        assert response.content.startswith(b'\x89PNG') and store.reads == 1
        assert client.get(image['url'], headers={'If-None-Match': response.headers['etag']}).status_code == 304
        assert store.reads == 1
        assert client.get(base + '/assets/unknown').status_code == 404
        assert result['document_path'] == str(tmp_path / 'images.pdf')


def test_cached_results_invalidate_when_pdf_preserves_size_and_mtime(studio):
    import os
    client, root, extractor = studio
    document = client.get('/api/documents').json()['documents'][0]
    url = f"/api/documents/{document['id']}"
    client.post(url + '/process')
    assert wait_ready(client, document['id'])['status'] == 'ready'
    old = client.get(url + '/result').json()
    path = root / document['path']
    stat = path.stat()
    data = bytearray(path.read_bytes())
    data[-1] ^= 1
    path.write_bytes(data)
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    assert client.get(url + '/result').status_code == 409
    refreshed = client.get('/api/documents').json()['documents'][0]
    assert refreshed['status'] == 'idle' and refreshed['version'] != document['version']
    client.post(url + '/process')
    assert wait_ready(client, document['id'])['status'] == 'ready'
    assert client.get(url + '/result').json()['document_id'] != old['document_id']
    assert extractor.calls == 2


def test_http_cache_is_bounded_by_bytes_as_well_as_document_count(tmp_path):
    for name in ('a.pdf', 'b.pdf'):
        make_pdf(tmp_path / name)
    engine = PdfParsePipeline(PdfParseConfiguration(cache=False), extractor=Extractor())
    with TestClient(create_app(root=tmp_path, pipeline=engine)) as probe:
        first = probe.get('/api/documents').json()['documents'][0]
        probe.post(f"/api/documents/{first['id']}/process")
        assert wait_ready(probe, first['id'])['status'] == 'ready'
        size = probe.app.state.cache_info()['bytes']
    with TestClient(create_app(root=tmp_path, pipeline=engine, cache_capacity=8, cache_max_bytes=size+128)) as client:
        docs = client.get('/api/documents').json()['documents']
        for document in docs:
            client.post(f"/api/documents/{document['id']}/process")
            assert wait_ready(client, document['id'])['status'] == 'ready'
        stats = client.app.state.cache_info()
        assert stats['entries'] == 1 and stats['bytes'] <= stats['max_bytes']
        assert client.get(f"/api/documents/{docs[0]['id']}").json()['status'] == 'idle'
        assert client.get(f"/api/documents/{docs[1]['id']}/result").status_code == 200


def test_result_larger_than_http_budget_is_reported_without_retaining_payload(tmp_path):
    make_pdf(tmp_path / 'large.pdf')
    engine = PdfParsePipeline(PdfParseConfiguration(cache=False), extractor=Extractor())
    with TestClient(create_app(root=tmp_path, pipeline=engine, cache_max_bytes=1)) as client:
        document = client.get('/api/documents').json()['documents'][0]
        client.post(f"/api/documents/{document['id']}/process")
        status = wait_ready(client, document['id'])
        assert status['status'] == 'error' and 'budget' in status['error']
        assert client.app.state.cache_info()['bytes'] == 0


def test_existing_disk_cache_is_ready_and_readable_without_analysis(tmp_path, monkeypatch):
    from pdfparse.backend.schemas import RuntimeConfiguration
    from pdfparse.ingestion.cache import remember_runtime_configuration
    import shutil

    profile = RuntimeConfiguration()
    extractor = Extractor()
    engine = PdfParsePipeline(PdfParseConfiguration(cache_identity={'fixture': 'studio-cache'}), extractor=extractor)
    paths = [tmp_path / f'{index}.pdf' for index in range(2)]
    for path in paths:
        make_pdf(path, pages=2)
        engine.run(path)
        remember_runtime_configuration(path, engine, profile.model_dump(mode='json'))
    calls = extractor.calls
    # Opening a previously processed corpus must not run inference or extraction.
    monkeypatch.setattr(engine, 'run', lambda *args: pytest.fail('Unexpected analysis'))
    with TestClient(create_app(root=tmp_path, pipeline=engine, runtime_configuration=profile,
                               cache_capacity=1)) as client:
        documents = client.get('/api/documents').json()['documents']
        assert [doc['status'] for doc in documents] == ['ready', 'ready']
        for document in documents + documents[:1]:
            response = client.get(f"/api/documents/{document['id']}/result")
            assert response.status_code == 200
            assert 'Guide PDF' in response.json()['markdown']
        assert extractor.calls == calls
        # HTTP eviction must not turn a valid disk result grey.
        assert all(doc['status'] == 'ready' for doc in client.get('/api/documents').json()['documents'])
        shutil.rmtree(paths[1].with_name(paths[1].name + '.pdfparsecache'))
        assert client.get(f"/api/documents/{documents[1]['id']}").json()['status'] == 'idle'


@pytest.mark.parametrize('invalid', ['profile', 'pdf', 'corrupt'])
def test_disk_cache_must_match_profile_and_remain_valid(tmp_path, invalid):
    from pdfparse.backend.schemas import RuntimeConfiguration, StructuralChunking
    from pdfparse.ingestion.cache import remember_runtime_configuration

    profile = RuntimeConfiguration()
    path = tmp_path / 'source.pdf'
    make_pdf(path, pages=2)
    engine = PdfParsePipeline(PdfParseConfiguration(cache_identity={'fixture': 'studio-cache'}), extractor=Extractor())
    result = engine.run(path)
    remember_runtime_configuration(path, engine, profile.model_dump(mode='json'))
    if invalid == 'profile':
        profile = profile.model_copy(update={'chunking': StructuralChunking(chunk_size=123)})
    elif invalid == 'pdf':
        make_pdf(path, pages=3)
    else:
        (result.document.assets_dir.parent / 'chunks.json').write_text('{}')
    with TestClient(create_app(root=tmp_path, pipeline=engine, runtime_configuration=profile)) as client:
        document = client.get('/api/documents').json()['documents'][0]
        assert document['status'] == 'idle'
        assert client.get(f"/api/documents/{document['id']}/result").status_code == 409
