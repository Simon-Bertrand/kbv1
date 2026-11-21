"""Real PDF conversion; detector weights are replaced by a deterministic backend."""

import hashlib
import json
import os
import shutil
from pathlib import Path

import pytest
from docling.datamodel.accelerator_options import AcceleratorDevice, AcceleratorOptions
from docling.datamodel.base_models import ConversionStatus, InputFormat
from docling.datamodel.pipeline_options import PdfPipelineOptions
from docling.document_converter import DocumentConverter, PdfFormatOption
from docling_core.types.doc import PictureItem, DoclingDocument
from PIL import Image, ImageDraw, ImageFont
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

from pdfparse.ingestion import DoclingPdfExtractor
from pdfparse.enrich import FileSystemAssetStore, EnrichedDocument, wrap_document
from pdfparse.models.layout.options import DoclingLayoutOptions
from pdfparse.models.layout.model import DoclingLayoutAdapter
from tests.layout_detectors import FixtureDetector


def make_pdf(path, *, scanned=False, pages=1):
    pdf = canvas.Canvas(str(path), pagesize=(300, 400))
    for number in range(pages):
        if scanned:
            image = Image.new("RGB", (900, 1200), "white")
            draw = ImageDraw.Draw(image)
            font = ImageFont.truetype("DejaVuSans.ttf", 42)
            draw.text((60, 120), "Hello layout pipeline", fill="black", font=font)
            draw.rectangle((100, 650, 800, 1000), fill="blue")
            pdf.drawImage(ImageReader(image), 0, 0, width=300, height=400)
        else:
            pdf.drawString(20, 350, f"Hello layout pipeline page {number + 1}")
            picture = Image.new("RGB", (100, 100), "blue")
            pdf.drawImage(ImageReader(picture), 30, 40, width=240, height=160)
        pdf.showPage()
    pdf.save()


def converter(tmp_path, **overrides):
    weights = tmp_path / "layout.weights"
    weights.touch()
    options = PdfPipelineOptions(
        do_ocr=False,
        do_table_structure=False,
        generate_picture_images=True,
        allow_external_plugins=True,
        accelerator_options=AcceleratorOptions(device=AcceleratorDevice.CPU, num_threads=2),
        layout_options=DoclingLayoutOptions(model_path=weights, detector_class=FixtureDetector),
    )
    for key, value in overrides.items():
        setattr(options, key, value)
    return DocumentConverter(format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=options)})


@pytest.mark.parametrize("scanned,pages", [(False, 1), (True, 1), (False, 3)])
def test_standard_pipeline_and_enrichment(tmp_path, scanned, pages):
    source = tmp_path / "fixture.pdf"
    make_pdf(source, scanned=scanned, pages=pages)
    engine = converter(tmp_path)
    result = engine.convert(source)
    assert result.status == ConversionStatus.SUCCESS
    assert not result.errors
    assert len(result.document.pages) == pages
    assert len(result.document.pictures) == pages
    assert all(isinstance(picture, PictureItem) for picture in result.document.pictures)
    assert isinstance(next(iter(engine.initialized_pipelines.values())).layout_model, DoclingLayoutAdapter)
    if not scanned:
        assert "Hello layout pipeline" in result.document.export_to_markdown()

    # The plugin does not own the output directory: exercise the existing caller
    # convention with unchanged enrich API and verify actual assets and JSON.
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    output = tmp_path / "output" / digest
    output.mkdir(parents=True)
    shutil.copyfile(source, output / "source.pdf")
    enriched = wrap_document(output / "source.pdf", result.document, asset_store=FileSystemAssetStore(output / "assets"))
    enriched.save_json(output / "enriched.json")
    assert enriched.document_id == digest
    assert (output / "source.pdf").read_bytes() == source.read_bytes()
    assert len(list((output / "assets").glob("*.png"))) == pages
    restored = EnrichedDocument.from_dict(json.loads((output / "enriched.json").read_text()))
    restored.validate()
    assert restored.document_id == digest


@pytest.mark.skipif(not os.getenv("PDFPARSE_TEST_MODEL_INTEGRATION"),
                    reason="Requires local Docling default model weights")
def test_default_layout_conversion(tmp_path):
    source = tmp_path / "default.pdf"
    make_pdf(source)
    options = PdfPipelineOptions(
        do_ocr=False, do_table_structure=False,
        accelerator_options=AcceleratorOptions(device=AcceleratorDevice.CPU, num_threads=2),
    )
    extractor = DoclingPdfExtractor(options=options)
    result = extractor.extract(source)
    assert "Hello layout pipeline" in result.export_to_markdown()


