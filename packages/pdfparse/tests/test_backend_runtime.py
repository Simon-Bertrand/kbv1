"""Hot loading stays offline, serialized and observable, with real registry reuse."""
import gc
import time
import weakref
import subprocess
import sys
from threading import Event
from itertools import product

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from docling_core.types.doc import DoclingDocument, ImageRef

import pdfparse
from pdfparse import ModelRegistry, PdfParsePipeline
from pdfparse.models import ModelSource
from pdfparse.backend import runtime as services
from pdfparse.backend.app import create_app
from pdfparse.backend.schemas import DeviceOption
from pdfparse.ingestion import BasePdfExtractor
from pdfparse.models.picture_classifier.base import DocumentClassifierModel, CategoryScore, PictureClassification
from tests.layout_detectors import FixtureDetector
from tests.test_model_registry import FixtureOcr, FixtureVlm
from tests.test_backend import wait_ready, studio
from tests.test_layout_pipeline import make_pdf
from pdfparse.models.layout.catalog import MODELS as LAYOUT_MODELS
from pdfparse.models.picture_classifier.catalog import MODELS as DFC_MODELS
from pdfparse.models.ocr.catalog import MODELS as OCR_MODELS
from pdfparse.models.vlm.catalog import MODELS as VLM_MODELS


class FigureClassifier(DocumentClassifierModel):
    labels = ('line_chart', 'other')

    def _predict_batch(self, images):
        return [PictureClassification('fixture.dfc', (CategoryScore('line_chart', 1.0),)) for _ in images]


class FigureExtractor(BasePdfExtractor):
    def extract(self, path):
        document = DoclingDocument(name='fixture')
        document.add_picture(image=ImageRef.from_pil(Image.new('RGB', (40, 30)), dpi=72))
        return document


def wait_runtime(client):
    for _ in range(200):
        data = client.get('/api/runtime').json()
        if data['status'] not in ('loading', 'unloading'):
            return data
        time.sleep(.01)
    pytest.fail('Runtime operation did not finish')


@pytest.fixture
def runtime_studio(tmp_path, monkeypatch):
    source = tmp_path / 'weights'
    source.mkdir()
    (source / 'weights.bin').write_bytes(b'local fixture')
    models = ModelRegistry(tmp_path, builtins=False)
    for category, implementation in [('layout', FixtureDetector), ('picture_classifier', FigureClassifier),
                                      ('ocr', FixtureOcr), ('vlm', FixtureVlm)]:
        models.register(category, 'fixture', implementation, ModelSource(local_path=source, files=('weights.bin',)))
        models.download(category, 'fixture')
    models.register('ocr', 'missing', FixtureOcr, ModelSource(local_path=tmp_path / 'missing', files=('weights.bin',)))
    root = tmp_path / 'pdfs'
    root.mkdir()
    make_pdf(root / 'figure.pdf')
    monkeypatch.setattr(services, 'available_devices', lambda: [DeviceOption(id='cpu', label='CPU'),
                                                              DeviceOption(id='cuda:1', label='Fixture GPU')])
    monkeypatch.setattr(pdfparse, 'PdfParsePipeline', lambda config, **kwargs: PdfParsePipeline(config.model_copy(update={'cache_identity': {'fixture': 'runtime-v1'}}), extractor=kwargs.get('extractor') or FigureExtractor()))
    with TestClient(create_app(root=root, models=models, assets_dir=tmp_path / 'assets')) as client:
        yield client, models


def selection():
    return {**{category: {'name': 'fixture', 'device': 'cuda:1' if category == 'vlm' else 'cpu'}
               for category in ('layout', 'picture_classifier', 'ocr', 'vlm')}, 'chunking': {'kind': 'token', 'chunk_size': 80},
                'enrichment': {'page_vlm': True, 'text_ocr': True}}


