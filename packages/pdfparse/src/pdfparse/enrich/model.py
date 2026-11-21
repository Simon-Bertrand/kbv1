"""Small handler-based strategies; traversal is a separate pure transformation."""
from abc import ABC, abstractmethod
from collections.abc import Mapping

from docling_core.types.doc import NodeItem, PictureItem

from .abstraction import EnrichedElement, EnrichedPictureItem, WrappingContext
from .handlers import handle_picture
from .registry import HandlerRegistry, ItemHandler, create_default_handler_registry


class BaseEnrichModel(ABC):
    """Transform one non-picture node; retain identity and graph relationships."""

    @abstractmethod
    def __call__(self, item: NodeItem, *, ctx: WrappingContext) -> EnrichedElement: ...


class BasePictureEnrichModel(ABC):
    """Transform one picture with caller-owned classification/enrichment services."""

    @abstractmethod
    def __call__(self, item: PictureItem, *, ctx: WrappingContext) -> EnrichedPictureItem: ...


class HandlerEnrichModel(BaseEnrichModel):
    """Override native handlers with a mapping, or snapshot a complete registry."""

    def __init__(self, handlers: Mapping[type[NodeItem], ItemHandler] | HandlerRegistry | None = None) -> None:
        if isinstance(handlers, HandlerRegistry):
            registry = handlers.copy()
        else:
            registry = create_default_handler_registry()
            for item_type, handler in (handlers or {}).items():
                registry.register(item_type, handler, replace=True)
        self.handlers = registry.freeze()

    def __call__(self, item: NodeItem, *, ctx: WrappingContext) -> EnrichedElement:
        if isinstance(item, PictureItem):
            raise TypeError("Configure PictureItem through picture_enrich")
        return self.handlers(item, ctx=ctx)


class WrappingPictureEnrichModel(BasePictureEnrichModel):
    """Preserve native picture data without implicit classification or inference."""

    def __call__(self, item: PictureItem, *, ctx: WrappingContext) -> EnrichedPictureItem:
        return handle_picture(item, ctx=ctx)