@pytest.mark.skipif(os.getenv("PDFPARSE_TEST_RAPIDOCR_MODELS") != "1",
                    reason="Requires provisioned RapidOCR ONNX weights")
def test_real_rapidocr_recognizes_text():
    from pdfparse.models import ModelRegistry
    model = ModelRegistry().load("ocr", "rapidocr_v4")
    from PIL import ImageDraw
    image = Image.new("RGB", (320, 80), "white")
    ImageDraw.Draw(image).text((20, 20), "Hello 123", fill="black", font_size=28)
    assert "123" in model.predict(image)


@pytest.mark.parametrize('scanned,pages,recognized', [(True, 2, 'Corps de lettre détecté.'),
                                                    (False, 1, 'Ne doit pas remplacer le texte natif.'),
                                                    (True, 1, ''), (True, 1, 42)])
def test_crop_ocr_retains_scanned_body_without_global_ocr(tmp_path, scanned, pages, recognized):
    from pdfparse import PdfParseConfiguration, PdfParsePipeline
    from pdfparse.ingestion.docling import DoclingPdfExtractor
    from pdfparse.models.ocr import BaseOcrModel
    from tests.layout_detectors import DuplicateTextDetector

    class Ocr(BaseOcrModel):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            self.crops = []
        def predict(self, image):
            self.crops.append(image.size)
            return recognized

    source = tmp_path / 'letter.pdf'
    make_pdf(source, scanned=scanned, pages=pages)
    ocr = Ocr(model_path=tmp_path)
    detector = DuplicateTextDetector(model_path=tmp_path)
    config = PdfParseConfiguration(cache=False, layout=detector, text_ocr=ocr)
    layout = detector.get_options().model_copy(update={'text_ocr': ocr, 'text_ocr_key': f'{id(ocr):x}'})
    options = PdfPipelineOptions(do_ocr=False, do_table_structure=False,
                                accelerator_options=AcceleratorOptions(device=AcceleratorDevice.CPU, num_threads=2))
    extractor = DoclingPdfExtractor(layout=layout, options=options)
    pipeline = PdfParsePipeline(config, extractor=extractor)
    if not isinstance(recognized, str):
        with pytest.raises(RuntimeError, match='Text-region OCR must return a string'):
            pipeline.run(source)
        assert len(ocr.crops) == 1
        return
    result = pipeline.run(source)
    texts = [element for element in result.document.elements.values() if element.element_type.value == 'text']
    assert not extractor._converter.format_to_options[InputFormat.PDF].pipeline_options.do_ocr
    assert len([element for element in result.document.elements.values()
                if element.element_type.value == 'picture']) == pages
    assert all(element.content_for_llm.strip() for element in texts)
    if not scanned:
        assert not ocr.crops
        assert any('Hello layout pipeline' in element.content_for_llm for element in texts)
    elif recognized:
        assert ocr.crops == [(600, 320)] * pages
        assert len(texts) == pages
        assert [element.page_no for element in texts] == list(range(1, pages + 1))
        assert all(element.content_for_llm == recognized for element in texts)
        assert all(element.original_text == recognized for element in texts)
        assert all(element.bbox == (0, 240, 300, 400) for element in texts)
        assert recognized in '\n'.join(chunk.text for chunk in result.chunks)
        assert any(region.text_origin == 'ocr' and region.source_refs and region.ocr_engine for region in result.extraction)
        assert all(region.recognition_confidence is None for region in result.extraction)
        assert any('ocr' in record['text_origins'].values() for record in result.index_records())
        restored = pipeline.codec.from_dict(json.loads(json.dumps(pipeline.codec.to_dict(result.document))))
        assert [element.content_for_llm for element in restored.elements.values() if element.element_type.value == 'text'] == [recognized] * pages
    else:
        assert ocr.crops == [(600, 320)]
        assert not texts
        assert not result.chunks


