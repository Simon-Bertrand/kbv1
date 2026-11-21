import inspect
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from docling.datamodel.accelerator_options import AcceleratorOptions
from docling.datamodel.base_models import InputFormat, Page
from docling.datamodel.pipeline_options import PdfPipelineOptions
from docling.document_converter import create_pipeline_options_hash
from docling.models.base_layout_model import BaseLayoutModel
from docling.models.factories import get_layout_factory
from docling_core.types.doc import CoordOrigin, DocItemLabel, Size
from PIL import Image
from pydantic import ValidationError

from pdfparse.models.layout import (
    BaseLayoutDetector,
    Detection,
)
from pdfparse.models.layout.options import DoclingLayoutOptions
from pdfparse.models.layout.model import DoclingLayoutAdapter
from tests.layout_detectors import AlternativeDetector, FixtureDetector


@pytest.mark.parametrize('label', [DocItemLabel.TABLE, DocItemLabel.DOCUMENT_INDEX])
@pytest.mark.parametrize('native', [False, True])
def test_spatial_ocr_fills_scanned_tables_with_real_coordinates(options, monkeypatch, label, native):
    from docling.datamodel.base_models import Cluster
    from docling_core.types.doc import BoundingBox
    from docling_core.types.doc.page import BoundingRectangle, TextCell
    from pdfparse.models.ocr import BaseSpatialOcrModel, OcrRegion

    class SpatialOcr(BaseSpatialOcrModel):
        calls = 0
        def predict(self, image):
            raise AssertionError('A table requires spatial OCR')
        def predict_regions(self, image):
            self.calls += 1
            assert image.size == (160, 200)
            return [OcrRegion('Loan', (0, 0, 100, 20)), OcrRegion('500,000.00', (120, 0, 160, 20))]

    ocr = SpatialOcr(model_path=options.model_path.parent)
    model = DoclingLayoutAdapter(None, AcceleratorOptions(), options.model_copy(update={'text_ocr': ocr}))
    bbox = BoundingBox(l=10, t=20, r=90, b=120, coord_origin=CoordOrigin.TOPLEFT)
    cells = [TextCell(index=7, text='Native table', orig='Native table', from_ocr=False,
                      rect=BoundingRectangle.from_bounding_box(bbox))] if native else []
    p = Page.model_construct(page_no=1, size=Size(width=100, height=200),
                             parsed_page=SimpleNamespace(textline_cells=cells, has_lines=bool(cells)))
    monkeypatch.setattr(Page, 'get_image', lambda *args, **kwargs: Image.new('RGB', (160, 200)))
    cluster = Cluster(id=1, label=label, confidence=.9, bbox=bbox)
    result = model._recognize_text(p, [cluster])
    assert ocr.calls == (0 if native else 1)
    if native:
        assert [c.text for c in result[0].cells] == ['Native table']
    else:
        assert [c.text for c in p.cells] == ['Loan', '500,000.00']
        assert [c.rect.to_bounding_box().as_tuple() for c in p.cells] == [(10, 20, 60, 30), (70, 20, 90, 30)]
        assert all(c.from_ocr for c in p.cells)
        assert len({c.index for c in p.cells}) == 2


