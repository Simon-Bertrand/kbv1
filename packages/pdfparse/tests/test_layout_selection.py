"""Selection, label mapping and backend boundaries without loading real weights."""

import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from docling.datamodel.pipeline_options import LayoutObjectDetectionOptions, PdfPipelineOptions
from docling.document_converter import create_pipeline_options_hash
from docling_core.types.doc import DocItemLabel
from PIL import Image
from pydantic import ValidationError

from pdfparse.models.layout import LayoutModel
from pdfparse.models.layout.options import DoclingLayoutOptions, get_layout_options
from pdfparse.models.layout.pp_doclayout_s import PADDLE_LABELS, PPDocLayoutSDetector
from pdfparse.models.layout import HeronLayoutDetector, Heron101LayoutDetector
from pdfparse import NativeLayoutModel


def test_three_choices_and_native_default():
    assert [choice.value for choice in LayoutModel] == ["pp_doclayout_s", "heron", "heron_101"]
    default = get_layout_options()
    assert isinstance(default, LayoutObjectDetectionOptions)
    assert default == PdfPipelineOptions().layout_options
    assert default.model_spec.repo_id == "docling-project/docling-layout-heron"
    accurate = get_layout_options("heron_101")
    assert accurate.model_spec.repo_id == "docling-project/docling-layout-heron-101"
    assert default.model_spec.repo_id == "docling-project/docling-layout-heron"


def test_native_layout_configuration_accepts_a_preset_without_sdk_options():
    layout = NativeLayoutModel("heron_101")
    first = layout.get_options()
    first.engine_options.score_threshold = 0.9
    assert layout.get_options().engine_options.score_threshold == 0.3
    assert layout.get_options().model_spec.repo_id == "docling-project/docling-layout-heron-101"
    assert NativeLayoutModel().get_options() == get_layout_options()
    with pytest.raises(ValueError):
        NativeLayoutModel("unknown")


@pytest.mark.parametrize("choice,backend", [
    ("pp_doclayout_s", PPDocLayoutSDetector),
    ("heron", HeronLayoutDetector),
    ("heron_101", Heron101LayoutDetector),
])
def test_local_model_selection(tmp_path, choice, backend):
    assert get_layout_options(choice, model_path=tmp_path).confidence_threshold == 0.35
    options = get_layout_options(choice, model_path=tmp_path, confidence_threshold=0.6)
    assert isinstance(options, DoclingLayoutOptions)
    assert options.model_path == tmp_path
    assert options.detector_class is backend
    assert options.confidence_threshold == 0.6
    assert DoclingLayoutOptions.model_validate_json(options.model_dump_json()) == options


def test_native_confidence_and_cache():
    first = PdfPipelineOptions(layout_options=get_layout_options())
    second = PdfPipelineOptions(layout_options=get_layout_options("heron_101"))
    assert create_pipeline_options_hash(first) != create_pipeline_options_hash(second)
    changed = get_layout_options("heron_101", confidence_threshold=0.7)
    assert changed.engine_options.score_threshold == 0.7
    assert get_layout_options("heron_101").engine_options.score_threshold == 0.3


@pytest.mark.parametrize("score", [-0.1, 1.1, float("nan"), float("inf")])
def test_invalid_native_threshold(score):
    with pytest.raises(ValidationError):
        get_layout_options(confidence_threshold=score)


def test_invalid_selection():
    with pytest.raises(ValueError):
        get_layout_options("unknown_model")
    with pytest.raises(ValueError, match="model_path"):
        get_layout_options("pp_doclayout_s")


@pytest.fixture
def paddle_detector(tmp_path, monkeypatch):
    for name in ("inference.json", "inference.yml", "inference.pdiparams"):
        (tmp_path / name).touch()
    runtime = Mock()
    constructor = Mock(return_value=runtime)
    monkeypatch.setitem(sys.modules, "paddleocr", SimpleNamespace(LayoutDetection=constructor))
    detector = PPDocLayoutSDetector(
        model_path=tmp_path,
        device="cpu", settings={"cpu_threads": 2},
    )
    constructor.assert_called_once_with(
        model_name="PP-DocLayout-S", model_dir=str(tmp_path), device="cpu",
        cpu_threads=2, enable_mkldnn=False,
    )
    return detector


def test_paddle_real_label_vocabulary_and_rgb_conversion(paddle_detector):
    labels = ["paragraph_title", "image", "text", "number", "abstract", "content",
              "figure_title", "formula", "table", "table_title", "reference", "doc_title",
              "footnote", "header", "algorithm", "footer", "seal", "chart_title", "chart",
              "formula_number", "header_image", "footer_image", "aside_text"]
    assert set(labels) <= PADDLE_LABELS.keys()
    paddle_detector.runtime.predict.return_value = [{"boxes": [
        {"label": name, "score": 0.9, "coordinate": [10, 20, 30, 40]} for name in labels
    ]}]
    results = paddle_detector.predict(Image.new("RGB", (40, 60), (255, 0, 0)))
    assert len(results) == 23
    assert all(isinstance(det.label, DocItemLabel) for det in results)
    assert results[0].xyxy == (10, 20, 30, 40)
    assert results[0].label == DocItemLabel.SECTION_HEADER
    assert results[1].label == DocItemLabel.PICTURE
    assert results[3].label == DocItemLabel.PAGE_FOOTER
    image = paddle_detector.runtime.predict.call_args.args[0]
    assert image[0, 0].tolist() == [0, 0, 255]


def test_paddle_unknown_label_raises(paddle_detector):
    paddle_detector.runtime.predict.return_value = [{"boxes": [
        {"label": "unknown_class", "score": 0.9, "coordinate": [0, 0, 1, 1]}
    ]}]
    with pytest.raises(ValueError, match="Unmapped"):
        paddle_detector.predict(Image.new("RGB", (100, 100)))


@pytest.mark.parametrize("count", [0, 2])
def test_paddle_prediction_cardinality(paddle_detector, count):
    paddle_detector.runtime.predict.return_value = [{"boxes": []}] * count
    with pytest.raises(ValueError, match="one Paddle"):
        paddle_detector.predict(Image.new("RGB", (100, 100)))


def test_paddle_missing_bundle_file(tmp_path):
    with pytest.raises(FileNotFoundError, match="inference.json"):
        PPDocLayoutSDetector(model_path=tmp_path, device="cpu")
