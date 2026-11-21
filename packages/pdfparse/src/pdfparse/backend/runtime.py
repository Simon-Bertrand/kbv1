"""Studio composition of explicitly selected services; no document orchestration."""
import gc
import hashlib
import json
import sys

# Import Docling before worker frames hold models: optional-backend import
# exceptions retained by Docling otherwise keep those frames and their VRAM alive.
from pdfparse import PdfParseConfiguration
from pdfparse.enrich import AssetStore, FileSystemAssetStore
from pathlib import Path
from pdfparse.progress import Stage, report_progress

from .schemas import DeviceOption, ModelOption, RuntimeConfiguration

DEFAULT_ASSETS_DIR = Path(__file__).resolve().parents[3] / '.assets'


def available_devices() -> list[DeviceOption]:
    devices = [DeviceOption(id='cpu', label='CPU')]
    try:
        import torch
    except ImportError:
        return devices
    if torch.cuda.is_available():
        devices.extend(DeviceOption(id=f'cuda:{index}', label=f'CUDA {index} · {torch.cuda.get_device_name(index)}')
                       for index in range(torch.cuda.device_count()))
    if torch.backends.mps.is_available():
        devices.append(DeviceOption(id='mps', label='Apple MPS'))
    if hasattr(torch, 'xpu') and torch.xpu.is_available():
        devices.extend(DeviceOption(id=f'xpu:{index}', label=f'XPU {index}')
                       for index in range(torch.xpu.device_count()))
    return devices


def model_options(models) -> list[ModelOption]:
    return [ModelOption(category=spec.category, name=spec.name,
                        prepared=models.is_prepared(spec.category, spec.name),
                        defaults=spec.implementation.validate_settings(None),
                        settings_schema=spec.implementation.settings_model.model_json_schema()
                        if spec.implementation.settings_model else {})
            for spec in models.list()]


def validate_selection(models, selection: RuntimeConfiguration) -> None:
    devices = {device.id for device in available_devices()}
    for category in ('layout', 'picture_classifier', 'ocr', 'vlm'):
        if category not in selection.required_services():
            continue
        choice = getattr(selection, category)
        if choice is None:
            continue
        spec = models.get(category, choice.name)
        spec.implementation.validate_settings(choice.settings)
        if choice.device not in devices:
            raise ValueError(f'Device indisponible : {choice.device}')
        if choice.settings.get('quantization') == 'nf4' and not choice.device.startswith('cuda:'):
            raise ValueError('La quantification NF4 nécessite CUDA.')
        if not models.is_prepared(category, choice.name):
            raise ValueError(f'Poids non préparés : {category}/{choice.name}. Téléchargez-les explicitement avant le chargement.')


def release_memory() -> None:
    gc.collect()
    torch = sys.modules.get('torch')
    if torch is not None:
        if torch.cuda.is_initialized():
            torch.cuda.empty_cache()
        if torch.backends.mps.is_available():
            torch.mps.empty_cache()
        if hasattr(torch, 'xpu') and torch.xpu.is_initialized():
            torch.xpu.empty_cache()


def build_chunking(options):
    if options.kind == 'structural':
        from pdfparse.cli import e5_tokenizer_directory
        from pdfparse.ingestion.e5_chunking import build_e5_chunker
        from pdfparse import StructuralChunkingModel

        snapshot = e5_tokenizer_directory()
        target_tokens = getattr(options, 'target_tokens', 384)
        min_tokens = getattr(options, 'min_tokens', 128)
        if snapshot and snapshot.is_dir():
            return build_e5_chunker(
                snapshot=snapshot,
                max_tokens=options.chunk_size,
                target_tokens=target_tokens,
                min_tokens=min_tokens,
                respect_page_boundaries=options.respect_page_boundaries,
            )
        return StructuralChunkingModel(
            chunk_size=options.chunk_size,
            target_tokens=target_tokens,
            min_tokens=min_tokens,
            respect_page_boundaries=options.respect_page_boundaries,
        )
    from chonkie import TokenChunker, SentenceChunker, RecursiveChunker, __version__
    from pdfparse.ingestion.chonkie import ChonkieChunkingModel

    parameters = options.model_dump(exclude={'kind'})
    backend = {'token': TokenChunker, 'sentence': SentenceChunker, 'recursive': RecursiveChunker}[options.kind](
        tokenizer='character', **parameters,
    )
    identity = json.dumps(options.model_dump(), sort_keys=True)
    version = hashlib.sha256(identity.encode()).hexdigest()[:16]
    return ChonkieChunkingModel(backend=backend,
        transformation_id=f'pdfparse.chonkie.context.v7:{__version__}:character:{options.kind}:{version}')