@pytest.mark.parametrize('scale', [1, 3])
def test_scanned_index_keeps_separate_rows_and_numeric_columns(scale):
    from docling.datamodel.base_models import Cluster
    from docling_core.types.doc import BoundingBox
    from docling_core.types.doc.page import BoundingRectangle, TextCell
    from pdfparse.models.layout.model import _spatial_index_table

    def box(l, t, r, b):
        return BoundingBox(l=l * scale, t=t * scale, r=r * scale, b=b * scale, coord_origin=CoordOrigin.TOPLEFT)
    values = [('First balance', box(0, 0, 45, 10)), ('1,234.56', box(50, 1, 70, 11)),
              ('Second balance', box(0, 20, 45, 30)), ('12,00', box(55, 20, 70, 30)),
              ('$1,246.56', box(75, 40, 100, 50)), ('0.00', box(80, 60, 100, 70))]
    cells = [TextCell(index=i, text=text, orig=text, from_ocr=True,
                      rect=BoundingRectangle.from_bounding_box(bbox)) for i, (text, bbox) in enumerate(values)]
    cluster = Cluster(id=9, label=DocItemLabel.DOCUMENT_INDEX, bbox=box(0, 0, 100, 80), cells=cells[::-1])
    table = _spatial_index_table(cluster, 2)
    assert (table.num_rows, table.num_cols, table.page_no) == (4, 3, 2)
    assert [(c.start_row_offset_idx, c.start_col_offset_idx, c.text) for c in table.table_cells] == [
        (0, 0, 'First balance'), (0, 1, '1,234.56'), (1, 0, 'Second balance'), (1, 1, '12,00'),
        (2, 2, '$1,246.56'), (3, 2, '0.00'),
    ]
    assert all(c.bbox is not None and c.col_span == c.row_span == 1 for c in table.table_cells)
    cells[0].from_ocr = False
    assert _spatial_index_table(cluster, 2) is None


@pytest.fixture
def options(tmp_path):
    weights = tmp_path / "layout.weights"
    weights.touch()
    return DoclingLayoutOptions(model_path=weights, detector_class=FixtureDetector)


@pytest.fixture
def model(options):
    return DoclingLayoutAdapter(None, AcceleratorOptions(), options)


def page(image=True, size=True, page_no=1):
    p = Page(page_no=page_no, size=Size(width=100, height=200) if size else None)
    if image:
        p._image_cache[1.0] = Image.new("RGB", (200, 400))
    return p


def test_abstract_contract():
    assert inspect.isabstract(BaseLayoutDetector)
    assert issubclass(DoclingLayoutAdapter, BaseLayoutModel)
    with pytest.raises(TypeError):
        BaseLayoutDetector(model_path=Path("unused"), device="cpu")


def test_prediction_contract(model):
    model.detector = Mock()
    model.detector.predict_batch.return_value = [[
        Detection(DocItemLabel.TEXT, 0.9, (20, 40, 100, 120)),
        Detection(DocItemLabel.PICTURE, 0.35, (-20, 200, 240, 450)),
        Detection(DocItemLabel.TEXT, 0.8, (250, 0, 300, 50)),
        Detection(DocItemLabel.TEXT, 0.8, (100, 50, 20, 100)),
        Detection(DocItemLabel.TEXT, 0.349, (0, 0, 30, 30)),
        Detection(DocItemLabel.TEXT, 0.8, (0, 0, 0, 30)),
    ]]
    predictions = model.predict_layout(Mock(), [page(), page(image=False), page(size=False)])
    assert len(predictions) == 3
    assert predictions[1].clusters == predictions[2].clusters == []
    text, picture = predictions[0].clusters
    assert (text.id, picture.id) == (0, 1)
    assert (text.label, picture.label) == (DocItemLabel.TEXT, DocItemLabel.PICTURE)
    assert (text.confidence, picture.confidence) == (0.9, 0.35)
    assert text.bbox.as_tuple() == (10, 20, 50, 60)
    assert picture.bbox.as_tuple() == (0, 100, 100, 200)
    assert text.bbox.coord_origin == picture.bbox.coord_origin == CoordOrigin.TOPLEFT
    assert text.cells == picture.cells == []
    model.detector.predict_batch.assert_called_once()


def test_page_order_and_docling_call(model):
    pages = [page(page_no=7), page(image=False, page_no=3), page(page_no=9)]
    returned = list(model(Mock(), iter(pages)))
    assert returned == pages
    assert [len(p.predictions.layout.clusters) for p in returned] == [2, 0, 2]
    assert model.predict_layout(Mock(), []) == []


