from pdfparse import ModelRegistry, PdfParseConfiguration
from pdfparse.backend.defaults import default_runtime_configuration


def test_shared_profile_models_and_routing():
    profile = default_runtime_configuration()
    assert profile.layout is None
    assert profile.picture_classifier.name == 'dfc_v25'
    assert profile.picture_classifier.device == 'cpu'
    assert profile.ocr.name == 'mineru2_5_pro_2605'
    assert profile.vlm.name == 'qwen3_5_4b'
    assert profile.ocr.device == profile.vlm.device == 'cuda:0'
    assert profile.vlm.settings['quantization'] == 'nf4'
    assert profile.enrichment.figures == profile.enrichment.fallback == 'vlm'
    assert profile.enrichment.text_ocr and not profile.enrichment.page_vlm
    assert profile.enrichment.confidence_threshold == .75
    assert profile.chunking.kind == 'structural' and profile.chunking.chunk_size == 512
    assert profile.chunking.target_tokens == 384 and profile.chunking.min_tokens == 128
    models = ModelRegistry()
    for category in profile.required_services():
        choice = getattr(profile, category)
        models.get(category, choice.name).implementation.validate_settings(choice.settings)
    cache = PdfParseConfiguration()
    assert cache.cache_max_entries == 8
    assert cache.cache_max_bytes == 512 * 1024 * 1024
    assert cache.cache_max_age_seconds is None


def test_studio_cli_selects_the_compact_shared_profile_without_loading_models(monkeypatch):
    import sys
    import uvicorn
    from pdfparse.backend import __main__ as studio
    captured = {}
    monkeypatch.setattr(sys, 'argv', ['pdfparse-studio'])
    monkeypatch.setattr(studio, 'create_app', lambda **kwargs: captured.update(kwargs))
    monkeypatch.setattr(uvicorn, 'run', lambda *args, **kwargs: None)
    studio.main()
    assert captured['runtime_configuration'] == default_runtime_configuration()
    assert captured['load_models_on_startup'] is False