def test_scanned_index_spatial_ocr_reaches_table_chunks_and_provenance(tmp_path, monkeypatch):
    from docling.models.stages.table_structure.table_structure_model import TableStructureModel
    from pdfparse import PdfParseConfiguration, PdfParsePipeline
    from pdfparse.models.ocr import BaseSpatialOcrModel, OcrRegion
    from pdfparse.models.layout.options import SpatialTableOptions
    from tests.layout_detectors import ScannedIndexDetector

    class Ocr(BaseSpatialOcrModel):
        calls = 0
        def predict(self, image):
            raise AssertionError('Do not flatten the table')
        def predict_regions(self, image):
            self.calls += 1
            w, h = image.size
            return [OcrRegion(text, (l*w, t*h, r*w, b*h)) for text, l, t, r, b in (
                ('Loan', .1, .1, .6, .15), ('500,000.00', .7, .1, .9, .15),
                ('Capital', .1, .2, .6, .25), ('3,700,000.00', .7, .2, .9, .25),
            )]

    def initialize(self, enabled, options, **kwargs):
        self.enabled, self.options, self.scale = enabled, options, 2.0
        # No TableFormer weights are needed: both scanned index rows have OCR geometry.
    monkeypatch.setattr(TableStructureModel, '__init__', initialize)
    source = tmp_path / 'scan.pdf'
    make_pdf(source, scanned=True)
    ocr = Ocr(model_path=tmp_path)
    pipeline = PdfParsePipeline(PdfParseConfiguration(cache=False, 
        layout=ScannedIndexDetector(model_path=tmp_path), text_ocr=ocr))
    result = pipeline.run(source)
    tables = [item for item in result.document.elements.values() if item.element_type.value == 'table']
    assert ocr.calls == 1
    assert len(tables) == 1 and (tables[0].num_rows, tables[0].num_cols) == (2, 2)
    assert '500,000.00' in tables[0].content_for_llm
    assert '3,700,000.00' in '\n'.join(chunk.text for chunk in result.chunks)
    assert any(region.text_origin == 'ocr' and region.source_refs for region in result.extraction)
    assert any('ocr' in record['text_origins'].values() for record in result.index_records())
    options = pipeline.extractor._converter.format_to_options[InputFormat.PDF].pipeline_options
    assert isinstance(options.table_structure_options, SpatialTableOptions)
    assert not options.do_ocr
    restored = pipeline.codec.from_dict(json.loads(json.dumps(pipeline.codec.to_dict(result.document))))
    restored.validate()


def test_native_layout_recognizes_scan_text_and_structured_table_without_global_ocr(tmp_path, monkeypatch):
    from docling.datamodel.base_models import Cluster, LayoutPrediction
    from docling.models.stages.layout.layout_object_detection_model import LayoutObjectDetectionModel
    from docling.models.stages.table_structure.table_structure_model import TableStructureModel
    from docling_core.types.doc import BoundingBox, CoordOrigin, DocItemLabel, TableCell, TableData
    from pdfparse import PdfParseConfiguration, PdfParsePipeline
    from pdfparse.models.ocr import BaseTableOcrModel

    native_devices = []
    def initialize_layout(self, *, accelerator_options, options, **kwargs):
        native_devices.append(accelerator_options.device)
        assert type(options).__name__ == 'LayoutObjectDetectionOptions'
    def predict_layout(self, conv_res, pages):
        return [LayoutPrediction(clusters=[
            Cluster(id=index, label=label, confidence=.99, bbox=BoundingBox(
                l=0, t=top * page.size.height, r=page.size.width, b=bottom * page.size.height,
                coord_origin=CoordOrigin.TOPLEFT))
            for index, (label, top, bottom) in enumerate([
                (DocItemLabel.TEXT, 0., .2), (DocItemLabel.TABLE, .2, 1.),
            ])]) for page in pages]
    def initialize_tables(self, *, enabled, options, **kwargs):
        self.enabled, self.options, self.scale = enabled, options, 2.
    monkeypatch.setattr(LayoutObjectDetectionModel, '__init__', initialize_layout)
    monkeypatch.setattr(LayoutObjectDetectionModel, 'predict_layout', predict_layout)
    monkeypatch.setattr(TableStructureModel, '__init__', initialize_tables)

    class Ocr(BaseTableOcrModel):
        crops = []
        def predict(self, image):
            self.crops.append(('text', image.size))
            return 'Bilan de la banque'
        def predict_table(self, image):
            self.crops.append(('table', image.size))
            return TableData(num_rows=1, num_cols=2, table_cells=[
                TableCell(text=text, start_row_offset_idx=0, end_row_offset_idx=1,
                          start_col_offset_idx=col, end_col_offset_idx=col + 1)
                for col, text in enumerate(['Loan', '500,000.00'])])

    source = tmp_path / 'native-scan.pdf'
    make_pdf(source, scanned=True)
    ocr = Ocr(model_path=tmp_path, device='cuda:0')
    pipeline = PdfParsePipeline(PdfParseConfiguration(cache=False, text_ocr=ocr))
    options = pipeline.extractor._converter.format_to_options[InputFormat.PDF].pipeline_options
    assert options.model_copy(deep=True).table_structure_options.table_ocr is ocr
    assert 'table_ocr' not in options.table_structure_options.model_dump(mode='json')
    from pdfparse.ingestion.preparation import describe
    assert 'table_ocr_key' not in describe(options.table_structure_options)['options']
    result = pipeline.run(source)
    assert pipeline.configuration.layout is None
    assert native_devices == [AcceleratorDevice.CPU]
    assert ocr.crops == [('text', (600, 160)), ('table', (600, 640))]
    assert not options.do_ocr
    assert 'Bilan de la banque' in result.document.to_markdown()
    assert '500,000.00' in '\n'.join(chunk.text for chunk in result.chunks)
    assert any(region.label == 'table' and region.text_origin == 'ocr' for region in result.extraction)
    for item in result.document.elements.values():
        if item.element_type.value in ('text', 'table'):
            assert item.content_for_llm.strip()
    restored = pipeline.codec.from_dict(json.loads(json.dumps(pipeline.codec.to_dict(result.document))))
    restored.validate()


