"""High-level configuration, substitution and source-preserving Chonkie tests."""
from dataclasses import replace
from types import SimpleNamespace

from chonkie import TokenChunker
from docling_core.types.doc import (
    BoundingBox, DocItemLabel, DoclingDocument, ImageRef, PictureItem, ProvenanceItem, TextItem,
)
from PIL import Image
import pytest
from pydantic import BaseModel, ValidationError

from pdfparse import (
    BaseChunkingModel, BaseEnrichModel, BaseLayoutModel, BasePictureEnrichModel,
    ChonkieChunkingModel, NativeLayoutModel, EnrichedDoclingDocument,
    HandlerEnrichModel, PdfParseConfiguration, PdfParsePipeline, PictureEnrichModel,
    UnhandledPicturePolicy,
)
from pdfparse.enrich import EnrichedPictureItem, MemoryAssetStore, create_default_handler_registry
from pdfparse.enrich.handlers import handle_text
from pdfparse.ingestion import BasePdfExtractor
from pdfparse.models.layout.options import DoclingLayoutOptions
from tests.layout_detectors import FixtureDetector
from tests.test_layout_pipeline import make_pdf
from tests.test_picture_routing import Classifier, Rule


class Extractor(BasePdfExtractor):
    def __init__(self, document, events=None):
        self.document = document
        self.events = events if events is not None else []

    def extract(self, pdfpath):
        self.events.append("extract")
        return self.document


@pytest.fixture
def source(tmp_path):
    path = tmp_path / "source.pdf"
    path.write_bytes(b"identity")
    document = DoclingDocument(name="fixture")
    for page in (1, 2):
        document.add_text(label=DocItemLabel.TEXT, text="été " * 8, prov=ProvenanceItem(
            page_no=page, bbox=BoundingBox(l=0, t=0, r=10, b=10), charspan=(0, 32)))
    return path, document


def test_progress_observers_are_scoped_and_reset_after_failure(source):
    from pdfparse.progress import Stage, observe_progress, report_progress
    events, nested = [], []
    pipeline = PdfParsePipeline(PdfParseConfiguration(cache=False), extractor=Extractor(source[1]))
    with observe_progress(lambda stage, detail: events.append(stage)):
        with pytest.raises(RuntimeError, match='observer scope'):
            with observe_progress(lambda stage, detail: nested.append(stage)):
                pipeline.run(source[0])
                raise RuntimeError('observer scope')
        report_progress(Stage.RENDERING)
    report_progress(Stage.LOADING)
    assert nested == [Stage.EXTRACTION, Stage.ENRICHMENT, Stage.CHUNKING]
    assert events == [Stage.RENDERING]


def test_defaults_and_cross_element_chonkie_provenance(source):
    pipeline = PdfParsePipeline(PdfParseConfiguration(cache=False), extractor=Extractor(source[1]))
    result = pipeline.run(source[0])
    assert isinstance(result.document, EnrichedDoclingDocument)
    assert len(result.chunks) == 1
    chunk = result.chunks[0]
    assert chunk.document_id == result.document.document_id
    assert len(chunk.source_refs) == 2
    assert {loc.page_no for loc in chunk.locations} == {1, 2}
    assert chunk.text == "été " * 8 + "\n\n" + "été " * 8
    assert pipeline.run(source[0]).chunks == result.chunks
    assert result.document.from_dict(result.document.to_dict()) == result.document


