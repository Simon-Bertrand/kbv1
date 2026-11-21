"""Docling boundary: standard PDF conversion, without document-wide OCR."""
from pathlib import Path
from contextvars import copy_context
from threading import RLock
import json

from docling.datamodel.accelerator_options import AcceleratorDevice, AcceleratorOptions
from docling.datamodel.base_models import ConversionStatus, InputFormat
from docling.datamodel.pipeline_options import (
    BaseLayoutOptions, LayoutObjectDetectionOptions, PdfPipelineOptions, TableStructureOptions,
)
from docling.document_converter import DocumentConverter, PdfFormatOption
from docling.pipeline.standard_pdf_pipeline import StandardPdfPipeline
from docling_core.types.doc import DoclingDocument

from pdfparse.models.layout.options import DoclingLayoutOptions, NativeOcrLayoutOptions, SpatialTableOptions
from pdfparse.models.ocr import BaseSpatialOcrModel, BaseTableOcrModel
from pdfparse.models.paths import model_directory, models_root, require_model_files
from .contracts import BasePdfExtractor, ExtractionRegion, ExtractionResult


class _ScheduledStage:
    """Docling stage adapter: compute enters the common CPU queue."""
    def __init__(self, model, scheduler, *, sequence=True, contexts=None):
        self.model, self.scheduler, self.sequence = model, scheduler, sequence
        self.contexts = contexts

    def __getattr__(self, name):
        return getattr(self.model, name)

    def __call__(self, *args, **kwargs):
        def run():
            result = self.model(*args, **kwargs)
            return list(result) if self.sequence else result
        if self.contexts is not None:
            context = self.contexts.for_input(args[0].input)
            # Page stages keep their state on Page/ConversionResult. Native
            # runtimes have their own exclusion keys; don't serialize a whole
            # layout stage while it awaits another model.
            key = None if self.sequence else ('docling-stage', id(self.model))
            future = context.run(self.scheduler.submit, run, key=key)
            return self.scheduler.wait(future)
        return self.scheduler.call(run, key=('docling-stage', id(self.model)))


class _DocumentContexts:
    """Route observations by InputDocument identity, never by shared model state."""
    def __init__(self):
        self.lock = RLock()
        self.active = {}

    def for_input(self, document):
        with self.lock:
            context = self.active.get(id(document))
            return context.copy() if context is not None else copy_context()


class _DocumentPipeline:
    """One shared native pipeline, isolated run contexts supplied by Docling."""
    def __init__(self, pipeline, contexts, options):
        self.pipeline, self.contexts = pipeline, contexts
        self.pipeline_options = options

    def __getattr__(self, name):
        return getattr(self.pipeline, name)

    def execute(self, in_doc, raises_on_error, **kwargs):
        with self.contexts.lock:
            self.contexts.active[id(in_doc)] = copy_context()
        try:
            return self.pipeline.execute(in_doc, raises_on_error=raises_on_error, **kwargs)
        finally:
            with self.contexts.lock:
                self.contexts.active.pop(id(in_doc), None)


def _schedule_native_call(model, name, scheduler, fallback='cpu'):
    """Route native runtimes by their resolved device, including CPU fallbacks."""
    from pdfparse.models.scheduling import canonical_device
    original = getattr(model, name)
    device = canonical_device(getattr(model, '_device', getattr(model, 'device', fallback)))
    session = getattr(model, '_session', None)
    if session is not None and 'CUDAExecutionProvider' not in session.get_providers():
        device = 'cpu'
    def call(*args, **kwargs):
        return scheduler.call(lambda: original(*args, **kwargs), device=device, key=('native-model', id(model)))
    setattr(model, name, call)