def test_load_route_process_invalidate_and_unload(runtime_studio):
    client, models = runtime_studio
    options = client.get('/api/runtime/options').json()
    assert len(options['models']) == 5
    assert not next(model for model in options['models'] if model['name'] == 'missing')['prepared']
    assert models._instances == {}  # Listing never initializes a model.
    response = client.post('/api/runtime', json=selection())
    assert response.status_code == 202 and response.json()['status'] == 'loading'
    loaded = wait_runtime(client)
    assert loaded['status'] == 'loaded' and loaded['configuration']['vlm']['device'] == 'cuda:1'
    from tests.test_backend import wait_job
    loading_job = wait_job(client, client.get('/api/jobs').json()[0]['id'])
    assert loading_job['status'] == 'succeeded'
    assert [event['detail'].split(' / ')[0] for event in loading_job['events'] if event['stage'] == 'loading'] == [
        'layout', 'picture_classifier', 'ocr', 'vlm']
    assert 'cuda:1' in loading_job['events'][-1]['detail']
    refs = [weakref.ref(instance) for instance in models._instances.values()]
    settings = client.get('/api/settings').json()
    assert settings['configuration']['picture_enrich']['classifier']['device'] == 'cpu'
    assert settings['configuration']['chunking']['backend']['chunk_size'] == 80
    doc = client.get('/api/documents').json()['documents'][0]
    url = f"/api/documents/{doc['id']}"
    client.post(url + '/process')
    assert wait_ready(client, doc['id'])['status'] == 'ready'
    document_job = wait_job(client, client.get('/api/jobs').json()[0]['id'])
    stages = [event['stage'] for event in document_job['events']]
    assert stages == ['configuration', 'extraction', 'enrichment', 'classification', 'ocr', 'vlm', 'chunking', 'rendering']
    result = client.get(url + '/result').json()
    figure = next(item for item in result['items'] if item['type'] == 'picture')
    asset_root = settings['configuration']['asset_store']['root']
    from pathlib import Path
    pdf_path = Path(result['document_path'])
    stored_crop, = pdf_path.with_name(pdf_path.name + '.pdfparsecache').glob(
        '*/assets/' + figure['data']['asset_path'])
    assert stored_crop.read_bytes().startswith(b'\x89PNG')
    assert 'recognized:40:cpu' in result['markdown'] and result['chunks']
    assert next(item for item in result['items'] if item['type'] == 'picture')['classification']['status'] == 'enriched'
    assert client.delete('/api/runtime').status_code == 202
    assert wait_runtime(client)['status'] == 'unloaded'
    unloading_job = wait_job(client, client.get('/api/jobs').json()[0]['id'])
    assert unloading_job['status'] == 'succeeded' and unloading_job['stage'] == 'unloading'
    assert client.get(url).json()['status'] == 'idle'
    assert client.get(url + '/result').status_code == 409
    assert models._instances == {}
    assert stored_crop.is_file()  # Unloading models must not remove image evidence.
    assert client.get('/api/settings').json()['configuration']['asset_store']['root'] == asset_root
    gc.collect()
    assert all(ref() is None for ref in refs)
    assert client.get('/api/settings').json()['configuration']['picture_enrich']['class'].endswith('WrappingPictureEnrichModel')
    assert models.is_prepared('ocr', 'fixture')  # Unloading retains local weights.


def test_initial_profile_failure_keeps_ui_and_jobs_available(tmp_path, monkeypatch):
    from pdfparse.backend.schemas import RuntimeConfiguration
    from tests.test_backend import wait_job
    def invalid_profile(*args):
        raise ValueError('Poids du profil initial manquants')
    monkeypatch.setattr(services, 'validate_selection', invalid_profile)
    with TestClient(create_app(root=tmp_path, runtime_configuration=RuntimeConfiguration())) as client:
        assert wait_runtime(client)['status'] == 'error'
        job = wait_job(client, client.get('/api/jobs').json()[0]['id'])
        assert job['kind'] == 'runtime' and job['status'] == 'failed'
        assert job['stage'] == 'configuration' and job['error'] == 'Poids du profil initial manquants'
        assert client.get('/openapi.json').status_code == 200


def test_studio_falls_back_to_full_crop_ocr_vlm_for_uncertain_dfc(runtime_studio, monkeypatch):
    client, _ = runtime_studio
    monkeypatch.setattr(FigureClassifier, '_predict_batch', lambda self, images: [
        PictureClassification('fixture.dfc', (CategoryScore('line_chart', .8), CategoryScore('other', .2)))
        for _ in images])
    client.post('/api/runtime', json=selection())
    assert wait_runtime(client)['status'] == 'loaded'
    doc = client.get('/api/documents').json()['documents'][0]
    client.post(f"/api/documents/{doc['id']}/process")
    assert wait_ready(client, doc['id'])['status'] == 'ready'
    result = client.get(f"/api/documents/{doc['id']}/result").json()
    pic = next(item for item in result['items'] if item['type'] == 'picture')
    assert pic['classification']['route'] == 'fallback'
    assert not pic['classification']['trusted']
    assert 'recognized:40:cpu' in result['markdown'] and result['chunks']
    from tests.test_backend import wait_job
    job = wait_job(client, client.get('/api/jobs').json()[0]['id'])
    assert any('Repli générique' in (event['detail'] or '') for event in job['events'])