@pytest.mark.parametrize('async_mode', [False, True])
def test_archived_pdf_uses_its_own_bytes_and_keeps_the_logical_cache(tmp_path, source, monkeypatch, async_mode):
    import asyncio
    from concurrent.futures import ThreadPoolExecutor
    import hashlib
    import json

    from pdfparse.cli import selected_pdfs
    from pdfparse.ingestion.cache import load_cached_run, remember_runtime_configuration

    logical, document = source
    original = logical.read_bytes()
    digest = hashlib.sha256(original).hexdigest()
    versions = tmp_path / 'versions'
    versions.mkdir()
    archived = versions / f'{digest}.pdf'
    archived.write_bytes(original)
    monkeypatch.setenv('SOURCE_VERSIONS_DIR', str(versions))
    manifest = tmp_path / 'files.json'
    manifest.write_text(json.dumps({str(logical): digest}))
    logical.write_bytes(b'replacement bytes')

    reads = []

    class ArchiveExtractor(Extractor):
        def extract(self, path):
            reads.append(path.read_bytes())
            return super().extract(path)

    pipeline = PdfParsePipeline(PdfParseConfiguration(cache_identity={'fixture': 'snapshot'}),
                                extractor=ArchiveExtractor(document))
    physical = selected_pdfs(tmp_path, manifest)[logical]
    if async_mode:
        class Scheduler:
            async def run_cpu(self, fn, *args):
                return fn(*args)

        with ThreadPoolExecutor() as bridge:
            result = asyncio.run(pipeline.run_async(logical, scheduler=Scheduler(), bridge_executor=bridge,
                                                    source_path=physical))
    else:
        result = pipeline.run(logical, source_path=physical)
    assert reads == [original]
    assert result.document.pdfpath == logical
    assert result.document.document_id == digest
    assert {chunk.document_id for chunk in result.chunks} == {digest}

    runtime = {'test': 'archived'}
    remember_runtime_configuration(logical, pipeline, runtime, source_path=physical)
    assert load_cached_run(logical, runtime) is None
    logical.write_bytes(original)
    assert load_cached_run(logical, runtime).chunks == result.chunks


def test_stage_order_substitution_and_picture_isolation(source):
    events = []
    source[1].add_picture(image=ImageRef.from_pil(Image.new("RGB", (8, 8)), dpi=72))
    classifier = Classifier()

    class Enrich(BaseEnrichModel):
        def __call__(self, item, *, ctx):
            assert not isinstance(item, PictureItem)
            events.append("enrich")
            return HandlerEnrichModel()(item, ctx=ctx)

    class Pictures(BasePictureEnrichModel):
        def __call__(self, item, *, ctx):
            events.append("picture")
            return PictureEnrichModel(classifier=classifier, routes={}, unhandled="keep")(item, ctx=ctx)

    class Chunking(BaseChunkingModel):
        def chunk(self, document):
            events.append("chunk")
            return [replace(chunk, text='Alternative strategy', transformation_id='other.v1',
                            start_index=None, end_index=None)
                    for chunk in ChonkieChunkingModel().chunk(document)]

    result = PdfParsePipeline(PdfParseConfiguration(cache=False, 
        enrich=Enrich(), picture_enrich=Pictures(), chunking=Chunking(),
    ), extractor=Extractor(source[1], events)).run(source[0])
    assert events[0] == "extract" and events[-1] == "chunk"
    assert events.count("picture") == classifier.calls == 1
    assert result.chunks[0].text == 'Alternative strategy'


@pytest.mark.parametrize('failure', [
    'identity', 'references', 'duplicate', 'version', 'geometry', 'graph_geometry',
    'extra_geometry', 'empty_text', 'blank_id', 'blank_version', 'duplicate_refs',
])
def test_chunker_must_preserve_identity_version_unique_ids_and_geometry(source, failure):
    if failure == 'graph_geometry':
        from docling_core.types.doc.document import GraphCell, GraphData
        source[1].add_form(graph=GraphData(cells=[GraphCell(
            cell_id=1, label='value', text='Located form', orig='Located form',
            prov=ProvenanceItem(page_no=3, bbox=BoundingBox(l=1, t=2, r=3, b=4), charspan=(0, 12)))]))

    class InvalidChunker(BaseChunkingModel):
        def chunk(self, document):
            chunk = ChonkieChunkingModel().chunk(document)[0]
            if failure == 'duplicate':
                return [chunk, chunk]
            updates = {
                'identity': {'document_id': 'wrong'},
                'references': {'source_refs': ()},
                'version': {'transformation_id': ''},
                'geometry': {'locations': ()},
                'graph_geometry': {'locations': tuple(loc for loc in chunk.locations if loc.page_no != 3)},
                'extra_geometry': {'locations': (*chunk.locations, replace(chunk.locations[0], page_no=99))},
                'empty_text': {'text': ' \n\t'},
                'blank_id': {'chunk_id': ' '},
                'blank_version': {'transformation_id': ' '},
                'duplicate_refs': {'source_refs': chunk.source_refs * 2},
            }
            return [replace(chunk, **updates[failure])]

    message = {'identity': 'document identity', 'references': 'source references', 'duplicate': 'unique',
               'version': 'transformation', 'geometry': 'geometry', 'graph_geometry': 'geometry',
               'extra_geometry': 'unrelated', 'empty_text': 'nonempty text', 'blank_id': 'nonempty',
               'blank_version': 'transformation', 'duplicate_refs': 'references must be unique'}[failure]
    with pytest.raises(ValueError, match=message):
        PdfParsePipeline(PdfParseConfiguration(cache=False, chunking=InvalidChunker()),
                         extractor=Extractor(source[1])).run(source[0])


