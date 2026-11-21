"""Per-pipeline polymorphic routing for functional or stateful handlers."""

from functools import singledispatch
from typing import Protocol, TypeVar

from docling_core.types.doc import (
    CodeItem,
    FieldHeadingItem,
    FieldItem,
    FieldRegionItem,
    FieldValueItem,
    FormItem,
    FormulaItem,
    GroupItem,
    InlineGroup,
    KeyValueItem,
    ListGroup,
    ListItem,
    NodeItem,
    PictureItem,
    SectionHeaderItem,
    TableItem,
    TextItem,
    TitleItem,
)

from . import handlers

from .abstraction import EnrichedElement, WrappingContext


class UnsupportedDoclingItem(TypeError):
    """No handler accepts the source item type."""


T = TypeVar("T", bound=NodeItem, contravariant=True)


class ItemHandler(Protocol[T]):
    """A pure function or callable object with injected model dependencies."""

    def __call__(self, item: T, *, ctx: WrappingContext) -> EnrichedElement: ...


def _unsupported(item, *, ctx):
    raise UnsupportedDoclingItem(f"No handler registered for {type(item).__name__}")


class HandlerRegistry:
    """Configure handlers before construction, then snapshot for a pipeline.

    Dispatch follows Python type specificity, including singledispatch's ABC
    ambiguity checks. Snapshots copy registrations, not callable instances;
    callers decide whether their model-backed handlers may share runtime state.
    """

    def __init__(self) -> None:
        self._dispatch = singledispatch(_unsupported)
        self._frozen = False

    def register(self, item_type: type[T], handler: ItemHandler[T] | None = None, *, replace: bool = False):
        if self._frozen:
            raise RuntimeError("Handler registry is frozen; configure a copy before creating a pipeline")
        if not isinstance(item_type, type) or not issubclass(item_type, NodeItem):
            raise TypeError("Handlers must be registered for a Docling NodeItem type")
        if issubclass(item_type, PictureItem):
            raise TypeError("Configure PictureItem through picture_enrich")
        if handler is None:
            return lambda fn: self.register(item_type, fn, replace=replace)
        if not callable(handler):
            raise TypeError("Handler must be callable")
        if item_type in self._dispatch.registry and not replace:
            raise ValueError(f"Handler already registered for {item_type.__name__}; use replace=True")
        self._dispatch.register(item_type, handler)
        return handler

    def copy(self) -> "HandlerRegistry":
        result = HandlerRegistry()
        for item_type, handler in self._dispatch.registry.items():
            if item_type is not object:
                result.register(item_type, handler)
        return result

    def freeze(self) -> "HandlerRegistry":
        self._frozen = True
        return self

    def __call__(self, item: NodeItem, *, ctx: WrappingContext) -> EnrichedElement:
        return self._dispatch(item, ctx=ctx)


_DEFAULT_HANDLERS = {
    TextItem: handlers.handle_text,
    TitleItem: handlers.handle_title,
    SectionHeaderItem: handlers.handle_section_header,
    ListItem: handlers.handle_list_item,
    FormulaItem: handlers.handle_formula,
    CodeItem: handlers.handle_code,
    FieldHeadingItem: handlers.handle_field_heading,
    FieldValueItem: handlers.handle_field_value,
    TableItem: handlers.handle_table,
    KeyValueItem: handlers.handle_key_value,
    FormItem: handlers.handle_form,
    FieldRegionItem: handlers.handle_field_region,
    FieldItem: handlers.handle_field_item,
    GroupItem: handlers.handle_group,
    ListGroup: handlers.handle_list_group,
    InlineGroup: handlers.handle_inline_group,
}


def create_default_handler_registry() -> HandlerRegistry:
    """Return an isolated registry for non-picture nodes."""
    registry = HandlerRegistry()
    for item_type, handler in _DEFAULT_HANDLERS.items():
        registry.register(item_type, handler)
    return registry
