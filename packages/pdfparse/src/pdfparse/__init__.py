"""PDF components; optional subsystems are loaded only when accessed."""

from importlib import import_module
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from . import enrich
    from .configuration import PdfParseConfiguration
    from .pipeline import PdfParsePipeline
    from .enrich import (BaseEnrichModel, BasePictureEnrichModel, HandlerEnrichModel,
                         WrappingPictureEnrichModel, PictureEnrichModel, EnrichedDoclingDocument,
                         UnhandledPicturePolicy, BasePictureEnricher, PictureEnrichment, OcrVlmPictureEnricher,
                         TablePictureEnricher, BasePageEnrichModel, VlmPageEnrichModel)
    from .ingestion import BaseChunkingModel, ChonkieChunkingModel, Chunk, PdfParseResult, SourceContext
    from .ingestion import StructuralChunkingModel, QualityReport, TextOrigin, IndexSnapshot, EvidenceCase, evaluate_retrieval
    from .models.layout import BaseLayoutModel, NativeLayoutModel
    from .models.picture_classifier.base import DocumentClassifierModel
    from .models import ModelRegistry

_EXPORTS = {
    "PdfParsePipeline": ".pipeline", "PdfParseConfiguration": ".configuration",
    **dict.fromkeys(("BaseEnrichModel", "BasePictureEnrichModel", "HandlerEnrichModel",
                     "WrappingPictureEnrichModel", "PictureEnrichModel", "EnrichedDoclingDocument",
                     "UnhandledPicturePolicy", "BasePictureEnricher", "PictureEnrichment",
                     "OcrVlmPictureEnricher", "TablePictureEnricher", "BasePageEnrichModel", "VlmPageEnrichModel"), ".enrich"),
    **dict.fromkeys(("BaseChunkingModel", "ChonkieChunkingModel", "StructuralChunkingModel", "QualityReport", "TextOrigin", "IndexSnapshot", "EvidenceCase", "evaluate_retrieval", "Chunk", "PdfParseResult", "SourceContext"), ".ingestion"),
    **dict.fromkeys(("BaseLayoutModel", "NativeLayoutModel"), ".models.layout"),
    "DocumentClassifierModel": ".models.picture_classifier.base",
    "ModelRegistry": ".models",
}

__all__ = ["enrich", *_EXPORTS]


def __getattr__(name: str) -> Any:
    if name == "enrich":
        module = import_module(".enrich", __name__)
        globals()[name] = module
        return module
    if name in _EXPORTS:
        value = getattr(import_module(_EXPORTS[name], __name__), name)
        globals()[name] = value
        return value
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