@pytest.mark.parametrize("field", ["layout", "enrich", "picture_enrich", "page_enrich", "chunking", "asset_store", "wrappers"])
def test_invalid_configuration_fails_early(field):
    with pytest.raises(ValidationError) as error:
        PdfParseConfiguration(cache=False, **{field: object()})
    assert error.value.errors()[0]["loc"] == (field,)


def test_abstract_contracts_and_immutable_configuration():
    for contract in (BaseLayoutModel, BaseEnrichModel, BasePictureEnrichModel, BaseChunkingModel):
        with pytest.raises(TypeError):
            contract()
    config = PdfParseConfiguration(cache=False)
    assert isinstance(config, BaseModel)
    with pytest.raises(ValidationError, match="frozen_instance"):
        config.layout = None
    other = PdfParseConfiguration(cache=False)
    assert other.enrich is not config.enrich
    assert other.asset_store is config.asset_store is None


def test_handler_snapshot_and_no_picture_in_ordinary_enrichment(source):
    from docling_core.types.doc import TextItem
    handlers = create_default_handler_registry()
    def custom(item, *, ctx):
        return replace(handle_text(item, ctx=ctx), content_for_embedding="custom")
    handlers.register(TextItem, custom, replace=True)
    enrich = HandlerEnrichModel(handlers)
    with pytest.raises(TypeError, match="picture_enrich"):
        handlers.register(PictureItem, custom)
    handlers.register(TextItem, handle_text, replace=True)
    result = PdfParsePipeline(PdfParseConfiguration(cache=False, enrich=enrich),
                             extractor=Extractor(source[1])).run(source[0])
    assert result.chunks[0].text == "custom\n\ncustom"
    with pytest.raises(TypeError, match="picture_enrich"):
        enrich(PictureItem(self_ref="#/pictures/0"), ctx=None)


def test_handler_mapping_overrides_defaults_and_snapshots_configuration(source):
    from docling_core.types.doc import TextItem

    source[1].add_text(label=DocItemLabel.TITLE, text="Native title")
    def custom(item, *, ctx):
        return replace(handle_text(item, ctx=ctx), content_for_embedding="custom")
    overrides = {TextItem: custom}
    enrich = HandlerEnrichModel(handlers=overrides)
    overrides.clear()
    result = PdfParsePipeline(PdfParseConfiguration(cache=False, enrich=enrich),
                             extractor=Extractor(source[1])).run(source[0])
    assert result.chunks[0].text == "custom\n\ncustom\n\nNative title"
    with pytest.raises(RuntimeError, match="frozen"):
        enrich.handlers.register(TextItem, handle_text, replace=True)


@pytest.mark.parametrize("overrides, message", [
    ({PictureItem: handle_text}, "picture_enrich"),
    ({str: handle_text}, "NodeItem"),
    ({TextItem: 42}, "callable"),
])
def test_invalid_handler_mapping_fails_at_construction(overrides, message):
    with pytest.raises(TypeError, match=message):
        HandlerEnrichModel(handlers=overrides)


