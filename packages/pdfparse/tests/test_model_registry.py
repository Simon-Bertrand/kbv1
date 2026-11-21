"""Unified model categories, explicit provisioning and per-device runtime reuse."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import hashlib
import json
from pathlib import Path
from unittest.mock import Mock

from PIL import Image
import pytest
from pydantic import ValidationError

from pdfparse import PdfParseConfiguration, PdfParsePipeline
from pdfparse.models import ModelCategory, ModelConfiguration, ModelRegistry, ModelSource, ModelSettings
from pdfparse.models import download as provisioning
from pdfparse.models.ocr import BaseOcrModel
from pdfparse.models.vlm import BaseVlmModel
from pdfparse.models.layout.options import DoclingLayoutOptions
from pdfparse.models.layout.model import DoclingLayoutAdapter
from tests.layout_detectors import FixtureDetector
from tests.test_layout_pipeline import make_pdf


class FixtureOcr(BaseOcrModel):
    loads = 0

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        type(self).loads += 1

    def predict(self, image):
        return f"recognized:{image.width}:{self.device}"


class FixtureVlm(BaseVlmModel):
    def predict(self, image, *, prompt="Describe this image."):
        return f"{prompt}:{image.width}:{self.device}"


@pytest.fixture
def local(tmp_path):
    source = tmp_path / "original"
    (source / "nested").mkdir(parents=True)
    (source / "nested/weights.bin").write_bytes(b"local weights")
    return ModelSource(local_path=source, files=("nested/weights.bin",))


def test_concurrent_load_constructs_one_runtime(tmp_path, local):
    registry = ModelRegistry(tmp_path, builtins=False)
    registry.register("ocr", "fixture", FixtureOcr, local)
    registry.download_all()
    previous = FixtureOcr.loads
    with ThreadPoolExecutor(max_workers=4) as workers:
        models = list(workers.map(lambda _: registry.load("ocr", "fixture"), range(8)))
    assert len({id(model) for model in models}) == 1
    assert FixtureOcr.loads == previous + 1


def test_custom_typed_settings_validate_before_loading_and_normalize_cache(tmp_path, local):
    class Options(ModelSettings):
        language: str = "fr"
    class TypedOcr(FixtureOcr):
        settings_model = Options

    registry = ModelRegistry(tmp_path, builtins=False)
    registry.register("ocr", "typed", TypedOcr, local)
    with pytest.raises(ValidationError, match="extra_forbidden"):
        registry.load("ocr", "typed", settings={"langauge": "fr"})
    with pytest.raises(ValidationError, match="string_type"):
        TypedOcr(model_path=tmp_path, settings={"language": 42})
    registry.download("ocr", "typed")
    model = registry.load("ocr", "typed")
    assert model.settings == {"language": "fr"}
    assert registry.load("ocr", "typed", settings={"language": "fr"}) is model


@pytest.mark.parametrize("name,settings", [
    ("heron", {"unknown": True}),
    ("pp_doclayout_s", {"cpu_threads": 0}),
    ("pp_doclayout_s", {"cpu_threads": "2"}),
    ("pp_doclayout_s", {"enable_mkldnn": "false"}),
    ("pp_doclayout_s", {"unknown": True}),
    ("dfc_v25", {"unknown": True}),
])
def test_builtin_settings_fail_before_weights_or_runtime_loading(tmp_path, name, settings):
    registry = ModelRegistry(tmp_path)
    spec = next(spec for spec in registry.list() if spec.name == name)
    with pytest.raises(ValidationError):
        registry.load(spec.category, name, settings=settings)


def test_missing_weights_never_trigger_download(tmp_path, no_hub):
    registry = ModelRegistry(tmp_path)
    with pytest.raises(FileNotFoundError, match="Run the appropriate models/download"):
        registry.load("layout", "heron")
    no_hub.assert_not_called()


def test_registration_rejects_wrong_category_abstract_and_duplicate(tmp_path, local):
    registry = ModelRegistry(tmp_path, builtins=False)
    with pytest.raises(TypeError, match="BaseOcrModel"):
        registry.register("ocr", "wrong", FixtureVlm, local)
    with pytest.raises(TypeError, match="concrete"):
        registry.register("ocr", "abstract", BaseOcrModel, local)
    registry.register("ocr", "fixture", FixtureOcr, local)
    with pytest.raises(ValueError, match="already registered"):
        registry.register("ocr", "fixture", FixtureOcr, local)
    with pytest.raises(KeyError, match="Unknown model"):
        registry.get("ocr", "unknown")
    with pytest.raises(ValueError):
        registry.list("unknown_category")
    for name in ("../escape", "", "a/b", "Uppercase"):
        with pytest.raises(ValueError, match="directory-safe"):
            registry.register("ocr", name, FixtureOcr, local)


@pytest.mark.parametrize("device", ["gpu", "cuda:-1", "cpu:1", "", "auto"])
def test_invalid_devices_fail_early(device, tmp_path):
    with pytest.raises(ValidationError, match="device"):
        ModelConfiguration(model_path=tmp_path, device=device)


@pytest.mark.parametrize("files", [(), ("../escape",), ("/absolute",), ("a/../b",),
                                   ("*.bin",), ("a\\b",), ("x", "x")])
def test_invalid_weight_sources(files, tmp_path):
    with pytest.raises(ValueError):
        ModelSource(local_path=tmp_path, files=files)


def test_weight_source_requires_one_explicit_origin(tmp_path):
    with pytest.raises(ValueError, match="pinned revision"):
        ModelSource(repo_id="owner/model", files=("weights",))
    with pytest.raises(ValueError, match="not both"):
        ModelSource(repo_id="owner/model", revision="version", local_path=tmp_path, files=("weights",))


def test_layout_instance_reused_by_docling_and_excluded_from_serialization(tmp_path, monkeypatch):
    from docling.datamodel.accelerator_options import AcceleratorOptions
    from docling.datamodel.pipeline_options import PdfPipelineOptions
    from docling.document_converter import create_pipeline_options_hash

    model = FixtureDetector(model_path=tmp_path, device="cpu")
    options = model.get_options()
    assert deepcopy(options).detector_instance is model
    assert "detector_instance" not in options.model_dump(mode="json")
    restored = DoclingLayoutOptions.model_validate_json(options.model_dump_json())
    assert restored.detector_instance is None and restored.detector_class is FixtureDetector
    adapter = DoclingLayoutAdapter(None, AcceleratorOptions(), options)
    assert adapter.detector is model
    other = FixtureDetector(model_path=tmp_path)
    cache_key = lambda model: create_pipeline_options_hash(PdfPipelineOptions(layout_options=model.get_options()))
    assert cache_key(model) == cache_key(model)
    assert cache_key(model) != cache_key(other)
    assert PdfParseConfiguration(cache=False, layout=model).layout is model
    monkeypatch.setattr(FixtureDetector, "__init__", lambda *a, **kw: pytest.fail("Layout loaded twice"))
    source = tmp_path / "source.pdf"
    make_pdf(source)
    result = PdfParsePipeline(PdfParseConfiguration(cache=False, layout=model)).run(source)
    assert any("Hello layout pipeline" in chunk.text for chunk in result.chunks)


def test_hub_sources_require_immutable_commit_revision():
    with pytest.raises(ValueError, match="pinned revision"):
        ModelSource(repo_id="owner/model", revision="main", files=("weights.bin",))


def test_model_instance_cache_is_lru_bounded_without_closing_borrowed_models(tmp_path, local):
    registry = ModelRegistry(tmp_path, builtins=False, max_instances=2)
    registry.register('ocr', 'fixture', FixtureOcr, local)
    registry.download('ocr', 'fixture')
    first = registry.load('ocr', 'fixture', settings={'variant': 1})
    second = registry.load('ocr', 'fixture', settings={'variant': 2})
    assert registry.load('ocr', 'fixture', settings={'variant': 1}) is first
    registry.load('ocr', 'fixture', settings={'variant': 3})
    assert registry.cache_info() == {'instances': 2, 'max_instances': 2}
    assert registry.load('ocr', 'fixture', settings={'variant': 2}) is not second
    assert second.predict(Image.new('RGB', (3, 3))).startswith('recognized:')


@pytest.fixture
def no_hub(monkeypatch):
    mocked = Mock(side_effect=AssertionError('The runtime must never download models'))
    monkeypatch.setattr('huggingface_hub.snapshot_download', mocked)
    monkeypatch.setattr('huggingface_hub.hf_hub_download', mocked)
    return mocked


def provision(registry, category, name):
    path = registry.weights_path(category, name)
    for filename in registry.get(category, name).source.files:
        asset = path / filename
        asset.parent.mkdir(parents=True, exist_ok=True)
        asset.write_bytes(b'local fixture')
    return path


def test_model_root_uses_only_models_dir(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv('MODELS_DIR', raising=False)
    monkeypatch.setenv('HF_HUB_CACHE', str(tmp_path / 'hub'))
    assert ModelRegistry(builtins=False).root == tmp_path / 'models'
    monkeypatch.setenv('MODELS_DIR', str(tmp_path / 'provisioned'))
    assert ModelRegistry(builtins=False).root == tmp_path / 'provisioned'


def test_every_registered_model_is_resolved_by_category(tmp_path, no_hub):
    registry = ModelRegistry(tmp_path)
    assert len(registry.list()) == 16
    paths = {f'{spec.category}/{spec.name}': provision(registry, spec.category, spec.name)
             for spec in registry.list()}
    assert registry.download_all() == paths
    for spec in registry.list():
        assert registry.weights_path(spec.category, spec.name) == tmp_path / spec.category / spec.name
        registry.verify(spec.category, spec.name)
    no_hub.assert_not_called()


def test_partial_directory_never_falls_back_to_hub(tmp_path, no_hub):
    registry = ModelRegistry(tmp_path)
    path = provision(registry, 'ocr', 'rapidocr_v4')
    (path / registry.get('ocr', 'rapidocr_v4').source.files[0]).unlink()
    assert not registry.is_prepared('ocr', 'rapidocr_v4')
    with pytest.raises(FileNotFoundError):
        registry.load('ocr', 'rapidocr_v4')
    with pytest.raises(FileNotFoundError):
        registry.download('ocr', 'rapidocr_v4')
    no_hub.assert_not_called()


def test_files_and_directories_cannot_escape_the_model_root(tmp_path, local):
    registry = ModelRegistry(tmp_path / 'models')
    model = registry.weights_path('layout', 'heron')
    model.parent.mkdir(parents=True)
    model.symlink_to(local.local_path)
    with pytest.raises(ValueError, match='escapes MODELS_DIR'):
        registry.weights_path('layout', 'heron')
    model.unlink()
    provision(registry, 'layout', 'heron')
    weight = model / 'model.safetensors'
    weight.unlink()
    weight.symlink_to(local.local_path / local.files[0])
    with pytest.raises(ValueError, match='escapes its weights directory'):
        registry.verify('layout', 'heron')


def test_local_training_weights_are_used_in_place_without_cache_copy(tmp_path, local):
    registry = ModelRegistry(tmp_path, builtins=False)
    registry.register('ocr', 'fixture', FixtureOcr, local)
    assert registry.download('ocr', 'fixture') == local.local_path
    model = registry.load('ocr', 'fixture', device='cuda:2')
    assert model.model_path == local.local_path
    assert model.predict(Image.new('RGB', (8, 8))) == 'recognized:8:cuda:2'
    assert list(registry.root.iterdir()) == [local.local_path]
    assert model.model_identity['weights']['files'][0]['sha256'] == hashlib.sha256(b'local weights').hexdigest()


def test_runtime_cache_hit_has_no_disk_or_sdk_access(tmp_path, local, monkeypatch):
    registry = ModelRegistry(tmp_path, builtins=False)
    registry.register('ocr', 'fixture', FixtureOcr, local)
    model = registry.load('ocr', 'fixture')
    def unexpected(*a, **k):
        pytest.fail('Cached runtime accessed disk or SDK')
    monkeypatch.setattr(Path, 'stat', unexpected)
    monkeypatch.setattr(Path, 'open', unexpected)
    assert registry.load('ocr', 'fixture') is model


def test_explicit_preparation_preserves_unchanged_runtime_and_invalidates_changes(tmp_path, local):
    registry = ModelRegistry(tmp_path, builtins=False)
    registry.register('ocr', 'fixture', FixtureOcr, local)
    model = registry.load('ocr', 'fixture')
    registry.download('ocr', 'fixture')
    assert registry.load('ocr', 'fixture') is model
    (local.local_path / local.files[0]).write_bytes(b'replaced weights')
    registry.download('ocr', 'fixture')
    assert registry.load('ocr', 'fixture') is not model


@pytest.mark.parametrize('category', [None, 'layout', 'picture_classifier', 'ocr', 'vlm'])
def test_unified_cli_checks_only_local_files(tmp_path, monkeypatch, no_hub, capsys, category):
    monkeypatch.setenv('MODELS_DIR', str(tmp_path))
    registry = ModelRegistry()
    args = ['--category', category] if category else []
    assert provisioning.main(args) == 1
    for spec in registry.list(category):
        provision(registry, spec.category, spec.name)
    assert provisioning.main(args) == 0
    for spec in registry.list(category):
        assert (tmp_path / spec.category / spec.name).is_dir()
    assert str(tmp_path) in capsys.readouterr().out
    no_hub.assert_not_called()
