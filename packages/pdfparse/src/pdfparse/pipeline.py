"""One orchestrator for the three independent document-processing stages."""
from pathlib import Path
from time import perf_counter

from docling_core.types.doc import DoclingDocument

from .configuration import PdfParseConfiguration
from .enrich import EnrichedDocument, EnrichedDocumentCodec, wrap_document
from .ingestion.cache import result_cache

from .ingestion.contracts import BasePdfExtractor, Chunk, PdfParseResult, StageMetrics, extract_source
from .progress import Stage, report_progress, observe_inference

_CONFIGURED_ASSETS = object()


class PdfParsePipeline:
    """Compose once, reuse models, and propagate failures without fallback."""

    def __init__(self, configuration: PdfParseConfiguration, *,
                 extractor: BasePdfExtractor | None = None) -> None:
        if not isinstance(configuration, PdfParseConfiguration):
            raise TypeError("configuration must be PdfParseConfiguration")
        if extractor is not None and not isinstance(extractor, BasePdfExtractor):
            raise TypeError("extractor must implement BasePdfExtractor")
        self.configuration = configuration
        self.extractor = extractor if extractor is not None else configuration.create_extractor()
        self.codec = EnrichedDocumentCodec(configuration.wrappers)

    def enrich_document(self, pdfpath: Path | str, source: DoclingDocument, *,
                        asset_store=_CONFIGURED_ASSETS, source_path=None) -> EnrichedDocument:
        report_progress(Stage.ENRICHMENT)
        config = self.configuration
        return wrap_document(pdfpath, source, enrich=config.enrich,
                             picture_enrich=config.picture_enrich, page_enrich=config.page_enrich,
                             asset_store=config.asset_store if asset_store is _CONFIGURED_ASSETS else asset_store,
                             wrappers=self.codec.wrappers, source_path=source_path)

    def chunk_document(self, document: EnrichedDocument) -> tuple[Chunk, ...]:
        """Chunk a valid enriched document and check the strategy's provenance."""
        report_progress(Stage.CHUNKING)
        chunks = tuple(self.configuration.chunking.chunk(document))
        self._validate_chunks(document, chunks)
        return chunks

    @staticmethod
    def _validate_chunks(document: EnrichedDocument, chunks: tuple[Chunk, ...]) -> None:
        seen: set[str] = set()
        source_locations = {}
        prepared = None
        for chunk in chunks:
            if not isinstance(chunk, Chunk):
                raise TypeError("Chunker must return Chunk instances")
            if not isinstance(chunk.chunk_id, str) or not chunk.chunk_id.strip() or chunk.chunk_id in seen:
                raise ValueError("Chunk IDs must be nonempty and unique")
            seen.add(chunk.chunk_id)
            if chunk.document_id != document.document_id or not chunk.source_refs:
                raise ValueError("Chunk must retain document identity and source references")
            if not isinstance(chunk.text, str) or not chunk.text.strip():
                raise ValueError("Chunk must contain nonempty text")
            if not isinstance(chunk.context, str):
                raise TypeError("Chunk context must be text")
            if not isinstance(chunk.transformation_id, str) or not chunk.transformation_id.strip():
                raise ValueError("Chunk must identify its transformation")
            if len(set(chunk.source_refs)) != len(chunk.source_refs):
                raise ValueError("Chunk source references must be unique")
            if not set((*chunk.body_refs, *chunk.section_refs)).issubset(chunk.source_refs):
                raise ValueError('Chunk body/section references must be included in its sources')
            if chunk.start_index is not None or chunk.end_index is not None:
                if (type(chunk.start_index) is not int or type(chunk.end_index) is not int
                        or not 0 <= chunk.start_index < chunk.end_index
                        or chunk.end_index - chunk.start_index != len(chunk.text)):
                    raise ValueError('Chunk offsets must match its text length')
            locations = set()
            for ref in chunk.source_refs:
                if ref not in source_locations:
                    source = ref.resolve(document)
                    source_locations[ref] = frozenset((*source.locations, *getattr(source, 'content_locations', ())))
                locations.update(source_locations[ref])
            chunk_locations = set(chunk.locations)
            if not locations.issubset(chunk_locations):
                if chunk.start_index is None:
                    raise ValueError("Chunk lost source geometry")
                if prepared is None:
                    from .ingestion.text import prepare_text
                    prepared = prepare_text(document)
                refs = set(chunk.body_refs)
                precise = {block.element.element_ref: block.body_locations(chunk.start_index, chunk.end_index)
                           for block in prepared.blocks_in_span(chunk.start_index, chunk.end_index)
                           if block.element.element_ref in refs}
                required = {loc for ref in chunk.source_refs for loc in precise.get(ref, source_locations[ref])}
                if not required.issubset(chunk_locations):
                    raise ValueError("Chunk lost source geometry")
            if not chunk_locations.issubset(locations):
                raise ValueError("Chunk contains geometry unrelated to its source references")

    def run(self, pdfpath: Path | str, *, source_path: Path | None = None) -> PdfParseResult:
        calls = []
        with observe_inference(calls.append):
            with result_cache(self, pdfpath, source_path=source_path) as cache:
                if cache and (cached := cache.load()) is not None:
                    report_progress(Stage.CONFIGURATION, 'Résultat chargé depuis le cache PDF')
                    return cached
                if cache and (reused := cache.reuse_enriched()) is not None:
                    return reused
                report_progress(Stage.EXTRACTION, 'OCR des blocs textuels activé' if self.configuration.text_ocr else None)
                started = perf_counter()
                extraction = cache.extract() if cache else extract_source(self.extractor, pdfpath, source_path)
                extracted = perf_counter()
                source = extraction.document
                document = self.enrich_document(pdfpath, source,
                                                asset_store=cache.asset_store() if cache else _CONFIGURED_ASSETS,
                                                source_path=source_path)
                enriched = perf_counter()
                chunks = self.chunk_document(document)
                finished = perf_counter()
                result = self._result(extraction, document, chunks, (started, extracted, enriched, finished), calls)

                if cache:
                    result = cache.save(result)
                return result


    def _result(self, extraction, document, chunks, times, calls):
        from .ingestion.preparation import preparation_manifest
        source = extraction.document
        started, extracted, enriched, finished = times
        pages = tuple(sorted(source.pages)) if source.pages else None
        try:
            import resource
            import sys
            peak_rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
            peak_rss = int(peak_rss * (1 if sys.platform == 'darwin' else 1024))
        except ImportError:
            peak_rss = None
        metrics = StageMetrics(extracted - started, enriched - extracted, finished - enriched,
                               len(pages) / (finished - started) if pages else None, peak_rss, calls)
        result = PdfParseResult(document, chunks, pages, extraction.regions, extraction.evidence_available,
                              preparation_manifest(self, document.document_id), metrics)
        return result

    async def run_async(self, pdfpath, *, scheduler, bridge_executor, item_concurrency=8, source_path=None):
        """Run document dependencies without blocking the shared CPU workers.

        Docling's synchronous conversion has a bounded bridge thread. Its model
        stages use the same scheduler; enrichment resumes from inference futures.
        """
        from contextvars import copy_context
        from .models.scheduling import await_future
        from .ingestion.cache import async_result_cache
        from .enrich.routing import wrap_document_async

        calls = []
        with observe_inference(calls.append):
            async with async_result_cache(self, pdfpath, scheduler, source_path=source_path) as cache:
                if cache:
                    cached = await scheduler.run_cpu(cache.load)
                    if cached is not None:
                        report_progress(Stage.CONFIGURATION, 'Résultat chargé depuis le cache PDF')
                        return cached
                if cache:
                    reused = await scheduler.run_cpu(cache.reuse_enriched)
                    if reused is not None:
                        return reused
                report_progress(Stage.EXTRACTION, 'OCR des blocs textuels activé' if self.configuration.text_ocr else None)
                started = perf_counter()
                read_pdf = cache.extract if cache else lambda: extract_source(self.extractor, pdfpath, source_path)
                extraction = await await_future(bridge_executor.submit(copy_context().run, read_pdf))
                extracted = perf_counter()
                store = await scheduler.run_cpu(cache.asset_store) if cache else self.configuration.asset_store
                report_progress(Stage.ENRICHMENT)
                config = self.configuration
                document = await wrap_document_async(pdfpath, extraction.document, scheduler=scheduler,
                    item_concurrency=item_concurrency, enrich=config.enrich, picture_enrich=config.picture_enrich,
                    page_enrich=config.page_enrich, asset_store=store, wrappers=self.codec.wrappers,
                    source_path=source_path)
                enriched = perf_counter()
                chunks = await scheduler.run_cpu(self.chunk_document, document)
                finished = perf_counter()
                result = await scheduler.run_cpu(self._result, extraction, document, chunks,
                    (started, extracted, enriched, finished), calls)
                return await scheduler.run_cpu(cache.save, result) if cache else result
