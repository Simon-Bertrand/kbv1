from types import SimpleNamespace

import pytest

from pdfparse import cli
from pdfparse.backend import runtime
from pdfparse.progress import Stage, report_progress


@pytest.fixture(autouse=True)
def isolated_models_dir(tmp_path, monkeypatch):
    root = tmp_path / 'models'
    monkeypatch.setenv('MODELS_DIR', str(root))
    return root


def test_e5_tokenizer_uses_the_shared_embeddings_directory(isolated_models_dir, monkeypatch):
    monkeypatch.setenv('PDFPARSE_E5_TOKENIZER_DIR', str(isolated_models_dir.parent))
    assert cli.e5_tokenizer_directory() is None
    directory = isolated_models_dir / 'embeddings' / 'multilingual_e5_large'
    directory.mkdir(parents=True)
    assert cli.e5_tokenizer_directory() == directory


def test_batch_progress_includes_failures_and_continues(tmp_path, monkeypatch, capsys):
    (tmp_path / 'a.pdf').touch()
    (tmp_path / 'nested').mkdir()
    (tmp_path / 'nested' / 'b.PDF').touch()
    cache = tmp_path / 'a.pdf.pdfparsecache'
    cache.mkdir()
    (cache / 'ignored.pdf').touch()
    versions = tmp_path / '_versions'
    versions.mkdir()
    (versions / 'archived.pdf').touch()
    calls = []

    def run(path):
        calls.append(path.name)
        report_progress(Stage.CHUNKING)
        if path.name == 'a.pdf':
            raise ValueError('PDF invalide')
        return SimpleNamespace(chunks=[1, 2])

    monkeypatch.setattr('pdfparse.ingestion.cache.remember_runtime_configuration', lambda *args: None)
    monkeypatch.setattr(runtime, 'validate_selection', lambda *args: None)
    monkeypatch.setattr(runtime, 'build_pipeline', lambda *args, **kwargs: SimpleNamespace(run=run))
    assert cli.main(['--data-dir', str(tmp_path)]) == 1
    assert sorted(calls) == ['a.pdf', 'b.PDF']
    output = capsys.readouterr()
    assert '2/2' in output.err
    assert 'PDF invalide' in output.err
    assert '1 réussis, 1 échecs' in output.out


def test_empty_corpus_from_environment(tmp_path, monkeypatch):
    monkeypatch.setenv('DATA_DIR', str(tmp_path))
    assert cli.main([]) == 0


def test_selected_pdf_reads_the_snapshot_in_its_adjacent_cache(tmp_path, monkeypatch):
    import json
    from hashlib import sha256

    monkeypatch.delenv('SOURCE_VERSIONS_DIR', raising=False)
    source = tmp_path / 'a.pdf'
    source.write_bytes(b'%PDF replaced')
    original = b'%PDF earlier'
    digest = sha256(original).hexdigest()
    versions = tmp_path / 'a.pdf.pdfparsecache' / 'versions'
    versions.mkdir(parents=True)
    snapshot = versions / f'{digest}.pdf'
    snapshot.write_bytes(original)
    manifest = tmp_path / 'selection.json'
    manifest.write_text(json.dumps({str(source): digest}))
    assert cli.selected_pdfs(tmp_path, manifest) == {source: snapshot}


def test_warm_corpus_never_builds_or_validates_models(tmp_path, monkeypatch, capsys):
    (tmp_path / 'a.pdf').touch()
    monkeypatch.setattr('pdfparse.ingestion.cache.cached_run_chunks', lambda *args: 3)
    def unexpected(*args):
        raise AssertionError('Models must not be loaded for warm caches')
    monkeypatch.setattr(runtime, 'validate_selection', unexpected)
    monkeypatch.setattr(runtime, 'build_pipeline', unexpected)
    assert cli.main(['--data-dir', str(tmp_path)]) == 0
    assert 'Cache réutilisé' in capsys.readouterr().out


def test_pdf_workers_overlap_and_reuse_their_own_pipeline(tmp_path, monkeypatch):
    from threading import Barrier, Lock
    for index in range(4):
        (tmp_path / f'{index}.pdf').touch()
    barrier = Barrier(2)
    pipelines = []
    owners = {}
    active = set()
    lock = Lock()
    def build(*args, **kwargs):
        owner = object()
        with lock:
            pipelines.append(owner)
        def run(path):
            with lock:
                assert owner not in active
                active.add(owner)
                owners[path.name] = owner
            barrier.wait(timeout=5)
            with lock:
                active.remove(owner)
            return SimpleNamespace(chunks=[1])
        return SimpleNamespace(run=run)
    monkeypatch.setattr(runtime, 'validate_selection', lambda *args: None)
    monkeypatch.setattr(runtime, 'build_pipeline', build)
    monkeypatch.setattr('pdfparse.ingestion.cache.remember_runtime_configuration', lambda *args: None)
    assert cli.main(['--data-dir', str(tmp_path), '--workers', '2']) == 0
    assert len(pipelines) == len(set(owners.values())) == 2
    assert len(owners) == 4