@pytest.mark.parametrize('flag', ['do_ocr', 'do_picture_classification', 'do_picture_description',
                                  'do_chart_extraction', 'do_code_enrichment', 'do_formula_enrichment'])
def test_extractor_rejects_docling_bypasses(flag):
    options = {'do_ocr': False, flag: True}
    with pytest.raises(ValueError, match='do_ocr=False' if flag == 'do_ocr' else 'handlers'):
        DoclingPdfExtractor(options=PdfPipelineOptions(**options))


def test_native_docling_defaults_to_cpu_and_preserves_explicit_devices():
    default = DoclingPdfExtractor()._converter.format_to_options[InputFormat.PDF].pipeline_options
    assert default.accelerator_options.device == AcceleratorDevice.CPU
    explicit = PdfPipelineOptions(do_ocr=False, accelerator_options=AcceleratorOptions(device=AcceleratorDevice.CUDA))
    configured = DoclingPdfExtractor(options=explicit)._converter.format_to_options[InputFormat.PDF].pipeline_options
    assert configured.accelerator_options.device == AcceleratorDevice.CUDA


def test_partial_conversion_is_rejected(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from docling.datamodel.base_models import ConversionStatus
    source = tmp_path / "source.pdf"
    source.write_bytes(b"source")
    options = PdfPipelineOptions(do_ocr=False)
    extractor = DoclingPdfExtractor(options=options)
    assert not options.generate_picture_images  # The caller options remain unchanged.
    monkeypatch.setattr(extractor._converter, "convert", lambda path: SimpleNamespace(
        status=ConversionStatus.PARTIAL_SUCCESS, errors=["page conversion failed"],
        document=DoclingDocument(name="partial")))
    with pytest.raises(RuntimeError, match="Incomplete Docling conversion"):
        extractor.extract(source)



def test_real_conversion_supports_separate_representations_wrappers_and_assets(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[3] / "ressources"))
    from examples.enrichment_experiments import ExperimentTextItem, build_experiment
    from pdfparse.enrich import EnrichedPictureItem

    path = tmp_path / "source.pdf"
    make_pdf(path, pages=2)
    converted = converter(tmp_path).convert(path)
    assert converted.status == ConversionStatus.SUCCESS
    original = converted.document.model_dump_json()
    baseline_store = FileSystemAssetStore(tmp_path / "baseline" / "assets")
    variant_store = FileSystemAssetStore(tmp_path / "variant" / "assets")
    baseline = build_experiment("verbatim", lambda text: text, baseline_store)
    variant = build_experiment("uppercase", str.upper, variant_store)
    first = baseline.enrich_document(path, converted.document)
    second = variant.enrich_document(path, converted.document)
    assert first.document_id == second.document_id
    assert first.elements.keys() == second.elements.keys()
    custom_texts = [item for item in first.elements.values() if type(item) is ExperimentTextItem]
    assert custom_texts
    for item in custom_texts:
        altered = second.get(item.element_ref)
        assert item.original_text == altered.original_text
        assert item.locations == altered.locations
        assert item.children_refs == altered.children_refs
        assert item.parent_ref == altered.parent_ref
        assert altered.content_for_embedding == item.content_for_embedding.upper()
        assert altered.representation_id == "uppercase"
    for picture in first.elements.values():
        if isinstance(picture, EnrichedPictureItem):
            assert baseline_store.get(picture.asset_path) == variant_store.get(picture.asset_path)
    baseline.codec.save(first, tmp_path / "baseline.json")
    variant.codec.save(second, tmp_path / "variant.json")
    assert baseline.codec.load(tmp_path / "baseline.json").to_dict(codec=baseline.codec) == first.to_dict(codec=baseline.codec)
    assert variant.codec.load(tmp_path / "variant.json").to_dict(codec=variant.codec) == second.to_dict(codec=variant.codec)
    assert converted.document.model_dump_json() == original