def test_chonkie_splits_unicode_repeated_text_and_overlap(source):
    chunking = ChonkieChunkingModel(TokenChunker(tokenizer="character", chunk_size=12, chunk_overlap=4),
                                    transformation_id="fixture.overlap.v1")
    pipeline = PdfParsePipeline(PdfParseConfiguration(cache=False, chunking=chunking), extractor=Extractor(source[1]))
    result = pipeline.run(source[0])
    assert len(result.chunks) > 2
    assert len({chunk.chunk_id for chunk in result.chunks}) == len(result.chunks)
    assert all(len(chunk.text) <= 12 for chunk in result.chunks)
    assert all(ref.resolve(result.document) for chunk in result.chunks for ref in chunk.source_refs)
    assert any(len(chunk.source_refs) == 2 for chunk in result.chunks)
    assert result.chunks == pipeline.run(source[0]).chunks
    alternate = ChonkieChunkingModel(chunk_size=12).chunk(result.document)
    assert alternate[0].chunk_id != result.chunks[0].chunk_id


def test_chonkie_empty_and_invalid_offsets(source):
    empty = DoclingDocument(name="empty")
    assert PdfParsePipeline(PdfParseConfiguration(cache=False), extractor=Extractor(empty)).run(source[0]).chunks == ()
    class BadOffsets:
        def chunk(self, text):
            return [SimpleNamespace(text="wrong", start_index=0, end_index=4)]
    with pytest.raises(ValueError, match="transformation_id"):
        ChonkieChunkingModel(BadOffsets())
    pipeline = PdfParsePipeline(PdfParseConfiguration(cache=False, chunking=ChonkieChunkingModel(
        BadOffsets(), transformation_id="bad.v1")), extractor=Extractor(source[1]))
    with pytest.raises(ValueError, match="exact character offsets"):
        pipeline.run(source[0])


@pytest.mark.parametrize('start, end', [(False, 1), (0, True)])
def test_chonkie_rejects_boolean_offsets(source, start, end):
    class BooleanOffsets:
        def chunk(self, text):
            return [SimpleNamespace(text=text[start:end], start_index=start, end_index=end)]

    pipeline = PdfParsePipeline(PdfParseConfiguration(cache=False, chunking=ChonkieChunkingModel(
        BooleanOffsets(), transformation_id='invalid.offsets.v1')), extractor=Extractor(source[1]))
    with pytest.raises(ValueError, match='exact character offsets'):
        pipeline.run(source[0])


def test_picture_crop_reused_and_metadata_roundtrip(source, monkeypatch):
    source[1].add_picture(image=ImageRef.from_pil(Image.new("RGB", (8, 8)), dpi=72))
    original = PictureItem.get_image
    calls = []
    def counted(item, doc):
        calls.append(item.self_ref)
        return original(item, doc)
    monkeypatch.setattr(PictureItem, "get_image", counted)
    classifier = Classifier()
    store = MemoryAssetStore()
    config = PdfParseConfiguration(cache=False, picture_enrich=PictureEnrichModel(
        classifier=classifier, routes={"chart": Rule(ocr=lambda image: "axis", vlm=lambda image, text: "chart")},
        unhandled=UnhandledPicturePolicy.ERROR), asset_store=store)
    result = PdfParsePipeline(config, extractor=Extractor(source[1])).run(source[0])
    assert len(calls) == classifier.calls == 1
    picture = next(item for item in result.document.elements.values() if isinstance(item, EnrichedPictureItem))
    assert picture.asset_path and picture.classification["ocr_text"] == "axis"
    assert result.document.from_dict(result.document.to_dict()) == result.document


def test_default_configuration_does_not_retain_crops(source, monkeypatch):
    source[1].add_picture(image=ImageRef.from_pil(Image.new("RGB", (8, 8)), dpi=72))
    def unexpected(*args, **kwargs):
        pytest.fail("Native wrapping without storage must not retrieve a crop")
    monkeypatch.setattr(PictureItem, "get_image", unexpected)
    pipeline = PdfParsePipeline(PdfParseConfiguration(cache=False), extractor=Extractor(source[1]))
    for _ in range(2):
        result = pipeline.run(source[0])
        assert all(item.asset_path is None for item in result.document.elements.values()
                   if isinstance(item, EnrichedPictureItem))