def test_real_cli_queues_then_reuses_corpus_without_model_loading(tmp_path, monkeypatch, capsys):
    import pdfparse
    from pdfparse import ModelRegistry, PdfParsePipeline
    from pdfparse.models import ModelSource
    from pdfparse.backend.schemas import RuntimeConfiguration, ModelSelection, EnrichmentSelection
    from pdfparse.backend import defaults
    from tests.test_backend_runtime import FigureClassifier, FigureExtractor
    from tests.test_model_registry import FixtureVlm

    corpus = tmp_path / 'pdfs'
    corpus.mkdir()
    for index in range(4):
        (corpus / f'{index}.pdf').write_bytes(f'fixture PDF {index}'.encode())
    weights = tmp_path / 'weights'
    weights.mkdir()
    (weights / 'weights.bin').write_bytes(b'fixture weights')
    models = ModelRegistry(tmp_path, builtins=False)
    for category, implementation in [('picture_classifier', FigureClassifier), ('vlm', FixtureVlm)]:
        models.register(category, 'fixture', implementation, ModelSource(local_path=weights, files=('weights.bin',)))
    profile = RuntimeConfiguration(
        picture_classifier=ModelSelection(name='fixture', device='cpu'),
        vlm=ModelSelection(name='fixture', device='cpu'),
        enrichment=EnrichmentSelection(figures='vlm', fallback='vlm', text_ocr=False))
    monkeypatch.setattr(defaults, 'default_runtime_configuration', lambda: profile)
    monkeypatch.setattr(pdfparse, 'ModelRegistry', lambda: models)
    monkeypatch.setattr(pdfparse, 'PdfParsePipeline', lambda config: PdfParsePipeline(
        config.model_copy(update={'cache_identity': {'fixture': 'queued-cli-v1'}}), extractor=FigureExtractor()))
    assert cli.main(['--data-dir', str(corpus), '--workers', '2']) == 0
    assert models.cache_info()['instances'] == 2
    assert '4 réussis, 0 échecs' in capsys.readouterr().out
    def unexpected(*args, **kwargs):
        raise AssertionError('Warm corpus must not load or validate models')
    monkeypatch.setattr(models, 'load', unexpected)
    monkeypatch.setattr(runtime, 'validate_selection', unexpected)
    assert cli.main(['--data-dir', str(corpus), '--workers', '4', '--batch-size', '8']) == 0
    assert capsys.readouterr().out.count('Cache réutilisé') == 4


@pytest.mark.parametrize('devices', [
    ('cpu', 'cpu', 'cpu'), ('cpu', 'cuda:0', 'cuda:0'),
    ('cuda:0', 'cpu', 'cuda:0'), ('cuda:0', 'cuda:0', 'cpu'),
])
def test_configuration_file_routes_each_model_to_its_selected_device(tmp_path, monkeypatch, devices):
    import pdfparse
    from threading import current_thread
    from pdfparse import ModelRegistry, PdfParsePipeline
    from pdfparse.models import ModelSource
    from pdfparse.backend.schemas import RuntimeConfiguration, ModelSelection, EnrichmentSelection, DeviceOption
    from tests.test_backend_runtime import FigureClassifier, FigureExtractor
    from tests.test_model_registry import FixtureOcr, FixtureVlm
    owners = {}
    class DFC(FigureClassifier):
        def _predict_batch(self, images):
            owners['dfc'] = current_thread().name
            return super()._predict_batch(images)
    class OCR(FixtureOcr):
        def predict(self, image):
            owners['ocr'] = current_thread().name
            return super().predict(image)
    class VLM(FixtureVlm):
        def predict(self, image, *, prompt=None):
            owners['vlm'] = current_thread().name
            return super().predict(image, prompt=prompt)
    corpus = tmp_path / 'pdfs'
    corpus.mkdir()
    (corpus / 'figure.pdf').write_bytes(b'fixture PDF')
    weights = tmp_path / 'weights'
    weights.mkdir()
    (weights / 'weights.bin').write_bytes(b'fixture weights')
    registry = ModelRegistry(tmp_path, builtins=False)
    for category, implementation in [('picture_classifier', DFC), ('ocr', OCR), ('vlm', VLM)]:
        registry.register(category, 'fixture', implementation, ModelSource(local_path=weights, files=('weights.bin',)))
    profile = RuntimeConfiguration(**{category: ModelSelection(name='fixture', device=device)
        for category, device in zip(('picture_classifier', 'ocr', 'vlm'), devices)},
        enrichment=EnrichmentSelection(figures='ocr_vlm', fallback='ocr_vlm', text_ocr=False))
    config_path = tmp_path / 'pipeline.json'
    config_path.write_text(profile.model_dump_json())
    monkeypatch.setattr(runtime, 'available_devices', lambda: [DeviceOption(id=device, label=device) for device in ('cpu', 'cuda:0')])
    monkeypatch.setattr(pdfparse, 'ModelRegistry', lambda: registry)
    monkeypatch.setattr(pdfparse, 'PdfParsePipeline', lambda config: PdfParsePipeline(
        config.model_copy(update={'cache_identity': {'fixture': 'mixed-devices-v1'}}), extractor=FigureExtractor()))
    assert cli.main(['--data-dir', str(corpus), '--configuration', str(config_path), '--cpu-workers', '1', '--cpu-threads', '1']) == 0
    for role, device in zip(('dfc', 'ocr', 'vlm'), devices):
        assert owners[role].startswith('pdfparse-cpu-') if device == 'cpu' else owners[role] == 'pdfparse-cuda:0'