def tableformer_files(options: PdfPipelineOptions) -> tuple[Path, tuple[str, ...]]:
    if not isinstance(options.table_structure_options, TableStructureOptions):
        raise ValueError('Only the locally provisioned Docling TableFormer is supported.')
    mode = options.table_structure_options.mode.value
    prefix = f'model_artifacts/tableformer/{mode}'
    return model_directory('table_structure', 'tableformer'), (
        f'{prefix}/tm_config.json', f'{prefix}/tableformer_{mode}.safetensors',
    )


class _LocalPdfPipeline(StandardPdfPipeline):
    def __init__(self, pipeline_options: PdfPipelineOptions) -> None:
        options = pipeline_options.model_copy()
        if type(options.layout_options) is LayoutObjectDetectionOptions:
            options.layout_options = NativeOcrLayoutOptions(**{
                name: getattr(options.layout_options, name)
                for name in LayoutObjectDetectionOptions.model_fields
            })
        if isinstance(options.layout_options, (DoclingLayoutOptions, NativeOcrLayoutOptions)):
            options.allow_external_plugins = True
        else:
            raise ValueError('Layout requires a locally provisioned Docling or PdfParse model.')
        options.enable_remote_services = False
        if options.do_table_structure:
            directory, files = tableformer_files(options)
            require_model_files(directory, files)
            options.artifacts_path = directory
        elif isinstance(options.layout_options, NativeOcrLayoutOptions):
            # Prevent Docling from taking its global artifacts setting.
            options.artifacts_path = models_root()
        super().__init__(options)