def test_native_batch_preserves_page_order_and_skips_missing_images(model):
    pages = [page(page_no=7), page(image=False, page_no=3), page(page_no=9)]
    model.detector = Mock()
    model.detector.predict_batch.return_value = [
        [Detection(DocItemLabel.TEXT, 0.9, (0, 0, 10, 10))],
        [Detection(DocItemLabel.PICTURE, 0.8, (0, 0, 20, 20))],
    ]
    predictions = model.predict_layout(Mock(), pages)
    assert predictions[0].clusters[0].label == DocItemLabel.TEXT
    assert predictions[1].clusters == []
    assert predictions[2].clusters[0].label == DocItemLabel.PICTURE
    model.detector.predict.assert_not_called()
    assert model.detector.predict_batch.call_count == 1
    assert len(model.detector.predict_batch.call_args.args[0]) == 2
    model.detector.predict_batch.return_value = [[]]
    with pytest.raises(ValueError):
        model.predict_layout(Mock(), pages)


def test_transformers_layout_uses_one_inference_for_mixed_size_images():
    from pdfparse.models.layout.transformers import TransformersLayoutDetector

    detector = object.__new__(TransformersLayoutDetector)
    detector.device = "cpu"
    detector.labels = {0: DocItemLabel.TEXT}
    detector.processor = Mock(return_value=SimpleNamespace(to=lambda device: {"pixels": "batch"}))
    detector.runtime = Mock()
    detection = {name: Mock() for name in ("labels", "scores", "boxes")}
    for name, value in (("labels", [0]), ("scores", [0.9]), ("boxes", [[0, 0, 10, 20]])):
        detection[name].tolist.return_value = value
    detector.processor.post_process_object_detection.return_value = [detection, detection]
    images = [Image.new("RGB", (20, 40)), Image.new("RGB", (60, 30))]
    results = detector.predict_batch(images)
    assert len(results) == 2 and all(result[0].label == DocItemLabel.TEXT for result in results)
    detector.runtime.assert_called_once_with(pixels="batch")
    assert detector.processor.post_process_object_detection.call_args.kwargs["target_sizes"] == [(40, 20), (30, 60)]
    assert detector.predict_batch([]) == []
    assert detector.runtime.call_count == 1
    detector.processor.post_process_object_detection.return_value = [detection]
    with pytest.raises(ValueError, match="one result"):
        detector.predict_batch(images)


@pytest.mark.parametrize("score", [float("nan"), float("inf"), -0.1, 1.1])
def test_invalid_confidence(model, score):
    model.detector = Mock()
    model.detector.predict_batch.return_value = [[Detection(DocItemLabel.TEXT, score, (0, 0, 10, 10))]]
    with pytest.raises(ValueError, match="confidence"):
        model.predict_layout(Mock(), [page()])


def test_invalid_label(model):
    model.detector = Mock()
    model.detector.predict_batch.return_value = [[Detection("text", 0.9, (0, 0, 10, 10))]]
    with pytest.raises(TypeError, match="label"):
        model.predict_layout(Mock(), [page()])


@pytest.mark.parametrize("coordinate", [float("nan"), float("inf"), -float("inf")])
def test_invalid_bbox(model, coordinate):
    model.detector = Mock()
    model.detector.predict_batch.return_value = [[Detection(DocItemLabel.TEXT, 0.9, (0, 0, coordinate, 10))]]
    with pytest.raises(ValueError, match="Non-finite"):
        model.predict_layout(Mock(), [page()])


def test_inference_error_propagates(model):
    model.detector = Mock()
    model.detector.predict_batch.side_effect = RuntimeError("backend inference failed")
    with pytest.raises(RuntimeError, match="backend inference failed"):
        model.predict_layout(Mock(), [page()])


def test_missing_model(options):
    options.model_path.unlink()
    with pytest.raises(FileNotFoundError):
        DoclingLayoutAdapter(None, AcceleratorOptions(), options)