def test_root_dotenv_data_dir_from_subdirectory(tmp_path, monkeypatch, capsys):
    root = tmp_path / 'workspace'
    root.mkdir()
    (root / 'pyproject.toml').write_text('[tool.uv.workspace]\nmembers = []\n')
    (root / '.env').write_text('DATA_DIR="./corpus with spaces"\n')
    corpus = root / 'corpus with spaces'
    corpus.mkdir()
    nested = root / 'pdfparse' / 'src'
    nested.mkdir(parents=True)
    monkeypatch.chdir(nested)
    monkeypatch.delenv('DATA_DIR', raising=False)
    assert cli.main([]) == 0
    assert str(corpus) in capsys.readouterr().out


def test_exported_and_explicit_data_dir_override_root_dotenv(tmp_path, monkeypatch, capsys):
    (tmp_path / 'pyproject.toml').write_text('[tool.uv.workspace]\nmembers = []\n')
    (tmp_path / '.env').write_text('DATA_DIR=./missing\n')
    exported, explicit = tmp_path / 'exported', tmp_path / 'explicit'
    exported.mkdir()
    explicit.mkdir()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv('DATA_DIR', str(exported))
    assert cli.main([]) == 0
    assert str(exported) in capsys.readouterr().out
    assert cli.main(['--data-dir', str(explicit)]) == 0
    assert str(explicit) in capsys.readouterr().out


def test_batch_default_profile_is_the_ui_default_without_overrides(tmp_path, monkeypatch):
    from pdfparse.backend.defaults import default_runtime_configuration
    (tmp_path / 'source.pdf').touch()
    profiles = []
    def cached(path, profile):
        profiles.append(profile)
        return 0
    monkeypatch.setattr('pdfparse.ingestion.cache.cached_run_chunks', cached)
    monkeypatch.setattr(runtime, 'validate_selection', lambda *args: pytest.fail('Warm caches must not load models'))
    assert cli.main(['--data-dir', str(tmp_path)]) == 0
    selection = default_runtime_configuration()
    effective = runtime.build_chunking(selection.chunking)
    assert profiles == [runtime.effective_runtime_configuration(selection, effective)]


def test_cache_scan_overlaps_processing_first_missing_pdf(tmp_path, monkeypatch):
    from threading import Event
    for name in ('a.pdf', 'b.pdf', 'c.pdf'):
        (tmp_path / name).touch()
    processing, scanning = Event(), Event()
    processed = []
    def cached(path, configuration):
        if path.name == 'a.pdf':
            return None
        if path.name == 'b.pdf':
            scanning.set()
            assert processing.wait(5), 'Entire corpus scanned before first PDF started'
            return 2
        return None
    def run(path):
        processed.append(path.name)
        processing.set()
        assert scanning.wait(5), 'Cache scanning stopped while PDF was running'
        return SimpleNamespace(chunks=[1])
    monkeypatch.setattr('pdfparse.ingestion.cache.cached_run_chunks', cached)
    monkeypatch.setattr('pdfparse.ingestion.cache.remember_runtime_configuration', lambda *args: None)
    monkeypatch.setattr(runtime, 'validate_selection', lambda *args: None)
    monkeypatch.setattr(runtime, 'build_pipeline', lambda *args, **kwargs: SimpleNamespace(run=run))
    assert cli.main(['--data-dir', str(tmp_path), '--workers', '1']) == 0
    assert processed == ['a.pdf', 'c.pdf']


def test_background_cache_scan_failure_continues_and_counts_once(tmp_path, monkeypatch, capsys):
    for name in ('a.pdf', 'b.pdf', 'c.pdf'):
        (tmp_path / name).touch()
    processed = []
    def cached(path, configuration):
        if path.name == 'b.pdf':
            raise OSError('cache inaccessible')
        return None
    def run(path):
        processed.append(path.name)
        return SimpleNamespace(chunks=[1])
    monkeypatch.setattr('pdfparse.ingestion.cache.cached_run_chunks', cached)
    monkeypatch.setattr('pdfparse.ingestion.cache.remember_runtime_configuration', lambda *args: None)
    monkeypatch.setattr(runtime, 'validate_selection', lambda *args: None)
    monkeypatch.setattr(runtime, 'build_pipeline', lambda *args, **kwargs: SimpleNamespace(run=run))
    assert cli.main(['--data-dir', str(tmp_path), '--workers', '2']) == 1
    assert sorted(processed) == ['a.pdf', 'c.pdf']
    output = capsys.readouterr()
    assert '2 réussis, 1 échecs' in output.out
    assert '3/3' in output.err
    assert 'cache inaccessible' in output.err