def test_studio_folder_extracts_another_pdf_while_vlm_is_running(runtime_studio, monkeypatch):
    from functools import wraps
    from pathlib import Path
    from docling_core.types.doc import DocItemLabel
    from tests.test_backend import wait_job

    client, models = runtime_studio
    root = Path(client.get('/api/source').json()['root'])
    make_pdf(root / 'plain.pdf', pages=2)
    vlm_started, release_vlm = Event(), Event()
    extract, predict = FigureExtractor.extract, FixtureVlm.predict

    def extract_document(self, path):
        if path.name == 'figure.pdf':
            return extract(self, path)
        assert vlm_started.wait(timeout=5)
        document = DoclingDocument(name='plain')
        document.add_text(label=DocItemLabel.TEXT, text='Texte sans figure')
        return document

    @wraps(predict)
    def predict_figure(self, image, *, prompt='Describe this image.'):
        vlm_started.set()
        assert release_vlm.wait(timeout=10)
        return predict(self, image, prompt=prompt)

    monkeypatch.setattr(FigureExtractor, 'extract', extract_document)
    monkeypatch.setattr(FixtureVlm, 'predict', predict_figure)
    profile = selection() | {'enrichment': {'text_ocr': True}}
    assert client.post('/api/runtime', json=profile).status_code == 202
    assert wait_runtime(client)['status'] == 'loaded'
    loaded_models = dict(models._instances)
    documents = {doc['name']: doc['id'] for doc in client.get('/api/documents').json()['documents']}

    response = client.post('/api/documents/process')
    assert response.status_code == 202
    batch_id = response.json()['id']
    try:
        assert vlm_started.wait(timeout=5), [
            (job['name'], job['status'], job['stage'], job['error']) for job in client.get('/api/jobs').json()
        ]
        assert wait_ready(client, documents['plain.pdf'])['status'] == 'ready'
        jobs = client.get('/api/jobs').json()
        figure_job = next(job for job in jobs if job['document_id'] == documents['figure.pdf'])
        assert figure_job['status'] == 'running' and figure_job['stage'] == 'vlm'
        assert client.delete('/api/runtime').status_code == 409
    finally:
        release_vlm.set()

    assert wait_job(client, batch_id)['status'] == 'succeeded'
    assert wait_ready(client, documents['figure.pdf'])['status'] == 'ready'
    assert models._instances == loaded_models
    repeat = client.post('/api/documents/process')
    assert '2 déjà analysés' in wait_job(client, repeat.json()['id'])['detail']


@pytest.mark.parametrize('update', [
    {'ocr': None}, {'vlm': {'name': 'unknown'}}, {'ocr': {'name': 'missing'}},
    {'vlm': {'name': 'fixture', 'device': 'cuda:7'}}, {'chunking': {'kind': 'token', 'chunk_size': True}},
    {'vlm': {'name': 'fixture', 'device': 'cpu', 'settings': {'quantization': 'nf4'}}},
])
def test_invalid_selection_preserves_active_models(runtime_studio, update):
    client, models = runtime_studio
    client.post('/api/runtime', json=selection())
    current = wait_runtime(client)
    refs = tuple(models._instances.values())
    assert client.post('/api/runtime', json=selection() | update).status_code == 422
    assert client.get('/api/runtime').json() == current
    assert tuple(models._instances.values()) == refs


def test_loading_is_serialized_and_errors_release_partial_models(runtime_studio, monkeypatch):
    client, models = runtime_studio
    started, finish = Event(), Event()
    partial = []
    def fail_after_load(registry, config, *, asset_store):
        partial.append(weakref.ref(registry.load('ocr', 'fixture')))
        started.set()
        finish.wait(timeout=5)
        raise RuntimeError('fixture allocation failed')
    monkeypatch.setattr(services, 'build_pipeline', fail_after_load)
    client.post('/api/runtime', json=selection())
    assert started.wait(timeout=2)
    try:
        doc = client.get('/api/documents').json()['documents'][0]
        assert client.post(f"/api/documents/{doc['id']}/process").status_code == 409
        assert client.delete('/api/runtime').status_code == 409
        assert client.post('/api/runtime', json=selection()).status_code == 409
        assert client.get('/api/settings').status_code == 409
        assert client.get('/api/runtime').json()['status'] == 'loading'
    finally:
        finish.set()
    failed = wait_runtime(client)
    assert failed['status'] == 'error' and failed['error'] == 'fixture allocation failed'
    from tests.test_backend import wait_job
    job = wait_job(client, client.get('/api/jobs').json()[0]['id'])
    assert job['status'] == 'failed' and job['error'] == failed['error']
    assert models._instances == {}
    gc.collect()
    assert partial[0]() is None
    assert client.post(f"/api/documents/{doc['id']}/process").status_code == 409
    client.delete('/api/runtime')
    assert wait_runtime(client)['status'] == 'unloaded'