@pytest.mark.parametrize("change", ["missing", "page", "bbox", "origin", "charspan"])
def test_handler_cannot_change_source_geometry(source, change):
    def lossy(item, *, ctx):
        result = handle_text(item, ctx=ctx)
        if change == "missing":
            return replace(result, locations=())
        updates = {"page": {"page_no": 99}, "bbox": {"bbox": (1, 2, 3, 4)},
                   "origin": {"coord_origin": "OTHER"}, "charspan": {"charspan": (1, 2)}}
        return replace(result, locations=(replace(result.locations[0], **updates[change]),))
    pipeline = PdfParsePipeline(PdfParseConfiguration(cache=False, enrich=HandlerEnrichModel({TextItem: lossy})),
                                extractor=Extractor(source[1]))
    with pytest.raises(ValueError, match="source geometry"):
        pipeline.run(source[0])


@pytest.mark.parametrize("change", ["parent", "children", "order", "detached"])
def test_handler_cannot_change_source_relationships(source, change):
    native = HandlerEnrichModel()
    class InvalidEnrich(BaseEnrichModel):
        def __call__(self, item, *, ctx):
            result = native(item, ctx=ctx)
            if change == "parent":
                return replace(result, parent_ref=None)
            if change == "children":
                return replace(result, children_refs=())
            if change == "order":
                return replace(result, children_refs=tuple(reversed(result.children_refs)))
            return replace(result, parent_ref=None, children_refs=())
    pipeline = PdfParsePipeline(PdfParseConfiguration(cache=False, enrich=InvalidEnrich()),
                                extractor=Extractor(source[1]))
    with pytest.raises(ValueError, match="source parent/child relationships"):
        pipeline.run(source[0])


@pytest.mark.parametrize('scanned', [False, True])
def test_high_level_real_docling_without_ocr(tmp_path, scanned):
    path = tmp_path / "source.pdf"
    make_pdf(path, scanned=scanned)
    options = DoclingLayoutOptions(model_path=tmp_path, detector_class=FixtureDetector)
    layout = NativeLayoutModel(options)
    options.confidence_threshold = 1.0
    assert layout.get_options().confidence_threshold != 1.0
    result = PdfParsePipeline(PdfParseConfiguration(cache=False, layout=layout)).run(path)
    assert result.document.elements
    assert any("Hello layout pipeline" in chunk.text for chunk in result.chunks) == (not scanned)
    assert result.chunks == tuple(ChonkieChunkingModel().chunk(result.document))
    assert result.page_numbers == (1,)
    assert result.extraction_evidence_available
    assert all(region.page_no == 1 for region in result.extraction)
    if not scanned:
        assert any(region.text_origin == 'native' and region.source_refs for region in result.extraction)
    quality = result.quality_report()
    assert quality.page_inventory_complete
    assert quality.pages_without_indexable_text == ((1,) if scanned else ())


def test_errors_stop_downstream_stages(source):
    class FailingExtractor(BasePdfExtractor):
        def extract(self, pdfpath):
            raise RuntimeError("extraction failed")
    class UnexpectedChunking(BaseChunkingModel):
        def chunk(self, document):
            pytest.fail("chunking must not run after a failure")
    pipeline = PdfParsePipeline(PdfParseConfiguration(cache=False, chunking=UnexpectedChunking()), extractor=FailingExtractor())
    with pytest.raises(RuntimeError, match="extraction failed"):
        pipeline.run(source[0])