def test_plugin_discovery_and_external_opt_in(options):
    get_layout_factory.cache_clear()
    external = get_layout_factory(allow_external_plugins=True)
    assert "pdfparse_custom_layout" in external.registered_kind
    instance = external.create_instance(
        options=options, artifacts_path=None, accelerator_options=AcceleratorOptions(),
        enable_remote_services=False,
    )
    assert isinstance(instance, DoclingLayoutAdapter)
    assert isinstance(instance.detector, FixtureDetector)
    internal = get_layout_factory(allow_external_plugins=False)
    assert "pdfparse_custom_layout" not in internal.registered_kind
    with pytest.raises(RuntimeError, match="No class found"):
        internal.create_instance(options=options)
    default = PdfPipelineOptions().layout_options
    assert type(default) is not DoclingLayoutOptions
    assert type(default) in internal.classes


def test_serialization_and_pipeline_cache(options):
    restored = DoclingLayoutOptions.model_validate_json(options.model_dump_json())
    assert restored.detector_class is FixtureDetector
    assert restored == options
    first = PdfPipelineOptions(layout_options=options)
    def key(**changes):
        return create_pipeline_options_hash(PdfPipelineOptions(
            layout_options=options.model_copy(update=changes)))
    assert create_pipeline_options_hash(first) == key()
    assert key(detector_class=AlternativeDetector) != key()
    assert key(detector_settings={"variant": "large"}) != key()
    assert key(confidence_threshold=0.8) != key()
    assert key(model_path=Path("other.weights")) != key()


@pytest.mark.parametrize("detector", [BaseLayoutDetector, "pathlib:Path", "not_a_real_module:Detector"])
def test_invalid_detector_configuration(options, detector):
    with pytest.raises(ValidationError):
        DoclingLayoutOptions(model_path=options.model_path, detector_class=detector)


@pytest.mark.parametrize("threshold", [-0.1, 1.1, float("nan")])
def test_invalid_threshold(options, threshold):
    with pytest.raises(ValidationError):
        DoclingLayoutOptions(model_path=options.model_path, detector_class=FixtureDetector,
                            confidence_threshold=threshold)


def test_detector_loaded_once_and_receives_configuration(options, monkeypatch):
    init = Mock()
    def tracked_init(self, **kwargs):
        init(**kwargs)
        BaseLayoutDetector.__init__(self, **kwargs)
    monkeypatch.setattr(FixtureDetector, "__init__", tracked_init)
    options.detector_settings["batch_size"] = 4
    accelerator = AcceleratorOptions()
    instance = DoclingLayoutAdapter(Path("artifacts"), accelerator, options)
    detector = Mock(return_value=[])
    monkeypatch.setattr(instance.detector, "predict", detector)
    instance.predict_layout(Mock(), [page(), page()])
    instance.predict_layout(Mock(), [page()])
    init.assert_called_once_with(model_path=options.model_path, device="cpu",
                                 settings={"batch_size": 4})
    assert detector.call_count == 3


@pytest.mark.parametrize('native', [False, True])
def test_text_ocr_injection_is_shared_excluded_from_json_and_distinguishes_cache(tmp_path, native):
    from pdfparse import PdfParseConfiguration
    from tests.test_model_registry import FixtureOcr
    detector = None if native else FixtureDetector(model_path=tmp_path)
    first, second = FixtureOcr(model_path=tmp_path), FixtureOcr(model_path=tmp_path)
    configurations = [PdfParseConfiguration(cache=False, layout=detector, text_ocr=ocr) for ocr in (first, second)]
    options = [config.create_extractor()._converter.format_to_options[InputFormat.PDF].pipeline_options
               for config in configurations]
    assert options[0].layout_options.text_ocr is first
    assert options[0].model_copy(deep=True).layout_options.text_ocr is first
    assert 'text_ocr' not in options[0].layout_options.model_dump(mode='json')
    assert create_pipeline_options_hash(options[0]) != create_pipeline_options_hash(options[1])
    if native:
        from pdfparse.models.layout.options import NativeOcrLayoutOptions
        assert isinstance(options[0].layout_options, NativeOcrLayoutOptions)