def test_processing_blocks_reconfiguration_even_if_document_changes(studio):
    client, root, extractor = studio
    doc = client.get('/api/documents').json()['documents'][0]
    extractor.gate.clear()
    try:
        client.post(f"/api/documents/{doc['id']}/process")
        path = root / doc['path']
        path.write_bytes(path.read_bytes() + b'\nchanged')
        client.get('/api/documents')
        assert client.delete('/api/runtime').status_code == 409
        assert client.post('/api/runtime', json={}).status_code == 409
    finally:
        extractor.gate.set()


def test_first_lazy_pipeline_can_be_collected_in_a_fresh_process(tmp_path):
    # Docling retains optional-backend import exceptions. Importing it from a
    # frame that later holds a pipeline must not keep that pipeline alive.
    subprocess.run([sys.executable, '-c', '''
import gc, time, weakref, sys
from fastapi.testclient import TestClient
from pdfparse.backend.app import create_app
from pdfparse.backend import runtime
from pdfparse.backend.schemas import DeviceOption
from pdfparse import ModelRegistry
runtime.available_devices = lambda: [DeviceOption(id='cpu', label='CPU')]
with TestClient(create_app(root=sys.argv[1], models=ModelRegistry(sys.argv[1], builtins=False)), headers={'Authorization':'Basic '+__import__('base64').b64encode(b'test-admin:test-password-long-enough').decode()}) as client:
    assert client.post('/api/runtime', json={}).status_code == 202
    for _ in range(200):
        if client.get('/api/runtime').json()['status'] != 'loading': break
        time.sleep(.01)
    assert client.get('/api/runtime').json()['status'] == 'loaded'
    references = [weakref.ref(obj) for obj in gc.get_objects() if type(obj).__name__ == 'PdfParsePipeline']
    assert len(references) == 1
    assert client.delete('/api/runtime').status_code == 202
    for _ in range(200):
        if client.get('/api/runtime').json()['status'] != 'unloading': break
        time.sleep(.01)
    gc.collect()
    assert references[0]() is None, 'The unloaded pipeline is still retained by a worker frame'
''', str(tmp_path)], check=True, timeout=60)


def test_chunking_change_reuses_services_and_unload_preserves_chunking(runtime_studio, monkeypatch):
    client, models = runtime_studio
    client.post('/api/runtime', json=selection())
    assert wait_runtime(client)['status'] == 'loaded'
    instances = dict(models._instances)
    def no_reload(*args, **kwargs):
        pytest.fail('A chunking-only change must reuse already loaded models')
    monkeypatch.setattr(models, 'load', no_reload)
    config = selection() | {'chunking': {'kind': 'sentence', 'chunk_size': 96, 'chunk_overlap': 12,
                                       'min_sentences_per_chunk': 2}}
    assert client.post('/api/runtime', json=config).status_code == 202
    assert wait_runtime(client)['status'] == 'loaded'
    assert models._instances == instances
    effective = client.get('/api/settings').json()['configuration']['chunking']['backend']
    assert effective['class'].endswith('SentenceChunker')
    assert effective['chunk_overlap'] == 12 and effective['min_sentences_per_chunk'] == 2
    client.delete('/api/runtime')
    assert wait_runtime(client)['configuration']['chunking']['kind'] == 'sentence'
    effective = client.get('/api/settings').json()['configuration']['chunking']['backend']
    assert effective['class'].endswith('SentenceChunker') and effective['chunk_size'] == 96


@pytest.mark.parametrize('chunking', [
    {'kind': 'unknown'}, {'kind': 'token', 'chunk_size': 80, 'chunk_overlap': 80},
    {'kind': 'recursive', 'chunk_overlap': 1}, {'kind': 'recursive', 'min_characters_per_chunk': 0},
    {'kind': 'sentence', 'min_sentences_per_chunk': False},
    {'kind': 'sentence', 'min_characters_per_sentence': '12'},
])
def test_invalid_chunking_never_starts_a_reconfiguration(runtime_studio, chunking):
    client, models = runtime_studio
    before = client.get('/api/runtime').json()
    assert client.post('/api/runtime', json={'chunking': chunking}).status_code == 422
    assert client.get('/api/runtime').json() == before and not models._instances