def picture_routing_identity(mode, routing, *, ocr_identity, ocr_settings,
                             vlm_identity, vlm_settings):
    identity = json.dumps({'mode': mode, 'routing': routing, 'ocr': ocr_identity,
                           'vlm': vlm_identity, 'ocr_settings': ocr_settings,
                           'vlm_settings': vlm_settings}, sort_keys=True, default=str)
    prefix = 'studio.table-routing.v2:' if mode == 'table' else 'studio.figure-routing.v10:'
    return prefix + hashlib.sha256(identity.encode()).hexdigest()[:16]


def effective_runtime_configuration(selection, chunker):
    return {**selection.model_dump(mode='json'), 'chunker_identity': chunker.transformation_id}


def build_pipeline(models, selection: RuntimeConfiguration, *, chunking=None, asset_store: AssetStore | None = None):
    from pdfparse import PdfParsePipeline
    from pdfparse.enrich import OcrVlmPictureEnricher, TablePictureEnricher, PictureEnrichModel, VlmPageEnrichModel

    configuration = {'chunking': chunking if chunking is not None else build_chunking(selection.chunking),
                     'asset_store': asset_store if asset_store is not None else FileSystemAssetStore(DEFAULT_ASSETS_DIR)}
    instances = {}
    def load(category):
        if category in instances:
            return instances[category]
        choice = getattr(selection, category)
        report_progress(Stage.LOADING, f'{category} / {choice.name} · {choice.device}')
        instances[category] = models.load(category, choice.name, device=choice.device, settings=choice.settings)
        return instances[category]
    if selection.layout:
        configuration['layout'] = load('layout')
    routing = selection.enrichment
    if selection.picture_classifier:
        load('picture_classifier')
    modes = [routing.figures, routing.fallback, *routing.class_routes.values()] if selection.picture_classifier else []
    if routing.text_ocr or any(mode in ('ocr', 'ocr_vlm') or (mode == 'table' and selection.ocr is not None) for mode in modes):
        load('ocr')
    if routing.page_vlm or any(mode in ('vlm', 'ocr_vlm') or (mode == 'table' and selection.vlm is not None) for mode in modes):
        load('vlm')
    if routing.text_ocr:
        configuration['text_ocr'] = load('ocr')
    if routing.page_vlm:
        configuration['page_enrich'] = VlmPageEnrichModel(
            vlm=load('vlm'), model_id=selection.vlm.name,
            revision=models.get('vlm', selection.vlm.name).source.revision,
        )
    if selection.picture_classifier:
        classifier = load('picture_classifier')
        unknown = set(routing.class_routes) - set(classifier.labels)
        if unknown:
            raise ValueError('Classes DFC inconnues : ' + ', '.join(sorted(unknown)))
        rules = {}
        for mode in {routing.figures, routing.fallback, *routing.class_routes.values()}:
            ocr = load('ocr') if mode in ('ocr', 'ocr_vlm') or (mode == 'table' and selection.ocr) else None
            vlm = load('vlm') if mode in ('vlm', 'ocr_vlm') or (mode == 'table' and selection.vlm) else None
            identity = picture_routing_identity(
                mode, routing.model_dump(), ocr_identity=getattr(ocr, 'model_identity', None),
                ocr_settings=getattr(ocr, 'settings', None), vlm_identity=getattr(vlm, 'model_identity', None),
                vlm_settings=getattr(vlm, 'settings', None),
            )
            if mode == 'table':
                rules[mode] = TablePictureEnricher(ocr=ocr, vlm=vlm, transformation_id=identity)
            else:
                rules[mode] = OcrVlmPictureEnricher(
                    ocr=ocr, vlm=vlm,
                    prompt='Décris en français la figure, ses éléments essentiels et leurs relations, pour la recherche textuelle.',
                    transformation_id=identity,
                )
        configuration['picture_enrich'] = PictureEnrichModel(
            classifier=classifier,
            routes={label: rules[routing.class_routes.get(label, routing.figures)] for label in classifier.labels},
            confidence_threshold=routing.confidence_threshold, fallback=rules[routing.fallback],
        )
    pipeline = PdfParsePipeline(PdfParseConfiguration(**configuration))
    scheduler = getattr(models, 'scheduler', None)
    if scheduler is not None:
        pipeline.extractor._scheduler = scheduler
    return pipeline