def test_runtime_options_exposes_shared_defaults(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from pdfparse.backend.app import create_app
    from pdfparse.backend import runtime
    monkeypatch.setattr(runtime, 'model_options', lambda models: [])
    monkeypatch.setattr(runtime, 'available_devices', lambda: [])
    with TestClient(create_app(root=tmp_path, models=object())) as client:
        response = client.get('/api/runtime/options')
        assert response.json()['defaults'] == default_runtime_configuration().model_dump(mode='json')
        assert not any('/pages' in path for path in client.get('/openapi.json').json()['paths'])


def test_default_studio_lifespan_never_loads_models(tmp_path, monkeypatch):
    import asyncio
    import sys
    import uvicorn
    from pdfparse.backend import __main__ as studio, runtime
    apps = []
    def unexpected(*args, **kwargs):
        raise AssertionError('Studio startup must not load or validate models')
    monkeypatch.setattr(runtime, 'validate_selection', unexpected)
    monkeypatch.setattr(runtime, 'build_pipeline', unexpected)
    monkeypatch.setattr(uvicorn, 'run', lambda app, **kwargs: apps.append(app))
    monkeypatch.setattr(sys, 'argv', ['pdfparse-studio', '--root', str(tmp_path)])
    studio.main()
    async def check():
        async with apps[0].router.lifespan_context(apps[0]):
            get_runtime = next(route.endpoint for route in apps[0].routes if getattr(route, 'operation_id', None) == 'getRuntime')
            state = get_runtime()
            assert state.status == 'unloaded'
            assert state.configuration == state.requested == default_runtime_configuration()
    asyncio.run(check())


def test_default_studio_reads_batch_cache_immediately_without_models(tmp_path, monkeypatch):
    import sys
    import uvicorn
    from fastapi.testclient import TestClient
    from pdfparse import PdfParsePipeline
    from pdfparse.backend import __main__ as studio, runtime
    from pdfparse.ingestion.cache import remember_runtime_configuration
    from tests.test_backend import Extractor
    from tests.test_layout_pipeline import make_pdf

    path = tmp_path / 'cached.pdf'
    make_pdf(path, pages=2)
    engine = PdfParsePipeline(PdfParseConfiguration(cache_identity={'fixture': 'default-studio'}), extractor=Extractor())
    engine.run(path)
    profile = default_runtime_configuration()
    remember_runtime_configuration(path, engine, profile.model_dump(mode='json'))
    apps = []
    def unexpected(*args, **kwargs):
        raise AssertionError('Reading the default cache must not initialize models')
    monkeypatch.setattr(runtime, 'validate_selection', unexpected)
    monkeypatch.setattr(runtime, 'build_pipeline', unexpected)
    monkeypatch.setattr(uvicorn, 'run', lambda app, **kwargs: apps.append(app))
    monkeypatch.setattr(sys, 'argv', ['pdfparse-studio', '--root', str(tmp_path)])
    studio.main()
    with TestClient(apps[0]) as client:
        state = client.get('/api/runtime').json()
        assert state['status'] == 'unloaded'
        assert state['configuration'] == profile.model_dump(mode='json')
        document = client.get('/api/documents').json()['documents'][0]
        assert document['status'] == 'ready'
        result = client.get(f"/api/documents/{document['id']}/result")
        assert result.status_code == 200
        assert 'Guide PDF' in result.json()['markdown']
        assert client.get('/api/jobs').json() == []


def test_studio_native_remains_an_explicit_opt_out(monkeypatch):
    import sys
    import uvicorn
    from pdfparse.backend import __main__ as studio
    captured = {}
    monkeypatch.setattr(sys, 'argv', ['pdfparse-studio', '--native'])
    monkeypatch.setattr(studio, 'create_app', lambda **kwargs: captured.update(kwargs))
    monkeypatch.setattr(uvicorn, 'run', lambda *args, **kwargs: None)
    studio.main()
    assert captured['runtime_configuration'] is None


def test_uncached_analysis_loads_selected_profile_after_settings_inspection(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from pdfparse import PdfParsePipeline
    from pdfparse.backend.app import create_app
    from pdfparse.backend import runtime
    from tests.test_backend import Extractor, wait_ready
    from tests.test_layout_pipeline import make_pdf

    make_pdf(tmp_path / 'new.pdf', pages=2)
    profile = default_runtime_configuration()
    calls = []
    engine = PdfParsePipeline(PdfParseConfiguration(cache_identity={'fixture': 'lazy-studio'}), extractor=Extractor())
    monkeypatch.setattr(runtime, 'validate_selection', lambda models, selected: calls.append(('validate', selected)))
    monkeypatch.setattr(runtime, 'build_pipeline', lambda models, selected, **kwargs: calls.append(('build', selected)) or engine)
    with TestClient(create_app(root=tmp_path, runtime_configuration=profile, load_models_on_startup=False)) as client:
        assert client.get('/api/settings').status_code == 200
        assert calls == []
        document = client.get('/api/documents').json()['documents'][0]
        assert document['status'] == 'idle'
        assert client.post(f"/api/documents/{document['id']}/process").status_code == 202
        assert wait_ready(client, document['id'])['status'] == 'ready'
        assert calls == [('validate', profile), ('build', profile)]
        assert client.get('/api/runtime').json()['status'] == 'loaded'


def test_default_profile_retains_pre_scheduler_cache_identity(tmp_path, monkeypatch):
    from pdfparse.backend.runtime import build_pipeline
    from pdfparse.ingestion.preparation import cache_configuration
    from pdfparse.models.base import BaseInferenceModel
    from pdfparse.models.ocr.transformers import TransformersOcrModel

    # Frozen OCR settings from the profile before scheduler tuning.
    previous_profile_ocr = {
        'batch_size': 1, 'dtype': 'float16', 'max_new_tokens': 2048,
        'alphabet': 'latin', 'reject_repetition': True,
    }
    previous_model_ocr = {
        **previous_profile_ocr, 'attn_implementation': 'sdpa',
        'quantization': 'none', 'use_cache': True,
    }
    profile = default_runtime_configuration()
    assert profile.ocr.settings == previous_profile_ocr
    models = ModelRegistry()
    loaded = {}
    def load(category, name, *, device, settings):
        spec = models.get(category, name)
        model = object.__new__(spec.implementation)
        BaseInferenceModel.__init__(model, model_path=tmp_path, device=device, settings=settings)
        model.model_identity = {'category': category, 'name': name, 'weights': {'fixture': 'unchanged'}}
        if category == 'picture_classifier':
            model.labels = ('line_chart',)
        loaded[category] = model
        return model
    monkeypatch.setattr(models, 'load', load)
    current = build_pipeline(models, profile)
    assert loaded['ocr'].settings == previous_model_ocr
    current_key, current_identity = cache_configuration(current)

    # Recreate the old cadence/settings, including enrichment transformation IDs.
    monkeypatch.setattr(TransformersOcrModel, 'guard_check_interval', 1)
    previous = build_pipeline(models, profile)
    loaded['ocr'].settings = previous_model_ocr
    previous_key, previous_identity = cache_configuration(previous)
    assert current_key == previous_key
    assert current_identity == previous_identity
    assert 'guard_check_interval' not in str(current_identity)