class DoclingPdfExtractor(BasePdfExtractor):
    """Reuse one converter; layout options may inject any concrete detector class.

    Supplying options never authorizes OCR or Docling's picture enrichment.
    Global OCR stays disabled. Explicit OCR may fill layout text crops before
    assembly; figure OCR/VLM remains in downstream injected handlers.
    Construction does not run inference; model resources are resolved by Docling
    on conversion. Provision local weights for offline operation.
    """

    def __init__(self, *, layout: BaseLayoutOptions | None = None,
                 options: PdfPipelineOptions | None = None, page_images: bool = False) -> None:
        configured = options.model_copy(deep=True) if options is not None else PdfPipelineOptions()
        if options is None:
            configured.accelerator_options = AcceleratorOptions(device=AcceleratorDevice.CPU)
        if configured.do_ocr:
            # Docling defaults to OCR: only reject an explicit incompatible config.
            if options is not None:
                raise ValueError("PDF extraction requires do_ocr=False")
        configured.do_ocr = False
        if any((configured.do_picture_classification, configured.do_picture_description,
                configured.do_chart_extraction, configured.do_code_enrichment,
                configured.do_formula_enrichment)):
            raise ValueError("Configure picture enrichment through injected handlers")
        if layout is not None:
            configured.layout_options = layout.model_copy(deep=True)
        if isinstance(configured.layout_options, (DoclingLayoutOptions, NativeOcrLayoutOptions)):
            configured.allow_external_plugins = True
        configured.generate_picture_images = True
        configured.generate_page_images = configured.generate_page_images or page_images
        ocr = getattr(configured.layout_options, 'text_ocr', None)
        if isinstance(ocr, (BaseSpatialOcrModel, BaseTableOcrModel)) and type(configured.table_structure_options) is TableStructureOptions:
            configured.table_structure_options = SpatialTableOptions(
                **configured.table_structure_options.model_dump(),
                table_ocr=ocr if isinstance(ocr, BaseTableOcrModel) else None,
            )
        self._ocr_engine = f'{type(ocr).__module__}.{type(ocr).__qualname__}' if ocr else None
        self._converter = DocumentConverter(
            allowed_formats=[InputFormat.PDF],
            format_options={InputFormat.PDF: PdfFormatOption(
                pipeline_cls=_LocalPdfPipeline, pipeline_options=configured,
            )},
        )

    def extract(self, pdfpath: Path | str) -> DoclingDocument:
        return self.extract_with_evidence(pdfpath).document

    def extract_with_evidence(self, pdfpath: Path | str) -> ExtractionResult:
        path = Path(pdfpath).resolve(strict=True)
        from pdfparse.progress import inference_workers
        options = self._converter.format_to_options[InputFormat.PDF].pipeline_options
        if getattr(self, '_scheduler', None) is not None:
            self._schedule_stages(options)
        layout = options.layout_options
        with inference_workers((getattr(layout,'detector_instance',None), getattr(layout,'text_ocr',None),
                                getattr(options.table_structure_options,'table_ocr',None))):
            result = self._converter.convert(path)
        if result.status != ConversionStatus.SUCCESS or result.errors:
            raise RuntimeError(f"Incomplete Docling conversion for {path}: {result.status}; {result.errors}")
        regions = []
        items_by_page = {}
        for item, _ in result.document.iterate_items():
            for prov in item.prov:
                page = result.document.pages[prov.page_no]
                box = prov.bbox.to_top_left_origin(page.size.height)
                items_by_page.setdefault(prov.page_no, []).append((item.self_ref, box))
        available = False
        for page in result.pages:
            if page.predictions.layout is None or page.size is None:
                continue
            available = True
            pending = list(page.predictions.layout.clusters)
            for cluster in pending:
                pending.extend(cluster.children)
                box = cluster.bbox.to_top_left_origin(page.size.height)
                refs = tuple(dict.fromkeys(ref for ref, candidate in items_by_page.get(page.page_no, ())
                             if candidate.intersection_over_self(box) >= 0.8))
                cells = [cell for cell in cluster.cells if cell.text.strip()]
                origins = {bool(cell.from_ocr) for cell in cells}
                origin = 'mixed' if len(origins) == 2 else ('ocr' if True in origins else 'native' if origins else 'unknown')
                regions.append(ExtractionRegion(page.page_no, (box.l, box.t, box.r, box.b),
                                               str(cluster.label.value), refs, origin, cluster.confidence,
                                               self._ocr_engine if origin in ('ocr', 'mixed') else None))
        return ExtractionResult(result.document, tuple(regions), available)


    def _schedule_stages(self, options):
        if getattr(self, '_scheduled', False):
            return
        scheduler = self._scheduler
        from docling.utils.accelerator_utils import decide_device
        native_device = decide_device(options.accelerator_options.device)
        from .preparation import describe
        from docling.document_converter import create_pipeline_options_hash
        identity = json.dumps({'options': describe(options), 'models_root': str(models_root())}, sort_keys=True)
        def initialize():
            pipeline = self._converter._get_pipeline(InputFormat.PDF)
            # Native Docling constructors may reset Torch's process thread budget.
            scheduler.configure_threads()
            contexts = _DocumentContexts()
            layout = pipeline.layout_model
            native = getattr(layout, 'native', layout)
            engine = getattr(native, 'engine', None)
            if engine is not None:
                _schedule_native_call(engine, 'predict_batch', scheduler, native_device)
            predictor = getattr(pipeline.table_model, 'tf_predictor', None)
            if predictor is not None:
                _schedule_native_call(predictor, 'multi_table_predict', scheduler, native_device)
            for name in ('preprocessing_model', 'ocr_model', 'layout_model', 'layout_postprocessing_model',
                         'table_model', 'assemble_model', 'reading_order_model', 'heading_hierarchy_model'):
                model = getattr(pipeline, name)
                setattr(pipeline, name, _ScheduledStage(model, scheduler, contexts=contexts,
                    sequence=name not in ('reading_order_model', 'heading_hierarchy_model')))
            return pipeline, contexts
        pipeline, contexts = scheduler.shared_resource(('docling-native', identity), initialize, device=native_device)
        # Each converter keeps its own option hash; instance keys are absent
        # from the semantic sharing key above, and remain local to Docling.
        key = (_LocalPdfPipeline, create_pipeline_options_hash(options))
        self._converter.initialized_pipelines[key] = _DocumentPipeline(pipeline, contexts, options)
        self._scheduled = True
