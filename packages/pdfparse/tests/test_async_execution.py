"""Coroutine dependencies, per-document integrity and the synchronous Docling bridge."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from copy import copy

from docling_core.types.doc import DoclingDocument, ImageRef, Size
from PIL import Image
import pytest

from pdfparse import PdfParseConfiguration, PdfParsePipeline, StructuralChunkingModel
from pdfparse.enrich import OcrVlmPictureEnricher, PictureEnrichModel, VlmPageEnrichModel
from pdfparse.ingestion import BasePdfExtractor
from pdfparse.models.scheduling import ModelQueue, ResourceScheduler
from pdfparse.models.vlm.catalog import MODELS
from tests.test_backend_runtime import FigureClassifier
from tests.test_vlm import engine


class Figures(BasePdfExtractor):
    def __init__(self, count=8):
        self.count, self.calls = count, 0

    def extract(self, path):
        self.calls += 1
        document = DoclingDocument(name='async fixture')
        for _ in range(self.count):
            document.add_picture(image=ImageRef.from_pil(Image.new('RGB', (40, 30)), dpi=72))
        for number in range(1, 3):
            document.add_page(page_no=number, size=Size(width=100, height=100),
                image=ImageRef.from_pil(Image.new('RGB', (100, 100)), dpi=72))
        return document


def compose(tmp_path, engine, scheduler, *, pages=False):
    model = MODELS['qwen3_5_4b'].implementation(model_path=tmp_path,
        settings={'quantization': 'none', 'batch_size': 1, 'max_new_tokens': 16})
    model.processor.batch_decode = lambda rows, **kwargs: ['Un diagramme technique.'] * len(rows)
    classifier = FigureClassifier(model_path=tmp_path)
    def facade(model):
        proxy = copy(model)
        proxy._inference_queue = ModelQueue(model, scheduler=scheduler, batch_size=4, wait_seconds=.02)
        return proxy
    vlm, dfc = facade(model), facade(classifier)
    rule = OcrVlmPictureEnricher(ocr=None, vlm=vlm, prompt='Describe', transformation_id='fixture.v1')
    configuration = PdfParseConfiguration(cache_identity={'fixture': 'async-v1'},
        picture_enrich=PictureEnrichModel(classifier=dfc, routes={'line_chart': rule}, fallback=rule),
        page_enrich=VlmPageEnrichModel(vlm=vlm) if pages else None, chunking=StructuralChunkingModel())
    return configuration


def test_one_pdf_batches_its_figures_without_blocking_cpu_workers(tmp_path, engine):
    path = tmp_path / 'figures.pdf'
    path.write_bytes(b'fixture PDF')
    scheduler = ResourceScheduler(cpu_workers=1, cpu_threads=1)
    try:
        configuration = compose(tmp_path, engine, scheduler, pages=True)
        extractor = Figures()
        pipeline = PdfParsePipeline(configuration, extractor=extractor)
        async def process(bridge):
            first = await pipeline.run_async(path, scheduler=scheduler, bridge_executor=bridge)
            previous_calls = engine.calls
            second = await pipeline.run_async(path, scheduler=scheduler, bridge_executor=bridge)
            assert engine.calls == previous_calls
            assert first.chunks == second.chunks
            return first
        with ThreadPoolExecutor(1) as bridge:
            result = asyncio.run(process(bridge))
        assert extractor.calls == 1
        assert engine.calls < extractor.count + 2  # Figures/pages actually share inference calls.
        assert any(call['batch_size'] > 1 for call in result.metrics.inferences)
        assert len([element for element in result.document.elements.values()
                    if element.element_type.value == 'page_description']) == 2
        # The synchronous API reuses the same cache identity and result.
        assert pipeline.run(path).chunks == result.chunks
        assert extractor.calls == 1
    finally:
        scheduler.close()


def test_duplicate_pdf_paths_are_serialized_without_occupying_cpu_workers(tmp_path, engine):
    path = tmp_path / 'figures.pdf'
    path.write_bytes(b'fixture PDF')
    scheduler = ResourceScheduler(cpu_workers=1, cpu_threads=1)
    try:
        configuration = compose(tmp_path, engine, scheduler)
        extractors = [Figures(1), Figures(1)]
        pipelines = [PdfParsePipeline(configuration, extractor=extractor) for extractor in extractors]
        async def process(bridge):
            return await asyncio.gather(*(pipeline.run_async(path, scheduler=scheduler, bridge_executor=bridge)
                                          for pipeline in pipelines))
        with ThreadPoolExecutor(2) as bridge:
            results = asyncio.run(process(bridge))
        assert results[0].chunks == results[1].chunks
        assert sum(extractor.calls for extractor in extractors) == 1
    finally:
        scheduler.close()


def test_docling_bridge_routes_native_stages_through_shared_cpu_pool(tmp_path):
    from docling.datamodel.pipeline_options import PdfPipelineOptions
    from pdfparse.ingestion.docling import DoclingPdfExtractor
    from tests.layout_detectors import FixtureDetector
    from tests.test_layout_pipeline import make_pdf
    path = tmp_path / 'source.pdf'
    make_pdf(path)
    scheduler = ResourceScheduler(cpu_workers=1, cpu_threads=1)
    try:
        detector = FixtureDetector(model_path=tmp_path)
        facade = copy(detector)
        facade._inference_queue = ModelQueue(detector, scheduler=scheduler)
        extractor = DoclingPdfExtractor(layout=facade.get_options(), options=PdfPipelineOptions(
            do_ocr=False, do_table_structure=False))
        extractor._scheduler = scheduler
        pipeline = PdfParsePipeline(PdfParseConfiguration(layout=facade, cache_identity={'fixture': 'docling-async'}),
                                    extractor=extractor)
        with ThreadPoolExecutor(1) as bridge:
            result = asyncio.run(pipeline.run_async(path, scheduler=scheduler, bridge_executor=bridge))
        assert any('Hello layout pipeline' in chunk.text for chunk in result.chunks)
        assert extractor._scheduled
        assert scheduler.metrics['images'] >= 1
    finally:
        scheduler.close()


def test_cancelled_document_drains_inference_and_releases_cache_lock(tmp_path, engine):
    from threading import Event
    from filelock import FileLock
    path = tmp_path / 'cancelled.pdf'
    path.write_bytes(b'fixture PDF')
    scheduler = ResourceScheduler(cpu_workers=1, cpu_threads=1)
    try:
        configuration = compose(tmp_path, engine, scheduler)
        pipeline = PdfParsePipeline(configuration, extractor=Figures(1))
        model = configuration.picture_enrich.routes['line_chart'].vlm._inference_queue.model
        entered, release = Event(), Event()
        generate = model.runtime.generate
        def blocked_generation(**kwargs):
            entered.set()
            assert release.wait(5)
            return generate(**kwargs)
        model.runtime.generate = blocked_generation
        async def cancel(bridge):
            task = asyncio.create_task(pipeline.run_async(path, scheduler=scheduler, bridge_executor=bridge))
            assert await asyncio.to_thread(entered.wait, 5)
            task.cancel()
            asyncio.get_running_loop().call_later(.01, release.set)
            with pytest.raises(asyncio.CancelledError):
                await task
        with ThreadPoolExecutor(1) as bridge:
            asyncio.run(cancel(bridge))
        parent = path.with_name(path.name + '.pdfparsecache')
        assert not list(parent.glob('*/manifest.json'))
        assert not list(parent.glob('.cache-*'))
        with FileLock(parent / '.lock', timeout=0):
            pass
        assert scheduler.requests == scheduler.memory == 0
    finally:
        scheduler.close()


def test_docling_submits_multiple_text_regions_as_one_ocr_batch(tmp_path):
    from docling.datamodel.pipeline_options import PdfPipelineOptions
    from pdfparse.ingestion.docling import DoclingPdfExtractor
    from pdfparse.models.ocr import BaseOcrModel
    from tests.layout_detectors import TwoColumnDetector
    from tests.test_layout_pipeline import make_pdf
    path = tmp_path / 'scan.pdf'
    make_pdf(path, scanned=True)
    class BatchOCR(BaseOcrModel):
        def predict(self, image):
            raise AssertionError('Independent text regions must use predict_batch')
        def predict_batch(self, images):
            self.batches.append(len(images))
            return [f'Column {index}' for index in range(len(images))]
    scheduler = ResourceScheduler(cpu_workers=1, cpu_threads=1)
    try:
        detector = TwoColumnDetector(model_path=tmp_path)
        ocr = BatchOCR(model_path=tmp_path)
        ocr.batches = []
        detector_proxy, ocr_proxy = copy(detector), copy(ocr)
        detector_proxy._inference_queue = ModelQueue(detector, scheduler=scheduler)
        ocr_proxy._inference_queue = ModelQueue(ocr, scheduler=scheduler)
        options = detector_proxy.get_options().model_copy(update={'text_ocr': ocr_proxy, 'text_ocr_key': str(id(ocr_proxy))})
        extractor = DoclingPdfExtractor(layout=options, options=PdfPipelineOptions(do_ocr=False, do_table_structure=False))
        extractor._scheduler = scheduler
        pipeline = PdfParsePipeline(PdfParseConfiguration(layout=detector_proxy, text_ocr=ocr_proxy,
            cache_identity={'fixture': 'ocr-batching'}), extractor=extractor)
        with ThreadPoolExecutor(1) as bridge:
            result = asyncio.run(pipeline.run_async(path, scheduler=scheduler, bridge_executor=bridge))
        assert ocr.batches == [2]
        text = '\n'.join(chunk.text for chunk in result.chunks)
        assert 'Column 0' in text and 'Column 1' in text
    finally:
        scheduler.close()


def test_multiple_pdf_workers_share_one_docling_initialization_and_observers(tmp_path, monkeypatch):
    from docling.datamodel.pipeline_options import PdfPipelineOptions
    from docling.pipeline.standard_pdf_pipeline import StandardPdfPipeline
    from pdfparse.ingestion.docling import DoclingPdfExtractor
    from pdfparse.models.ocr import BaseOcrModel
    from tests.layout_detectors import TwoColumnDetector
    from tests.test_layout_pipeline import make_pdf
    count = []
    initialize = StandardPdfPipeline.__init__
    def initialized(self, *args, **kwargs):
        count.append(1)
        return initialize(self, *args, **kwargs)
    monkeypatch.setattr(StandardPdfPipeline, '__init__', initialized)
    class OCR(BaseOcrModel):
        def predict(self, image):
            return 'Recognized scanned text'
    paths = [tmp_path / f'scan-{index}.pdf' for index in range(4)]
    for path in paths:
        make_pdf(path, scanned=True)
    scheduler = ResourceScheduler(cpu_workers=2, cpu_threads=1)
    try:
        detector, ocr = TwoColumnDetector(model_path=tmp_path), OCR(model_path=tmp_path)
        detector_queue = ModelQueue(detector, scheduler=scheduler)
        ocr_queue = ModelQueue(ocr, scheduler=scheduler)
        pipelines = []
        for _ in paths:
            detector_proxy, ocr_proxy = copy(detector), copy(ocr)
            detector_proxy._inference_queue = detector_queue
            ocr_proxy._inference_queue = ocr_queue
            options = detector_proxy.get_options().model_copy(update={'text_ocr': ocr_proxy, 'text_ocr_key': str(id(ocr_proxy))})
            extractor = DoclingPdfExtractor(layout=options, options=PdfPipelineOptions(do_ocr=False, do_table_structure=False))
            extractor._scheduler = scheduler
            pipelines.append(PdfParsePipeline(PdfParseConfiguration(layout=detector_proxy, text_ocr=ocr_proxy,
                cache_identity={'fixture': 'shared-native'}), extractor=extractor))
        async def run(bridge):
            return await asyncio.gather(*(pipeline.run_async(path, scheduler=scheduler, bridge_executor=bridge)
                for path, pipeline in zip(paths, pipelines)))
        with ThreadPoolExecutor(4) as bridge:
            results = asyncio.run(run(bridge))
        assert len(count) == 1  # Includes construction of the heavy native stage models.
        adapters = [next(iter(pipeline.extractor._converter.initialized_pipelines.values())) for pipeline in pipelines]
        assert len({id(adapter.pipeline) for adapter in adapters}) == 1
        assert all(not adapter.contexts.active for adapter in adapters)
        assert [result.document.pdfpath for result in results] == paths
        assert all(result.metrics.inferences for result in results)
        assert all(any('Recognized scanned text' in chunk.text for chunk in result.chunks) for result in results)
    finally:
        scheduler.close()


def test_ordinary_wrapping_overlaps_figure_generation(tmp_path, engine):
    from threading import Event
    from pdfparse.enrich.model import HandlerEnrichModel
    from pdfparse.enrich.routing import wrap_document_async
    from docling_core.types.doc import ContentLayer, DocItemLabel
    path = tmp_path / 'overlap.pdf'
    path.write_bytes(b'fixture PDF')
    scheduler = ResourceScheduler(cpu_workers=2, cpu_threads=1)
    ordinary_started, generation_started = Event(), Event()
    try:
        config = compose(tmp_path, engine, scheduler)
        vlm = config.picture_enrich.fallback.vlm
        vlm._inference_queue.device = 'cuda:0'
        original_generate = vlm.runtime.generate
        def generate(**kwargs):
            generation_started.set()
            assert ordinary_started.wait(5), 'Ordinary wrapping waited for all figure inference'
            return original_generate(**kwargs)
        vlm.runtime.generate = generate
        default = HandlerEnrichModel()
        def ordinary(item, *, ctx):
            ordinary_started.set()
            assert generation_started.wait(5), 'Figure inference waited for ordinary wrapping'
            return default(item, ctx=ctx)
        source = Figures(2).extract(path)
        source.add_text(label=DocItemLabel.TEXT, text='Text with its original source order.')
        result = asyncio.run(wrap_document_async(path, source, scheduler=scheduler,
            enrich=ordinary, picture_enrich=config.picture_enrich))
        assert len([item for item in result.elements.values() if item.source_label == 'picture']) == 2
        expected = [item.self_ref for root in (source.body, source.furniture)
                    for item, _ in source.iterate_items(root=root, with_groups=True, traverse_pictures=True,
                                                       included_content_layers=set(ContentLayer))]
        assert [element.element_ref.cref for element in result.elements.values()] == expected
        result.validate()
    finally:
        ordinary_started.set()
        generation_started.set()
        scheduler.close()


def test_async_wrapping_matches_synchronous_document(tmp_path):
    from pdfparse.enrich.routing import wrap_document, wrap_document_async
    from docling_core.types.doc import ContentLayer, DocItemLabel
    from pdfparse.enrich.assets import MemoryAssetStore
    path = tmp_path / 'equivalent.pdf'
    path.write_bytes(b'fixture PDF')
    source = Figures(3).extract(path)
    source.add_text(label=DocItemLabel.TEXT, text='Preserve source text and source ordering.')
    scheduler = ResourceScheduler(cpu_workers=2, cpu_threads=1)
    try:
        synchronous = wrap_document(path, source, asset_store=MemoryAssetStore())
        asynchronous = asyncio.run(wrap_document_async(path, source, scheduler=scheduler,
                                                       asset_store=MemoryAssetStore()))
        assert asynchronous == synchronous
    finally:
        scheduler.close()
