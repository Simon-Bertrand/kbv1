"""Enriched-document data models, references and graph validation.

Wrappers contain no live DoclingDocument or image data. WrappingContext is a
short-lived DTO passed only while normalizing Docling nodes.
"""
from __future__ import annotations

from dataclasses import dataclass, field, fields, is_dataclass
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Any

from docling_core.types.doc import DoclingDocument, RefItem

if TYPE_CHECKING:
    from .assets import AssetStore
    from .serialization import EnrichedDocumentCodec


class EnrichedElementType(str, Enum):
    TEXT = "text"
    PAGE_DESCRIPTION = "page_description"
    TITLE = "title"
    SECTION_HEADER = "section_header"
    LIST_ITEM = "list_item"
    CODE = "code"
    FORMULA = "formula"
    TABLE = "table"
    PICTURE = "picture"
    KEY_VALUE = "key_value"
    FORM = "form"
    GROUP = "group"
    LIST_GROUP = "list_group"
    INLINE_GROUP = "inline_group"
    FIELD_HEADING = "field_heading"
    FIELD_VALUE = "field_value"
    FIELD_REGION = "field_region"
    FIELD_ITEM = "field_item"


@dataclass(frozen=True, slots=True)
class EnrichedRef:
    """Document-scoped equivalent of Docling's RefItem."""

    document_id: str
    cref: str

    def __post_init__(self) -> None:
        if not self.document_id or not self.cref.startswith("#/"):
            raise ValueError("Invalid EnrichedRef document_id or cref")

    def resolve(self, document: EnrichedDocument) -> EnrichedElement:
        if self.document_id != document.document_id:
            raise ValueError("Reference belongs to a different document")
        try:
            return document.elements[self.cref]
        except KeyError as exc:
            raise KeyError(f"Unresolved enriched reference: {self.cref}") from exc


@dataclass(frozen=True, slots=True)
class ElementLocation:
    page_no: int
    bbox: tuple[float, float, float, float]
    coord_origin: str
    charspan: tuple[int, int] | None = None


@dataclass(kw_only=True, slots=True)
class EnrichedElement:
    pdfpath: Path
    document_id: str
    element_type: EnrichedElementType
    element_ref: EnrichedRef
    source_class: str
    source_label: str | None
    content_layer: str
    parent_ref: EnrichedRef | None
    children_refs: tuple[EnrichedRef, ...] = ()
    locations: tuple[ElementLocation, ...] = ()

    @property
    def page_no(self) -> int | None:
        return self.locations[0].page_no if self.locations else None

    @property
    def bbox(self) -> tuple[float, float, float, float] | None:
        return self.locations[0].bbox if self.locations else None


@dataclass(kw_only=True, slots=True)
class EnrichedTextItem(EnrichedElement):
    original_text: str
    content_for_embedding: str | None
    content_for_llm: str
    formatting: dict[str, Any] | None = None
    hyperlink: str | None = None


@dataclass(kw_only=True, slots=True)
class EnrichedTitleItem(EnrichedTextItem):
    pass


@dataclass(kw_only=True, slots=True)
class EnrichedPageDescriptionItem(EnrichedTextItem):
    """Generated page introduction, distinct from text present in the PDF."""
    description: str
    content_refs: tuple[EnrichedRef, ...]
    model_id: str
    transformation_id: str
    source_markdown_hash: str


@dataclass(kw_only=True, slots=True)
class EnrichedSectionHeaderItem(EnrichedTextItem):
    level: int


@dataclass(kw_only=True, slots=True)
class EnrichedListItem(EnrichedTextItem):
    marker: str
    enumerated: bool


@dataclass(kw_only=True, slots=True)
class EnrichedFormulaItem(EnrichedTextItem):
    pass


@dataclass(kw_only=True, slots=True)
class EnrichedCodeItem(EnrichedTextItem):
    code_language: str
    caption: str = ""
    caption_refs: tuple[EnrichedRef, ...] = ()


@dataclass(kw_only=True, slots=True)
class EnrichedFieldHeadingItem(EnrichedTextItem):
    level: int


@dataclass(kw_only=True, slots=True)
class EnrichedFieldValueItem(EnrichedTextItem):
    kind: str


@dataclass(kw_only=True, slots=True)
class EnrichedTableItem(EnrichedElement):
    caption: str
    caption_refs: tuple[EnrichedRef, ...]
    num_rows: int
    num_cols: int
    table_data: dict[str, Any]
    content_for_embedding: str | None
    content_for_llm: str


@dataclass(kw_only=True, slots=True)
class EnrichedPictureItem(EnrichedElement):
    caption: str
    caption_refs: tuple[EnrichedRef, ...]
    description: str | None
    classification: dict[str, Any] | None
    asset_path: str | None
    content_for_embedding: str | None
    content_for_llm: str


@dataclass(kw_only=True, slots=True)
class EnrichedKeyValueItem(EnrichedElement):
    graph_data: dict[str, Any]
    content_for_embedding: str | None = None
    content_for_llm: str = ""
    content_refs: tuple[EnrichedRef, ...] = ()
    content_locations: tuple[ElementLocation, ...] = ()


@dataclass(kw_only=True, slots=True)
class EnrichedFormItem(EnrichedElement):
    graph_data: dict[str, Any]
    content_for_embedding: str | None = None
    content_for_llm: str = ""
    content_refs: tuple[EnrichedRef, ...] = ()
    content_locations: tuple[ElementLocation, ...] = ()