@pytest.mark.parametrize('options', [
    {'kind': 'token', 'chunk_size': 35, 'chunk_overlap': 12},
    {'kind': 'sentence', 'chunk_size': 50, 'chunk_overlap': 25, 'min_characters_per_sentence': 1},
    {'kind': 'recursive', 'chunk_size': 40, 'min_characters_per_chunk': 1},
])
def test_selected_chunkers_preserve_unicode_offsets_and_multipage_sources(tmp_path, options):
    from docling_core.types.doc import BoundingBox, DocItemLabel, ProvenanceItem
    from pdfparse.backend.schemas import RuntimeConfiguration
    from pdfparse import PdfParseConfiguration
    source = tmp_path / 'source.pdf'
    source.write_bytes(b'fixture identity')
    doc = DoclingDocument(name='repeated Unicode')
    text = '  Été 😀. Répété.\n\nÉté 😀. Répété ! Fin.  '
    items = [doc.add_text(label=DocItemLabel.TEXT, text=text, prov=ProvenanceItem(
        page_no=page, bbox=BoundingBox(l=10, t=30, r=40, b=10), charspan=(0, len(text))))
        for page in (1, 2, 3)]
    model = services.build_chunking(RuntimeConfiguration.model_validate({'chunking': options}).chunking)
    pipeline = PdfParsePipeline(PdfParseConfiguration(cache=False, chunking=model), extractor=FigureExtractor())
    document = pipeline.enrich_document(source, doc)
    joined = '\n\n'.join([text] * 3)
    native = [part for part in model.backend.chunk(joined) if part.text.strip()]
    chunks = pipeline.chunk_document(document)
    assert chunks and len(chunks) == len(native)
    for chunk, part in zip(chunks, native):
        assert chunk.text == joined[part.start_index:part.end_index]
        indices = [i for i in range(3) if i * (len(text) + 2) < part.end_index
                   and i * (len(text) + 2) + len(text) > part.start_index]
        assert [ref.cref for ref in chunk.source_refs] == [items[i].self_ref for i in indices]
        assert {location.page_no for location in chunk.locations} == {i + 1 for i in indices}
    assert pipeline.chunk_document(document) == chunks
    different = services.build_chunking(RuntimeConfiguration.model_validate({'chunking': options | {'chunk_size': 64}}).chunking)
    assert different.transformation_id != model.transformation_id


# Exhaustive composition matrix; real engine inference has separate opt-in tests.

_COMPOSITIONS = [
    (layout, dfc, ocr, vlm, kind)
    for layout, (dfc, ocr, vlm), kind in product(
        (None, *LAYOUT_MODELS),
        [(None, None, None), *product(DFC_MODELS, OCR_MODELS, VLM_MODELS)],
        ('token', 'sentence', 'recursive', 'structural'),
    )
]


@pytest.fixture
def composition_source(tmp_path):
    from docling_core.types.doc import DocItemLabel
    path = tmp_path / 'composition.pdf'
    path.write_bytes(b'Composition matrix source identity')
    doc = DoclingDocument(name='composition')
    doc.add_text(label=DocItemLabel.SECTION_HEADER, text='Section principale')
    for width in (20, 21, 22):
        pic = doc.add_picture(image=ImageRef.from_pil(Image.new('RGB', (width, 30)), dpi=72))
        if width == 21:
            doc.add_text(label=DocItemLabel.TEXT, text='Transcription native unique', parent=pic)
    return path, doc


