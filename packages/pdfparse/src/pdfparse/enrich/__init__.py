"""Public API for PDF -> enriched document wrappers."""
from .abstraction import (
    ElementLocation,
    EnrichedCodeItem,
    EnrichedDocument,
    EnrichedElement,
    EnrichedElementType,
    EnrichedFieldHeadingItem,
    EnrichedFieldItem,
    EnrichedFieldRegionItem,
    EnrichedFieldValueItem,
    EnrichedFormItem,
    EnrichedFormulaItem,
    EnrichedGroupItem,
    EnrichedInlineGroupItem,
    EnrichedKeyValueItem,
    EnrichedListGroupItem,
    EnrichedListItem,
    EnrichedPictureItem,
    EnrichedPageDescriptionItem,
    EnrichedRef,
    EnrichedSectionHeaderItem,
    EnrichedTableItem,
    EnrichedTextItem,
    EnrichedTitleItem,
    WrappingContext,
)
from .routing import wrap_document
from .registry import HandlerRegistry, ItemHandler, UnsupportedDoclingItem, create_default_handler_registry
from .assets import AssetStore, FileSystemAssetStore, MemoryAssetStore
from .serialization import (
    DataclassWrapperCodec,
    EnrichedDocumentCodec,
    WrapperCodec,
    WrapperRegistry,
    create_default_wrapper_registry,
)
from .model import (
    BaseEnrichModel, BasePictureEnrichModel, HandlerEnrichModel, WrappingPictureEnrichModel,
)
from . import handlers

__all__ = [
    "ElementLocation",
    "EnrichedCodeItem",
    "EnrichedDocument",
    "EnrichedElement",
    "EnrichedElementType",
    "EnrichedFieldHeadingItem",
    "EnrichedFieldItem",
    "EnrichedFieldRegionItem",
    "EnrichedFieldValueItem",
    "EnrichedFormItem",
    "EnrichedFormulaItem",
    "EnrichedGroupItem",
    "EnrichedInlineGroupItem",
    "EnrichedKeyValueItem",
    "EnrichedListGroupItem",
    "EnrichedListItem",
    "EnrichedPictureItem",
    "EnrichedRef",
    "EnrichedSectionHeaderItem",
    "EnrichedTableItem",
    "EnrichedTextItem",
    "EnrichedTitleItem",
    "UnsupportedDoclingItem",
    "WrappingContext",
    "handlers",
    "wrap_document",
    "AssetStore",
    "FileSystemAssetStore",
    "MemoryAssetStore",
    "HandlerRegistry",
    "ItemHandler",
    "create_default_handler_registry",
    "WrapperCodec",
    "DataclassWrapperCodec",
    "WrapperRegistry",
    "EnrichedDocumentCodec",
    "create_default_wrapper_registry",
]

from .pictures import (BasePictureEnricher, OcrVlmPictureEnricher, TablePictureEnricher, PictureEnrichModel,
                       PictureEnrichment, UnhandledPicturePolicy)
from .pages import BasePageEnrichModel, VlmPageEnrichModel

EnrichedDoclingDocument = EnrichedDocument

__all__ += ["BasePictureEnricher", "OcrVlmPictureEnricher", "TablePictureEnricher", "PictureEnrichment"]
__all__ += ["BasePageEnrichModel", "VlmPageEnrichModel", "EnrichedPageDescriptionItem"]
__all__ += ["BaseEnrichModel", "BasePictureEnrichModel", "HandlerEnrichModel",
            "WrappingPictureEnrichModel", "PictureEnrichModel", "UnhandledPicturePolicy",
            "EnrichedDoclingDocument"]