def test_one_validation_and_no_registry_copies_per_document(source, monkeypatch):
    from unittest.mock import Mock
    from pdfparse.enrich import EnrichedDocument, HandlerRegistry

    pipeline = PdfParsePipeline(PdfParseConfiguration(cache=False), extractor=Extractor(source[1]))
    validate = Mock(side_effect=EnrichedDocument.validate)
    copies = Mock(side_effect=AssertionError("Registry copied during processing"))
    monkeypatch.setattr(EnrichedDocument, "validate", lambda document: validate(document))
    monkeypatch.setattr(HandlerRegistry, "copy", copies)
    for _ in range(2):
        result = pipeline.run(source[0])
        assert result.chunks
    assert validate.call_count == 2
    copies.assert_not_called()


def test_picture_model_cannot_return_an_ordinary_wrapper(source):
    from pdfparse.enrich.handlers import handle_picture
    from pdfparse.enrich import EnrichedTextItem

    source[1].add_picture(image=ImageRef.from_pil(Image.new("RGB", (8, 8)), dpi=72))
    class InvalidPictures(BasePictureEnrichModel):
        def __call__(self, item, *, ctx):
            picture = handle_picture(item, ctx=ctx)
            return EnrichedTextItem(
                pdfpath=picture.pdfpath, document_id=picture.document_id,
                element_type=picture.element_type, element_ref=picture.element_ref,
                source_class=picture.source_class, source_label=picture.source_label,
                content_layer=picture.content_layer, parent_ref=picture.parent_ref,
                original_text="wrong wrapper", content_for_embedding=None, content_for_llm="",
            )
    pipeline = PdfParsePipeline(PdfParseConfiguration(cache=False, picture_enrich=InvalidPictures()),
                             extractor=Extractor(source[1]))
    with pytest.raises(TypeError, match="EnrichedPictureItem"):
        pipeline.run(source[0])


def test_pydantic_configuration_rejects_unknown_fields_and_aggregates_errors():
    with pytest.raises(ValidationError) as error:
        PdfParseConfiguration.model_validate({"enrich": None, "chunking": {}, "unexpected": True})
    errors = {entry["loc"]: entry["type"] for entry in error.value.errors()}
    assert errors == {("enrich",): "is_instance_of", ("chunking",): "is_instance_of",
                      ("unexpected",): "extra_forbidden"}


def test_pydantic_configuration_preserves_injected_service_identity():
    original = PdfParseConfiguration(cache=False)
    rebuilt = PdfParseConfiguration.model_validate({
        name: getattr(original, name) for name in type(original).model_fields
    })
    assert all(getattr(rebuilt, name) is getattr(original, name)
               for name in type(original).model_fields)
    assert PdfParseConfiguration(cache=False, layout=None, asset_store=None).asset_store is None


@pytest.mark.parametrize('from_environment', [False, True])
def test_pdfparse_uses_an_external_cache_for_a_read_only_corpus(tmp_path, source, monkeypatch, from_environment):
    from pdfparse.ingestion.cache import cache_info, load_cached_run, remember_runtime_configuration
    from pdfparse.ingestion.preparation import cache_configuration

    corpus = tmp_path / 'corpus'
    corpus.mkdir()
    logical = source[0].rename(corpus / 'source.pdf')
    cache = tmp_path / 'external'
    configuration = {'cache_identity': {'fixture': 'external-cache'}}
    if from_environment:
        monkeypatch.setenv('PDFPARSE_CACHE_DIR', str(cache))
    else:
        configuration['cache_dir'] = cache

    engine = PdfParsePipeline(PdfParseConfiguration(**configuration), extractor=Extractor(source[1]))
    adjacent = PdfParsePipeline(PdfParseConfiguration(cache_identity=configuration['cache_identity']),
                                extractor=Extractor(source[1]))
    assert cache_configuration(engine)[0] == cache_configuration(adjacent)[0]
    runtime = {'test': 'external'}
    corpus.chmod(0o555)
    try:
        result = engine.run(logical)
        remember_runtime_configuration(logical, engine, runtime)
        restored = load_cached_run(logical, runtime, cache_dir=cache)
        assert restored.chunks == result.chunks
        assert cache_info(logical, cache_dir=cache)['entries'] == 1
        assert not logical.with_name('source.pdf.pdfparsecache').exists()
    finally:
        corpus.chmod(0o755)
