"""Contract, interchangeability and inference-boundary tests; no downloaded weights."""

import subprocess
import sys
from types import SimpleNamespace

import pytest
import torch
from PIL import Image

from pdfparse.models.picture_classifier import (
    DocumentClassifierModel, CategoryScore, DFCV1PictureClassifier,
    DFCV25PictureClassifier, PictureClassification, TransformersPictureClassifier,
    create_picture_classifier,
)


def test_import_is_independent_and_public_exports_are_available():
    subprocess.run([sys.executable, "-c", """
import sys
import pdfparse.models.picture_classifier
assert not any(name in sys.modules for name in (
    'torch', 'torchvision', 'transformers', 'pdfparse.enrich', 'docling', 'docling_core'
))
import pdfparse
assert pdfparse.enrich.HandlerEnrichModel is not None
from pdfparse import enrich
assert enrich is pdfparse.enrich
try:
    pdfparse.missing_attribute
except AttributeError:
    pass
else:
    raise AssertionError('Unknown package attribute must fail')
"""], check=True)


class FixtureClassifier(DocumentClassifierModel):
    def _predict_batch(self, images):
        return [PictureClassification("fixture", (CategoryScore(image.mode, 1.0),))
                for image in images]


def test_abstract_contract_and_order(tmp_path):
    with pytest.raises(TypeError):
        DocumentClassifierModel()
    model = FixtureClassifier(model_path=tmp_path)
    images = [Image.new("RGB", (4, 4)), Image.new("L", (4, 4))]
    assert [r.best.label for r in model.predict_batch(images)] == ["RGB", "L"]
    assert model.predict(images[0]).best.confidence == 1.0
    assert model.predict_batch([]) == []
    with pytest.raises(TypeError, match="PIL"):
        model.predict("crop.png")
    with pytest.raises(ValueError, match="positive"):
        model.predict(Image.new("RGB", (0, 4)))


@pytest.mark.parametrize("scores", [
    (), (CategoryScore("a", .7),),
    (CategoryScore("a", .3), CategoryScore("b", .7)),
    (CategoryScore("a", .5), CategoryScore("a", .5)),
])
def test_result_invariants(scores):
    with pytest.raises(ValueError):
        PictureClassification("fixture", scores)


@pytest.mark.parametrize("score", [float("nan"), float("inf"), -.1, 1.1])
def test_invalid_confidence(score):
    with pytest.raises(ValueError, match="confidence"):
        CategoryScore("chart", score)


def test_partial_results_rejected(tmp_path):
    class BrokenClassifier(DocumentClassifierModel):
        def _predict_batch(self, images):
            return []
    with pytest.raises(ValueError, match="one result"):
        BrokenClassifier(model_path=tmp_path).predict(Image.new("RGB", (2, 2)))


@pytest.fixture
def fake_transformers(monkeypatch):
    import transformers

    state = SimpleNamespace(loads=0, calls=0, logits=None, label_map={1: "photograph", 0: "chart"})

    class Runtime:
        def __init__(self):
            self.config = SimpleNamespace(id2label=state.label_map, num_labels=2)

        def to(self, device):
            assert device == "cpu"
            return self

        def eval(self):
            return self

        def __call__(self, **inputs):
            assert not torch.is_grad_enabled()
            state.calls += 1
            state.inputs = inputs["pixel_values"]
            size = len(state.inputs)
            return SimpleNamespace(logits=state.logits if state.logits is not None
                                   else torch.tensor([[1., 3.]]).repeat(size, 1))

    def load_model(path, **kwargs):
        assert kwargs == {"local_files_only": True, "trust_remote_code": False}
        state.loads += 1
        return Runtime()

    def load_processor(path, **kwargs):
        assert kwargs == {"local_files_only": True, "trust_remote_code": False}

        def process(images, return_tensors):
            assert all(image.mode == "RGB" for image in images)
            assert return_tensors == "pt"
            return {"pixel_values": torch.zeros(len(images), 3, 224, 224)}
        return process

    monkeypatch.setattr(transformers.AutoModelForImageClassification, "from_pretrained", load_model)
    monkeypatch.setattr(transformers.AutoImageProcessor, "from_pretrained", load_processor)
    return state


@pytest.mark.parametrize("name,expected", [
    ("dfc_v1", DFCV1PictureClassifier), ("dfc_v25", DFCV25PictureClassifier),
])
def test_models_interchangeable_and_load_once(tmp_path, fake_transformers, name, expected):
    model = create_picture_classifier(name, model_path=tmp_path)
    assert isinstance(model, expected)
    assert model.predict_batch([]) == []
    assert fake_transformers.calls == 0
    images = [Image.new("L", (40, 30)), Image.new("RGBA", (20, 50))]
    results = model.predict_batch(images)
    assert len(results) == 2
    assert results[0].best.label == "photograph"
    assert results[0].scores[1].label == "chart"  # index order, not dict insertion order
    assert results[0].best.confidence == pytest.approx(.880797, abs=1e-6)
    assert results[0].model_id == model.model_id
    assert images[0].mode == "L"
    model.predict(images[0])
    assert fake_transformers.loads == 1
    assert fake_transformers.calls == 2
    if name == "dfc_v1":
        assert fake_transformers.inputs.shape == (1, 3, 224, 224)
        assert fake_transformers.inputs[0, 0, 0, 0].item() == pytest.approx(-.485 / .47853944)


def test_default_selection_and_invalid_model(tmp_path, fake_transformers):
    assert isinstance(create_picture_classifier(model_path=tmp_path), DFCV25PictureClassifier)
    with pytest.raises(ValueError):
        create_picture_classifier("unknown", model_path=tmp_path)


@pytest.mark.parametrize("logits", [torch.zeros(2, 2), torch.zeros(1, 3),
                                    torch.tensor([[float("nan"), 0.]])])
def test_invalid_runtime_output(tmp_path, fake_transformers, logits):
    model = create_picture_classifier(model_path=tmp_path)
    fake_transformers.logits = logits
    with pytest.raises(ValueError):
        model.predict(Image.new("RGB", (10, 10)))


def test_generic_backend_and_invalid_mapping(tmp_path, fake_transformers):
    model = TransformersPictureClassifier(model_path=tmp_path, model_id="my-fine-tune")
    assert model.predict(Image.new("RGB", (4, 4))).model_id == "my-fine-tune"
    fake_transformers.label_map = {0: "a", 2: "b"}
    with pytest.raises(ValueError, match="output index"):
        create_picture_classifier(model_path=tmp_path)
