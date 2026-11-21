"""Persistent sidecars preserve complete results without repeating inference."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import threading

from docling_core.types.doc import DocItemLabel, DoclingDocument, ImageRef, ProvenanceItem, BoundingBox
from PIL import Image
import pytest

from pdfparse import PdfParseConfiguration, PdfParsePipeline, PdfParseResult, StructuralChunkingModel, NativeLayoutModel
from pdfparse.enrich import FileSystemAssetStore, EnrichedPictureItem
from pdfparse.ingestion import BasePdfExtractor
from pdfparse.ingestion.preparation import cache_configuration
from pdfparse.ingestion.contracts import ExtractionResult, ExtractionRegion


class FixtureExtractor(BasePdfExtractor):
    def __init__(self, *, fail=False, empty=False):
        self.calls, self.fail, self.empty = 0, fail, empty

    def extract(self, pdfpath):
        self.calls += 1
        if self.fail:
            raise AssertionError('Extraction must not run on a cache hit')
        document = DoclingDocument(name='cache-fixture')
        if not self.empty:
            document.add_text(label=DocItemLabel.TEXT, text='Été 中文 ' * 30, prov=ProvenanceItem(
                page_no=1, bbox=BoundingBox(l=0, t=0, r=10, b=10), charspan=(0, 210)))
            document.add_picture(image=ImageRef.from_pil(Image.new('RGB', (8, 8), 'blue'), dpi=72))
        return document

    def extract_with_evidence(self, pdfpath):
        document = self.extract(pdfpath)
        regions = () if self.empty else (ExtractionRegion(1, (0., 0., 10., 10.), 'text',
                    ('#/texts/0',), 'native', 0.98),)
        return ExtractionResult(document, regions, True)


def pipeline(**kwargs):
    extractor = kwargs.pop('extractor', None) or FixtureExtractor()
    return PdfParsePipeline(PdfParseConfiguration(cache_identity={'fixture': 'v1'}, **kwargs), extractor=extractor)


@pytest.fixture
def pdf(tmp_path):
    path = tmp_path / 'A.pdf'
    path.write_bytes(b'PDF identity')
    return path


def cache_directory(engine, pdf):
    configuration = cache_configuration(engine)[0]
    digest = hashlib.sha256(pdf.read_bytes()).hexdigest()
    for manifest in pdf.with_name(pdf.name + '.pdfparsecache').glob('*/manifest.json'):
        value = json.loads(manifest.read_text())
        if value.get('configuration_hash') == configuration and value.get('pdf_sha256') == digest:
            return manifest.parent
    raise AssertionError('No cache entry for this PDF/configuration')


def test_sidecar_roundtrip_new_pipeline_no_inference(pdf, monkeypatch):
    engine = pipeline()
    first = engine.run(pdf)
    directory = first.document.assets_dir.parent
    assert first.document.assets_dir == directory / 'assets'
    assert {p.name for p in directory.iterdir()} == {
        'assets', 'manifest.json', 'document.json', 'chunks.json', 'result.json', 'models.json',
        'preparation.json', 'index.jsonl', 'visuals.jsonl', 'quality.json', 'document.md'}
    picture = next(e for e in first.document.elements.values() if isinstance(e, EnrichedPictureItem))
    assert FileSystemAssetStore(first.document.assets_dir).get(picture.asset_path).startswith(b'\x89PNG')
    other = pipeline(extractor=FixtureExtractor(fail=True))
    monkeypatch.setattr(other, 'enrich_document', lambda *a, **k: pytest.fail('enrichment on hit'))
    monkeypatch.setattr(other, 'chunk_document', lambda *a, **k: pytest.fail('chunking on hit'))
    second = other.run(pdf)
    assert other.extractor.calls == 0
    assert second.document == first.document
    assert second.chunks == first.chunks and second.metrics == first.metrics
    assert second.extraction == first.extraction
    assert second.preparation_manifest() == first.preparation_manifest()
    assert list(second.index_records()) == list(first.index_records())
    assert second.source_context([second.chunks[0].chunk_id]) == first.source_context([first.chunks[0].chunk_id])
    assert json.loads((directory / 'chunks.json').read_text())['format'] == 'pdfparse.chunks'
    assert not list(directory.rglob('*.pdf'))


def test_configuration_stable_and_settings_distinct(pdf):
    assert cache_configuration(pipeline())[0] == cache_configuration(pipeline())[0]
    assert cache_configuration(PdfParsePipeline(PdfParseConfiguration()))[0] == cache_configuration(PdfParsePipeline(PdfParseConfiguration()))[0]
    first, second = pipeline(), pipeline(chunking=StructuralChunkingModel(chunk_size=64))
    first.run(pdf); second.run(pdf)
    assert cache_configuration(first)[0] != cache_configuration(second)[0]
    assert len([p for p in pdf.with_name('A.pdf.pdfparsecache').iterdir() if p.is_dir() and not p.name.startswith('.')]) == 2
    native = PdfParsePipeline(PdfParseConfiguration(layout=NativeLayoutModel('heron_101')))
    assert cache_configuration(native)[0] != cache_configuration(PdfParsePipeline(PdfParseConfiguration()))[0]


def test_pdf_modified_and_missing_asset_invalidate(pdf):
    engine = pipeline()
    initial = engine.run(pdf)
    pdf.write_bytes(b'new PDF identity')
    modified = engine.run(pdf)
    assert engine.extractor.calls == 2 and modified.document.document_id != initial.document.document_id
    assert len(list(modified.document.assets_dir.glob('*.png'))) == 1
    assert initial.document.assets_dir != modified.document.assets_dir
    picture = next(e for e in modified.document.elements.values() if isinstance(e, EnrichedPictureItem))
    (modified.document.assets_dir / picture.asset_path).unlink()
    engine.run(pdf)
    assert engine.extractor.calls == 2


@pytest.mark.parametrize('filename', ['chunks.json', 'document.json', 'index.jsonl', 'manifest.json'])
def test_corruption_recomputes_without_partial_results(pdf, filename):
    engine = pipeline()
    result = engine.run(pdf)
    (cache_directory(engine, pdf) / filename).write_text('invalid')
    second = engine.run(pdf)
    assert engine.extractor.calls == 1 and second.chunks == result.chunks


def test_chunk_sources_validated_even_with_updated_digest(pdf):
    engine = pipeline()
    engine.run(pdf)
    directory = cache_directory(engine, pdf)
    path = directory / 'chunks.json'
    data = json.loads(path.read_text())
    data['chunks'][0]['source_refs'][0]['cref'] = '#/missing'
    path.write_text(json.dumps(data))
    manifest = json.loads((directory / 'manifest.json').read_text())
    manifest['files']['chunks.json'] = hashlib.sha256(path.read_bytes()).hexdigest()
    (directory / 'manifest.json').write_text(json.dumps(manifest))
    repaired=engine.run(pdf)
    assert engine.extractor.calls == 1
    engine._validate_chunks(repaired.document,repaired.chunks)


def test_failure_never_publishes_cache_and_can_retry(pdf, monkeypatch):
    engine = pipeline()
    original = engine.chunk_document
    def fail(*args): raise RuntimeError('failure')
    monkeypatch.setattr(engine, 'chunk_document', fail)
    with pytest.raises(RuntimeError, match='failure'):
        engine.run(pdf)
    assert not any(p.is_dir() and not p.name.startswith('.') for p in pdf.with_name('A.pdf.pdfparsecache').iterdir())
    monkeypatch.setattr(engine, 'chunk_document', original)
    engine.run(pdf)
    assert engine.extractor.calls == 1


def test_concurrent_requests_publish_once(pdf):
    engine = pipeline()
    barrier = threading.Barrier(2)
    def run(_):
        barrier.wait()
        return engine.run(pdf)
    with ThreadPoolExecutor(max_workers=2) as executor:
        first, second = list(executor.map(run, range(2)))
    assert engine.extractor.calls == 1 and first.chunks == second.chunks


def test_empty_document_and_opt_out(pdf):
    engine = pipeline(extractor=FixtureExtractor(empty=True))
    assert engine.run(pdf).chunks == ()
    assert engine.run(pdf).chunks == () and engine.extractor.calls == 1
    other = pipeline(cache=False)
    other.run(pdf); other.run(pdf)
    assert other.extractor.calls == 2


def test_custom_components_require_identity(pdf):
    engine = PdfParsePipeline(PdfParseConfiguration(), extractor=FixtureExtractor())
    with pytest.raises(ValueError, match='cache_identity'):
        engine.run(pdf)
    assert engine.extractor.calls == 0


def test_pdf_changed_during_extraction_not_published(pdf):
    class Mutating(FixtureExtractor):
        def extract(self, path):
            document = super().extract(path)
            path.write_bytes(b'changed during extraction')
            return document
    with pytest.raises(RuntimeError, match='PDF changed'):
        pipeline(extractor=Mutating()).run(pdf)
    assert not any(p.is_dir() and not p.name.startswith('.') for p in pdf.with_name('A.pdf.pdfparsecache').iterdir())


def test_cache_survives_process_restart(pdf):
    # Import the same fixture module in each process to preserve its qualified identity.
    script = '''
import sys
from test_result_cache import pipeline, FixtureExtractor
engine = pipeline(extractor=FixtureExtractor(fail=sys.argv[2] == 'read'))
result = engine.run(sys.argv[1])
assert result.chunks
assert engine.extractor.calls == (0 if sys.argv[2] == 'read' else 1)
'''
    env = dict(os.environ)
    env['PYTHONPATH'] = os.pathsep.join([str(Path(__file__).parent),
        str(Path(__file__).parents[1] / 'src'), env.get('PYTHONPATH', '')])
    for mode in ('write', 'read'):
        subprocess.run([sys.executable, '-c', script, str(pdf), mode],
                       env=env, check=True, capture_output=True, text=True, timeout=60)


def test_cache_directory_replaced_by_file_recomputes(pdf):
    import shutil
    engine = pipeline()
    engine.run(pdf)
    directory = cache_directory(engine, pdf)
    shutil.rmtree(directory)
    directory.write_text('damaged cache')
    assert engine.run(pdf).chunks
    assert directory.is_dir() and engine.extractor.calls == 1


def test_crops_belong_to_cache_and_are_recomputed_after_full_purge(pdf):
    from pdfparse.ingestion.cache import prune_cache, cache_info
    engine = pipeline()
    first = engine.run(pdf)
    picture = next(e for e in first.document.elements.values() if isinstance(e, EnrichedPictureItem))
    image_path = first.document.assets_dir / picture.asset_path
    original = image_path.read_bytes()
    parent = pdf.with_name(pdf.name + '.pdfparsecache')
    assert image_path.is_relative_to(parent)
    versions = parent / 'versions'
    versions.mkdir()
    archived = versions / f'{first.document.document_id}.pdf'
    archived.write_bytes(pdf.read_bytes())
    assert prune_cache(pdf, max_entries=0)['entries'] == 0
    assert cache_info(pdf)['bytes'] == 0 and not image_path.exists()
    assert archived.read_bytes() == pdf.read_bytes()
    again = engine.run(pdf)
    assert engine.extractor.calls == 2
    assert again.document.assets_dir == first.document.assets_dir
    assert image_path.read_bytes() == original


def test_cached_run_owns_its_assets_while_separate_enrichment_uses_injected_store(pdf, tmp_path):
    store = FileSystemAssetStore(tmp_path / 'external')
    engine = pipeline(asset_store=store)
    result = engine.run(pdf)
    assert result.document.assets_dir == cache_directory(engine, pdf) / 'assets'
    assert result.document.assets_dir != store.root and not store.root.exists()
    assert engine.run(pdf).chunks == result.chunks and engine.extractor.calls == 1
    document = engine.extractor.extract(pdf)
    enriched = engine.enrich_document(pdf, document)
    assert enriched.assets_dir == store.root and list(store.root.glob('*.png'))


def test_hit_uses_two_pdf_hashes_and_reuses_projections(pdf, monkeypatch):
    import pdfparse.ingestion.cache as module
    from pdfparse.backend.presentation import result_view
    engine = pipeline()
    first = engine.run(pdf)
    before = result_view(engine, first).model_dump()
    actual = module._sha
    reads = []
    def track(path):
        reads.append(path)
        return actual(path)
    monkeypatch.setattr(module, '_sha', track)
    hit = engine.run(pdf)
    assert reads.count(pdf) == 2
    monkeypatch.setattr(PdfParseResult, 'prepare_index', lambda *a, **k: pytest.fail('Index rebuilt on cache hit'))
    monkeypatch.setattr(type(hit.document), 'to_markdown', lambda *a: pytest.fail('Markdown rebuilt on cache hit'))
    monkeypatch.setattr(engine.codec, 'to_dict', lambda *a, **k: pytest.fail('Graph encoded again on cache hit'))
    assert result_view(engine, hit).model_dump() == before


def test_cache_quotas_bound_complete_bundles_including_crops(pdf):
    from pdfparse.ingestion.cache import cache_info, prune_cache
    first, second = pipeline(cache_max_entries=1), pipeline(cache_max_entries=3)
    assert cache_configuration(first)[0] == cache_configuration(second)[0]
    old = first.run(pdf)
    pipeline(chunking=StructuralChunkingModel(chunk_size=64), cache_max_entries=1).run(pdf)
    assert cache_info(pdf)['entries'] == 1 and not old.document.assets_dir.exists()
    with pytest.raises(RuntimeError, match='exceeds cache_max_bytes'):
        pipeline(cache_max_bytes=1).run(pdf)
    assert cache_info(pdf)['bytes'] <= 1
    result = second.run(pdf)
    assert result.document.assets_dir.is_dir()
    directory = cache_directory(second, pdf)
    os.utime(directory, (1, 1))
    assert prune_cache(pdf, max_age_seconds=1)['entries'] == 0
    assert not result.document.assets_dir.exists()


def test_full_purge_also_removes_legacy_crops_without_loading_old_format(pdf):
    from pdfparse.ingestion.cache import prune_cache, cache_info
    parent = pdf.with_name(pdf.name + '.pdfparsecache')
    legacy = parent / ('a' * 64)
    (legacy / 'assets').mkdir(parents=True)
    asset = legacy / 'assets/old.png'
    asset.write_bytes(b'old figure')
    (legacy / 'manifest.json').write_text('{"format":"pdfparse.result_cache","schema_version":1}')
    engine = pipeline()
    result = engine.run(pdf)
    assert json.loads((result.document.assets_dir.parent / 'manifest.json').read_text())['schema_version'] == 2
    assert prune_cache(pdf, max_entries=0)['entries'] == 0
    assert not asset.exists() and not result.document.assets_dir.exists()
    assert cache_info(pdf)['bytes'] == 0


def test_native_weights_do_not_change_configuration_key(tmp_path):
    from docling.datamodel.base_models import InputFormat
    engine = PdfParsePipeline(PdfParseConfiguration())
    options = engine.extractor._converter.format_to_options[InputFormat.PDF].pipeline_options
    artifacts = tmp_path / 'native'
    artifacts.mkdir()
    weight = artifacts / 'model.safetensors'
    weight.write_bytes(b'checkpoint-v1')
    options.artifacts_path = artifacts
    before = cache_configuration(engine)[0]
    stat = weight.stat()
    replacement = artifacts / 'replacement'
    replacement.write_bytes(b'checkpoint-v2')
    os.utime(replacement, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    replacement.replace(weight)
    assert cache_configuration(engine)[0] == before


def test_cached_result_is_reused_when_provisioned_weights_change(pdf, tmp_path, monkeypatch):
    from docling.datamodel.base_models import InputFormat
    engine = PdfParsePipeline(PdfParseConfiguration())
    converter = engine.extractor._converter
    options = converter.format_to_options[InputFormat.PDF].pipeline_options
    artifacts = tmp_path / 'native'
    artifacts.mkdir()
    weight = artifacts / 'model.bin'
    weight.write_text('first weights')
    options.artifacts_path = artifacts
    def extract(path):
        text = converter.initialized_pipelines.setdefault('fixture-native', weight.read_text())
        document = DoclingDocument(name='native-fixture')
        document.add_text(label=DocItemLabel.TEXT, text=text)
        return ExtractionResult(document)
    monkeypatch.setattr(engine.extractor, 'extract_with_evidence', extract)
    first = engine.run(pdf)
    weight.write_text('new weights')
    second = engine.run(pdf)
    assert first.chunks[0].text == second.chunks[0].text == 'first weights'
    assert first.preparation_manifest() == second.preparation_manifest()


def test_crashed_writer_remnants_are_pruned_without_touching_assets(pdf):
    from pdfparse.ingestion.cache import prune_cache
    engine = pipeline()
    result = engine.run(pdf)
    parent = cache_directory(engine, pdf).parent
    for name in ('.cache-abandoned', '.old-abandoned'):
        (parent / name).mkdir()
        (parent / name / 'unused.bin').write_bytes(b'abandoned')
    prune_cache(pdf)
    assert not (parent / '.cache-abandoned').exists()
    assert not (parent / '.old-abandoned').exists()
    assert list(result.document.assets_dir.glob('*.png'))


def test_native_weights_are_resolved_from_models_dir(tmp_path, monkeypatch):
    from pdfparse.ingestion.preparation import native_weights_identity
    monkeypatch.setenv('MODELS_DIR', str(tmp_path))
    engine = PdfParsePipeline(PdfParseConfiguration())
    assert native_weights_identity(engine)['complete'] is False
    for directory, files in (
        ('layout/heron', ('config.json', 'preprocessor_config.json', 'model.safetensors')),
        ('table_structure/tableformer', ('model_artifacts/tableformer/accurate/tm_config.json',
          'model_artifacts/tableformer/accurate/tableformer_accurate.safetensors')),
    ):
        for name in files:
            path = tmp_path / directory / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b'native fixture')
    identity = native_weights_identity(engine)
    assert identity['complete'] and identity['root'] == str(tmp_path)
    assert len(identity['snapshots']) == 2
    key = cache_configuration(engine)[0]
    (tmp_path / 'layout/heron/model.safetensors').unlink()
    assert native_weights_identity(engine)['complete'] is False
    assert cache_configuration(engine)[0] == key


@pytest.mark.parametrize('package', ['fla-core', 'causal-conv1d', 'bitsandbytes'])
def test_package_versions_do_not_change_configuration_key(pdf, monkeypatch, package):
    from pdfparse.ingestion import preparation
    engine = pipeline()
    previous = cache_configuration(engine)[0]
    installed_version = preparation.version
    monkeypatch.setattr(preparation, 'version', lambda name: '999.0' if name == package else installed_version(name))
    assert cache_configuration(engine)[0] == previous


def test_cli_cold_lookup_validates_profile_pdf_and_assets(pdf):
    from pdfparse.ingestion.cache import cached_run_chunks
    engine = pipeline()
    profile = {'ocr': {'name': 'fixture', 'settings': {'max_new_tokens': 2048}}}
    engine._runtime_configuration = profile
    result = engine.run(pdf)
    assert cached_run_chunks(pdf, profile) == len(result.chunks)
    assert cached_run_chunks(pdf, {'ocr': {'name': 'other'}}) is None
    asset = next(result.document.assets_dir.glob('*.png'))
    original = asset.read_bytes()
    asset.write_bytes(b'corrupt')
    assert cached_run_chunks(pdf, profile) is None
    asset.write_bytes(original)
    pdf.write_bytes(b'changed PDF')
    assert cached_run_chunks(pdf, profile) is None


def test_cli_cold_lookup_upgrades_existing_cache(pdf):
    from pdfparse.ingestion.cache import cached_run_chunks, remember_runtime_configuration
    engine = pipeline()
    result = engine.run(pdf)
    profile = {'fixture': 'v1'}
    assert cached_run_chunks(pdf, profile) is None
    remember_runtime_configuration(pdf, engine, profile)
    assert cached_run_chunks(pdf, profile) == len(result.chunks)