@dataclass(kw_only=True, slots=True)
class EnrichedFieldRegionItem(EnrichedElement):
    pass


@dataclass(kw_only=True, slots=True)
class EnrichedFieldItem(EnrichedElement):
    pass


@dataclass(kw_only=True, slots=True)
class EnrichedGroupItem(EnrichedElement):
    name: str


@dataclass(kw_only=True, slots=True)
class EnrichedListGroupItem(EnrichedGroupItem):
    pass


@dataclass(kw_only=True, slots=True)
class EnrichedInlineGroupItem(EnrichedGroupItem):
    pass


def _iter_references(value: Any, active: set[int] | None = None):
    """Find typed references, including fields introduced by custom wrappers."""
    if isinstance(value, EnrichedRef):
        yield value
        return
    if is_dataclass(value) and not isinstance(value, type):
        values = (getattr(value, item.name) for item in fields(value))
    elif isinstance(value, dict):
        values = value.values()
    elif isinstance(value, (tuple, list, set, frozenset)):
        values = value
    else:
        return
    active = set() if active is None else active
    identity = id(value)
    if identity in active:
        raise ValueError("Cycle in wrapper data")
    active.add(identity)
    try:
        for item in values:
            yield from _iter_references(item, active)
    finally:
        active.remove(identity)


@dataclass(slots=True)
class EnrichedDocument:
    document_id: str
    pdfpath: Path
    assets_dir: Path | None = None
    elements: dict[str, EnrichedElement] = field(default_factory=dict)

    def add(self, element: EnrichedElement) -> None:
        if element.document_id != self.document_id or element.element_ref.document_id != self.document_id:
            raise ValueError("Element/document ID mismatch")
        if element.element_ref.cref in self.elements:
            raise ValueError(f"Duplicate element: {element.element_ref.cref}")
        self.elements[element.element_ref.cref] = element

    def get(self, ref: EnrichedRef | str) -> EnrichedElement:
        if isinstance(ref, EnrichedRef):
            return ref.resolve(self)
        return self.elements[ref]

    def parent(self, item: EnrichedElement) -> EnrichedElement | None:
        return item.parent_ref.resolve(self) if item.parent_ref else None

    def children(self, item: EnrichedElement) -> list[EnrichedElement]:
        return [ref.resolve(self) for ref in item.children_refs]

    def to_markdown(self) -> str:
        """Render source content and image descriptions; no extraction or inference."""
        from .handlers import document_markdown
        return document_markdown(self)

    def validate(self) -> None:
        """Check reference scope, parents/children symmetry, and acyclicity."""
        children = {key: set(item.children_refs) for key, item in self.elements.items()}
        for key, item in self.elements.items():
            if key != item.element_ref.cref or item.document_id != self.document_id:
                raise ValueError(f"Invalid element ID: {key}")
            if item.pdfpath != self.pdfpath:
                raise ValueError(f"Element/document PDF path mismatch: {key}")
            for reference in _iter_references(item):
                reference.resolve(self)
            if item.parent_ref is not None:
                parent = item.parent_ref.resolve(self)
                if item.element_ref not in children[parent.element_ref.cref]:
                    raise ValueError(f"Parent/child mismatch: {key}")
            if len(item.children_refs) != len(children[key]):
                raise ValueError(f"Duplicate child references: {key}")
            for child_ref in item.children_refs:
                child = child_ref.resolve(self)
                if child.parent_ref != item.element_ref:
                    raise ValueError(f"Child/parent mismatch: {key} -> {child_ref.cref}")

        colors: dict[str, int] = {}
        for start in self.elements:
            if colors.get(start, 0):
                continue
            stack: list[tuple[str, bool]] = [(start, False)]
            while stack:
                ref, leaving = stack.pop()
                if leaving:
                    colors[ref] = 2
                    continue
                if colors.get(ref, 0) == 1:
                    raise ValueError(f"Cycle in document graph at {ref}")
                if colors.get(ref, 0) == 2:
                    continue
                colors[ref] = 1
                stack.append((ref, True))
                stack.extend((r.cref, False) for r in reversed(self.elements[ref].children_refs))

    def to_dict(self, *, codec: EnrichedDocumentCodec | None = None) -> dict[str, Any]:
        from .serialization import EnrichedDocumentCodec
        return (codec if codec is not None else EnrichedDocumentCodec()).to_dict(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any], *, codec: EnrichedDocumentCodec | None = None) -> EnrichedDocument:
        from .serialization import EnrichedDocumentCodec
        return (codec if codec is not None else EnrichedDocumentCodec()).from_dict(payload)

    def save_json(self, filename: Path | str, *, codec: EnrichedDocumentCodec | None = None) -> None:
        from .serialization import EnrichedDocumentCodec
        (codec if codec is not None else EnrichedDocumentCodec()).save(self, filename)

    @classmethod
    def load_json(cls, filename: Path | str, *, codec: EnrichedDocumentCodec | None = None) -> EnrichedDocument:
        from .serialization import EnrichedDocumentCodec
        return (codec if codec is not None else EnrichedDocumentCodec()).load(filename)


@dataclass(frozen=True, slots=True)
class WrappingContext:
    pdfpath: Path
    document_id: str
    doc: DoclingDocument
    asset_store: AssetStore | None = None

    def ref(self, value: RefItem | str | None) -> EnrichedRef | None:
        if value is None:
            return None
        cref = value if isinstance(value, str) else value.cref
        return EnrichedRef(document_id=self.document_id, cref=cref)