@pytest.mark.parametrize('layout,dfc,ocr,vlm,kind', _COMPOSITIONS)
def test_all_builtin_model_and_chunking_compositions(monkeypatch, tmp_path, composition_source,
                                                     layout, dfc, ocr, vlm, kind):
    from docling.datamodel.base_models import InputFormat
    from pdfparse.models.base import BaseInferenceModel
    from pdfparse.models.ocr import BaseSpatialOcrModel, BaseTableOcrModel
    from pdfparse.models.layout.options import NativeOcrLayoutOptions, DoclingLayoutOptions, SpatialTableOptions
    from pdfparse.ingestion.text import prepare_text
    from pdfparse.backend.schemas import RuntimeConfiguration
    from pdfparse.enrich import EnrichedPictureItem

    registry = ModelRegistry(tmp_path)
    calls, loaded = [], {}
    def load(category, name, *, device, settings):
        spec = registry.get(category, name)
        model = object.__new__(spec.implementation)
        BaseInferenceModel.__init__(model, model_path=tmp_path, device=device, settings=settings)
        if category == 'picture_classifier':
            model.labels = ('chart', 'other')
            def classify(images):
                return [PictureClassification(name, (CategoryScore('chart', .5), CategoryScore('other', .5))
                        if image.width == 22 else (CategoryScore('chart', 1.),)) for image in images]
            model._predict_batch = classify
        elif category == 'ocr':
            def recognize(image):
                calls.append(('ocr', image.width))
                return f'Transcription OCR largeur {image.width}'
            model.predict = recognize
        elif category == 'vlm':
            def describe(image, *, prompt):
                calls.append(('vlm', image.width))
                return f'Description visuelle largeur {image.width}.'
            model.predict = describe
        loaded[category] = model
        return model
    monkeypatch.setattr(registry, 'load', load)
    configuration = RuntimeConfiguration.model_validate({
        **({'layout': {'name': layout}} if layout else {}),
        **({category: {'name': name} for category, name in
            [('picture_classifier', dfc), ('ocr', ocr), ('vlm', vlm)]} if dfc else {}),
        'chunking': {'kind': kind, 'chunk_size': 512},
        'enrichment': {'page_vlm': bool(dfc), 'text_ocr': bool(dfc)},
    })
    pipeline = services.build_pipeline(registry, configuration,
                                       asset_store=pdfparse.enrich.FileSystemAssetStore(tmp_path / 'assets'))
    options = pipeline.extractor._converter.format_to_options[InputFormat.PDF].pipeline_options
    assert not options.do_ocr
    if dfc:
        assert options.generate_page_images
        assert pipeline.configuration.page_enrich.vlm is loaded['vlm']
        assert options.layout_options.text_ocr is loaded['ocr']
        assert isinstance(options.layout_options, DoclingLayoutOptions if layout else NativeOcrLayoutOptions)
        assert options.model_copy(deep=True).layout_options.text_ocr is loaded['ocr']
        if isinstance(loaded['ocr'], (BaseSpatialOcrModel, BaseTableOcrModel)):
            assert isinstance(options.table_structure_options, SpatialTableOptions)
        if isinstance(loaded['ocr'], BaseTableOcrModel):
            assert options.table_structure_options.table_ocr is loaded['ocr']
    if layout:
        assert options.layout_options.detector_instance is loaded['layout']
    document = pipeline.enrich_document(*composition_source)
    assert document.to_markdown().count('Transcription native unique') == 1
    canonical = prepare_text(document).text
    assert canonical.count('Transcription native unique') == 1
    if dfc:
        assert calls == [('ocr', 20), ('vlm', 20), ('vlm', 21), ('ocr', 22), ('vlm', 22)]
        assert canonical.count('Transcription OCR largeur 20') == 1
        assert canonical.count('Transcription OCR largeur 22') == 1
        pictures = [item for item in document.elements.values() if isinstance(item, EnrichedPictureItem)]
        assert [item.classification['route'] for item in pictures] == ['chart', 'chart', 'fallback']
    else:
        assert not calls
    chunks = pipeline.chunk_document(document)
    assert chunks and len({c.chunk_id for c in chunks}) == len(chunks)
    restored = pipeline.codec.from_dict(pipeline.codec.to_dict(document))
    assert pipeline.chunk_document(restored) == chunks


def test_reapplying_configuration_restores_cached_document_status(runtime_studio):
    client, models = runtime_studio
    profile = selection()
    client.post('/api/runtime', json=profile)
    assert wait_runtime(client)['status'] == 'loaded'
    document = client.get('/api/documents').json()['documents'][0]
    url = f"/api/documents/{document['id']}"
    client.post(url + '/process')
    assert wait_ready(client, document['id'])['status'] == 'ready'
    expected = client.get(url + '/result').json()
    changed = {**profile, 'chunking': {'kind': 'token', 'chunk_size': 160}}
    client.post('/api/runtime', json=changed)
    assert wait_runtime(client)['status'] == 'loaded'
    assert client.get('/api/documents').json()['documents'][0]['status'] == 'idle'
    assert client.get(url + '/result').status_code == 409
    client.post('/api/runtime', json=profile)
    assert wait_runtime(client)['status'] == 'loaded'
    assert client.get('/api/documents').json()['documents'][0]['status'] == 'ready'
    assert client.get(url + '/result').json() == expected