def test_native_text_inside_real_pdf_figure_is_not_transcribed_twice(tmp_path):
    from pdfparse import PdfParseConfiguration, PdfParsePipeline, OcrVlmPictureEnricher
    from pdfparse.enrich import PictureEnrichModel
    from pdfparse.models.ocr import BaseOcrModel
    from pdfparse.models.vlm import BaseVlmModel
    from tests.test_picture_routing import Classifier

    class Ocr(BaseOcrModel):
        def predict(self, image):
            pytest.fail('Native figure text must be reused without running OCR')

    class Vlm(BaseVlmModel):
        def predict(self, image, *, prompt=None):
            assert 'Pressure: 12 kPa' in prompt
            return 'Un diagramme bleu.'

    source = tmp_path / 'native-figure.pdf'
    pdf = canvas.Canvas(str(source), pagesize=(300, 400))
    pdf.drawImage(ImageReader(Image.new('RGB', (100, 100), 'blue')), 30, 40, width=240, height=160)
    pdf.drawString(60, 120, 'Pressure: 12 kPa')
    pdf.save()
    engine = converter(tmp_path)
    converted = engine.convert(source)
    figure = converted.document.pictures[0]
    assert figure.children
    assert any(getattr(ref.resolve(converted.document), 'text', '') == 'Pressure: 12 kPa'
               for ref in figure.children)
    rule = OcrVlmPictureEnricher(ocr=Ocr(model_path=tmp_path), vlm=Vlm(model_path=tmp_path),
                               prompt='Describe.', transformation_id='owned-native.v1')
    pipeline = PdfParsePipeline(PdfParseConfiguration(cache=False, 
        picture_enrich=PictureEnrichModel(classifier=Classifier(), routes={'chart': rule})))
    document = pipeline.enrich_document(source, converted.document)
    assert document.to_markdown().count('Pressure: 12 kPa') == 1
    assert '\n\n'.join(chunk.text for chunk in pipeline.chunk_document(document)).count('Pressure: 12 kPa') == 1
    text = next(item for item in document.elements.values()
                if getattr(item, 'original_text', '') == 'Pressure: 12 kPa')
    assert text.locations and text.parent_ref.cref == figure.self_ref


def test_real_pdf_page_images_feed_summaries_after_enrichment(tmp_path):
    from pdfparse import PdfParseConfiguration, PdfParsePipeline
    from pdfparse.enrich import BasePageEnrichModel
    class Pages(BasePageEnrichModel):
        model_id, transformation_id = 'fixture', 'page-descriptions.v1'
        calls = []
        def describe(self, image, *, markdown):
            assert image.width > 0 and image.height > 0
            self.calls.append(markdown)
            return 'un guide de test.'
    source = tmp_path / 'real-pages.pdf'
    make_pdf(source, pages=2)
    pages = Pages()
    detector = FixtureDetector(model_path=tmp_path)
    config = PdfParseConfiguration(cache=False, layout=detector, page_enrich=pages)
    from docling.datamodel.base_models import InputFormat
    created = config.create_extractor()._converter.format_to_options[InputFormat.PDF].pipeline_options
    assert created.generate_page_images and not created.do_ocr
    engine = converter(tmp_path, generate_page_images=True)
    converted = engine.convert(source)
    assert converted.status == ConversionStatus.SUCCESS
    assert all(page.image is not None for page in converted.document.pages.values())
    pipeline = PdfParsePipeline(config)
    document = pipeline.enrich_document(source, converted.document)
    assert len(pages.calls) == 2
    assert 'Hello layout pipeline page 1' in pages.calls[0] and 'page 2' not in pages.calls[0]
    assert 'Hello layout pipeline page 2' in pages.calls[1] and 'page 1' not in pages.calls[1]
    markdown = document.to_markdown()
    assert markdown.startswith('Page 1 du PDF real-pages.pdf : un guide de test.')
    assert markdown.count('Page 2 du PDF') == 1
    assert pipeline.chunk_document(document)
