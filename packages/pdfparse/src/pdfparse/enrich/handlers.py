"""Stateless built-in wrapping of Docling nodes, preserving graph and provenance."""
from __future__ import annotations

import hashlib
import re
import unicodedata
from collections.abc import Iterator
from io import BytesIO
from typing import Any, TypeVar

from PIL import Image

from docling_core.types.doc import (
    CodeItem,
    DocItem,
    DocItemLabel,
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
    TableCell,
    TableData,
    TableItem,
    TextItem,
    TitleItem,
)

from .abstraction import (
    ElementLocation,
    EnrichedCodeItem,
    EnrichedDocument,
    EnrichedElement,
    EnrichedElementType,
    EnrichedPageDescriptionItem,
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
    EnrichedRef,
    EnrichedSectionHeaderItem,
    EnrichedTableItem,
    EnrichedTextItem,
    EnrichedTitleItem,
    WrappingContext,
)


def embedding_text(text: str) -> str:
    """Remove extraction artifacts without guessing words or flattening paragraphs."""
    text = text.replace('\r\n', '\n').replace('\r', '\n')
    text = re.sub(r'(?<=\w)\u00ad[ \t]*\n[ \t]*(?=\w)', '', text)
    text = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f\u00ad\u200b\u2060\ufeff]', '', text)
    return unicodedata.normalize('NFC', text)


def document_content(document: EnrichedDocument, *, embedding: bool = False
                     ) -> Iterator[tuple[EnrichedElement, str, tuple[EnrichedElement, ...]]]:
    """Select canonical owners once; equal unrelated text remains independent."""
    field = 'content_for_embedding' if embedding else 'content_for_llm'
    owned, covered = {}, set()
    for element in document.elements.values():
        if not (text := getattr(element, field, None)) or not text.strip():
            continue
        captions = getattr(element, 'caption_refs', ())
        refs = tuple(dict.fromkeys((*captions, *getattr(element, 'content_refs', ()))))
        sources = tuple(document.get(ref) for ref in refs if ref != element.element_ref)
        owned[element.element_ref] = sources
        graph_texts = {}
        for cell in getattr(element, 'graph_data', {}).get('cells', ()):
            if reference := cell.get('item_ref'):
                value = embedding_text(cell['text']) if embedding else cell['text']
                graph_texts.setdefault(reference['cref'], set()).add(value)
        for source in sources:
            source_text = getattr(source, field, None)
            if isinstance(source, EnrichedTextItem) and (
                source.element_ref in captions
                or source_text and source_text in graph_texts.get(source.element_ref.cref, ())
            ):
                covered.add(source.element_ref)
    entries = []
    for element in document.elements.values():
        text = getattr(element, field, None)
        if not text or not text.strip() or element.element_ref in covered:
            continue
        caption = getattr(element, 'caption', None)
        if not caption:
            caption = '\n'.join(getattr(source, field, '') or ''
                                for source in owned.get(element.element_ref, ())
                                if source.element_ref in getattr(element, 'caption_refs', ()))
        if caption:
            caption = embedding_text(caption) if embedding else caption
            # Only a complete sequence of lines represents an existing caption.
            # A substring match (e.g. "Figure 1" in "Figure 10") loses content.
            if f'\n{caption}\n' not in f'\n{text}\n':
                text = f'{caption}\n{text}'
        entries.append((element, text, (element, *owned.get(element.element_ref, ()))))
    introductions = {entry[0].page_no: entry for entry in entries
                     if isinstance(entry[0], EnrichedPageDescriptionItem)}
    if not introductions:
        yield from entries
        return
    pages = {page_no: [] for page_no in sorted(introductions)}
    first_page = next(iter(pages))
    unplaced = []
    for entry in entries:
        if isinstance(entry[0], EnrichedPageDescriptionItem):
            continue
        page_no = next((item.page_no for item in entry[2] if item.page_no is not None), first_page)
        if page_no in pages:
            pages[page_no].append(entry)
        else:
            unplaced.append(entry)
    for page_no, items in pages.items():
        yield introductions[page_no]
        yield from items
    yield from unplaced


def markdown_content(document: EnrichedDocument):
    """Format canonical blocks once, preserving their owners and contributors."""
    depths = {}
    for element in document.elements.values():
        depths[element.element_ref] = depths.get(element.parent_ref, 0) + isinstance(element, EnrichedListGroupItem)
    for element, text, sources in document_content(document):
        is_list = isinstance(element, EnrichedListItem)
        if isinstance(element, EnrichedTitleItem):
            text = f'# {text}'
        elif isinstance(element, EnrichedSectionHeaderItem):
            text = f'{"#" * max(1, min(6, element.level + 1))} {text}'
        elif is_list:
            marker = element.marker or ('1.' if element.enumerated else '-')
            if not text.startswith(f'{marker} '):
                text = f'{marker} {text}'
            indent = '    ' * max(0, depths[element.element_ref] - 1)
            text = indent + text.replace('\n', '\n' + indent + ' ' * (len(marker) + 1))
        elif isinstance(element, EnrichedCodeItem):
            code = element.content_for_llm
            caption = text[:-len(code)] if text.endswith(code) and text != code else ''
            fence = '`' * max(3, max((len(run) for run in re.findall(r'`+', code)), default=0) + 1)
            text = f'{caption}{fence}{element.code_language}\n{code}\n{fence}'
        elif isinstance(element, EnrichedFormulaItem) and not text.lstrip().startswith('$$'):
            text = f'$$\n{text}\n$$'
        yield element, text, sources


def document_markdown(document: EnrichedDocument) -> str:
    """Render faithful canonical content without models or backend dependencies."""
    parts, previous_list = [], False
    for element, text, _ in markdown_content(document):
        is_list = isinstance(element, EnrichedListItem)
        if parts:
            parts.append('\n' if is_list and previous_list else '\n\n')
        parts.append(text)
        previous_list = is_list
    return ''.join(parts)


def visual_description(text: str) -> str:
    """Keep the full visual description, including paragraph boundaries."""
    text = text.strip()
    if text.startswith(('{', '[', '```')):
        return ''
    return text


def extract_common_fields(ctx: WrappingContext, item: NodeItem) -> dict[str, Any]:
    """Preserve every geometry entry; group nodes have no provenance."""
    locations = ()
    if isinstance(item, DocItem):
        locations = tuple(
            ElementLocation(
                page_no=prov.page_no,
                bbox=tuple(prov.bbox.as_tuple()),
                coord_origin=prov.bbox.coord_origin.value,
                charspan=tuple(prov.charspan) if prov.charspan is not None else None,
            )
            for prov in item.prov
        )
    return {
        "pdfpath": ctx.pdfpath,
        "document_id": ctx.document_id,
        "element_ref": ctx.ref(item.self_ref),
        "source_class": type(item).__name__,
        "source_label": item.label.value if hasattr(item, "label") else None,
        "content_layer": item.content_layer.value,
        "parent_ref": ctx.ref(item.parent),
        "children_refs": tuple(ctx.ref(x) for x in item.children),
        "locations": locations,
    }


T = TypeVar("T", bound=EnrichedTextItem)


def _wrap_text(
    item: TextItem,
    ctx: WrappingContext,
    cls: type[T],
    kind: EnrichedElementType,
    **extra: Any,
) -> T:
    searchable = item.text if kind in (EnrichedElementType.CODE, EnrichedElementType.FORMULA) else embedding_text(item.text)
    if item.label in (DocItemLabel.PAGE_HEADER, DocItemLabel.PAGE_FOOTER):
        searchable = ''
    return cls(
        **extract_common_fields(ctx, item),
        element_type=kind,
        original_text=item.orig,
        content_for_embedding=searchable if searchable.strip() else None,
        content_for_llm=item.text,
        formatting=item.formatting.model_dump(mode="json") if item.formatting is not None else None,
        hyperlink=str(item.hyperlink) if item.hyperlink is not None else None,
        **extra,
    )


def handle_text(item: TextItem, *, ctx: WrappingContext) -> EnrichedTextItem:
    return _wrap_text(item, ctx, EnrichedTextItem, EnrichedElementType.TEXT)


def handle_title(item: TitleItem, *, ctx: WrappingContext) -> EnrichedTitleItem:
    return _wrap_text(item, ctx, EnrichedTitleItem, EnrichedElementType.TITLE)


def handle_section_header(item: SectionHeaderItem, *, ctx: WrappingContext) -> EnrichedSectionHeaderItem:
    return _wrap_text(item, ctx, EnrichedSectionHeaderItem, EnrichedElementType.SECTION_HEADER, level=item.level)


def handle_list_item(item: ListItem, *, ctx: WrappingContext) -> EnrichedListItem:
    return _wrap_text(item, ctx, EnrichedListItem, EnrichedElementType.LIST_ITEM,
                      marker=item.marker, enumerated=item.enumerated)


def handle_formula(item: FormulaItem, *, ctx: WrappingContext) -> EnrichedFormulaItem:
    return _wrap_text(item, ctx, EnrichedFormulaItem, EnrichedElementType.FORMULA)


def handle_code(item: CodeItem, *, ctx: WrappingContext) -> EnrichedCodeItem:
    return _wrap_text(item, ctx, EnrichedCodeItem, EnrichedElementType.CODE,
                      code_language=item.code_language.value,
                      caption=item.caption_text(ctx.doc),
                      caption_refs=tuple(ctx.ref(x) for x in item.captions))


def handle_table(item: TableItem, *, ctx: WrappingContext) -> EnrichedTableItem:
    markdown = item.export_to_markdown(doc=ctx.doc)
    searchable = render_table(item)
    return EnrichedTableItem(
        **extract_common_fields(ctx, item),
        element_type=EnrichedElementType.TABLE,
        caption=item.caption_text(ctx.doc),
        caption_refs=tuple(ctx.ref(x) for x in item.captions),
        num_rows=item.data.num_rows,
        num_cols=item.data.num_cols,
        table_data=item.data.model_dump(mode="json", exclude={"grid"}),
        content_for_embedding=searchable or None,
        content_for_llm=markdown,
    )


def _write_picture(item: PictureItem, ctx: WrappingContext, image: Image.Image | None) -> str | None:
    if ctx.asset_store is None:
        return None
    pil_image = image if image is not None else item.get_image(ctx.doc)
    if pil_image is None:
        return None
    buffer = BytesIO()
    pil_image.save(buffer, format="PNG")
    image_bytes = buffer.getvalue()
    ref_hash = hashlib.sha256(item.self_ref.encode("utf-8")).hexdigest()[:12]
    data_hash = hashlib.sha256(image_bytes).hexdigest()[:16]
    filename = f"{ctx.document_id[:12]}_{ref_hash}_{data_hash}.png"
    return ctx.asset_store.put(filename, image_bytes, media_type="image/png")


def handle_picture(item: PictureItem, *, ctx: WrappingContext, image: Image.Image | None = None) -> EnrichedPictureItem:
    caption = item.caption_text(ctx.doc)
    description = (
        item.meta.description.text
        if item.meta is not None and item.meta.description is not None
        else None
    )
    classification = (
        item.meta.classification.model_dump(mode="json")
        if item.meta is not None and item.meta.classification is not None
        else None
    )
    content = "\n".join(part for part in (caption, description) if part)
    return EnrichedPictureItem(
        **extract_common_fields(ctx, item),
        element_type=EnrichedElementType.PICTURE,
        caption=caption,
        caption_refs=tuple(ctx.ref(x) for x in item.captions),
        description=description,
        classification=classification,
        asset_path=_write_picture(item, ctx, image),
        content_for_embedding=embedding_text(content) or None,
        content_for_llm=content,
    )


def handle_field_heading(item: FieldHeadingItem, *, ctx: WrappingContext) -> EnrichedFieldHeadingItem:
    return _wrap_text(item, ctx, EnrichedFieldHeadingItem, EnrichedElementType.FIELD_HEADING, level=item.level)


def handle_field_value(item: FieldValueItem, *, ctx: WrappingContext) -> EnrichedFieldValueItem:
    return _wrap_text(item, ctx, EnrichedFieldValueItem, EnrichedElementType.FIELD_VALUE, kind=item.kind)


def table_rows(data: TableData) -> list[tuple[int, str, tuple[TableCell, ...]]]:
    """Render rows under their preceding header block, preserving merged cells."""
    headers: list[list[str]] = [[] for _ in range(data.num_cols)]
    header_rows: list[list[tuple[TableCell, str]]] = [[] for _ in range(data.num_rows)]
    rows: list[list[tuple[TableCell, str]]] = [[] for _ in range(data.num_rows)]
    for cell in data.table_cells:
        # Normalize before flattening: flattening first loses soft-hyphen breaks.
        value = ' '.join(embedding_text(cell.text).split())
        if cell.column_header:
            if 0 <= cell.start_row_offset_idx < len(rows):
                header_rows[cell.start_row_offset_idx].append((cell, value))
        else:
            for row in range(max(0, cell.start_row_offset_idx), min(cell.end_row_offset_idx, len(rows))):
                rows[row].append((cell, value))
    lines = []
    active_headers = []
    data_since_headers = False
    labels = [f'Colonne {col + 1}' for col in range(len(headers))]
    for row_index, (header_row, row) in enumerate(zip(header_rows, rows)):
        if header_row:
            if data_since_headers:
                headers = [[] for _ in headers]
                active_headers = []
            active_headers.extend(cell for cell, _ in header_row)
            data_since_headers = False
            for cell, value in sorted(header_row, key=lambda entry: entry[0].start_col_offset_idx):
                for col in range(max(0, cell.start_col_offset_idx), min(cell.end_col_offset_idx, len(headers))):
                    if value and value not in headers[col]:
                        headers[col].append(value)
            labels = [' / '.join(names) or f'Colonne {col + 1}' for col, names in enumerate(headers)]
        if not any(value for _, value in row):
            continue
        data_since_headers = True
        values = []
        for cell, value in sorted(row, key=lambda entry: entry[0].start_col_offset_idx):
            names = [labels[col]
                     for col in range(max(0, cell.start_col_offset_idx), min(cell.end_col_offset_idx, len(headers)))]
            label = " / ".join(dict.fromkeys(names))
            values.append(f"{label}: {value}")
        if values:
            lines.append((row_index, " ; ".join(values), tuple(active_headers) + tuple(cell for cell, _ in row)))
    if not lines:
        text = " ; ".join(dict.fromkeys(name for col in headers for name in col))
        if text:
            lines.append((0, text, tuple(active_headers)))
    return lines


def render_table(item: TableItem) -> str:
    return "\n".join(text for _, text, _ in table_rows(item.data))


def graph_text_fields(graph: dict[str, Any], document_id: str) -> dict[str, Any]:
    """Render explicit key/value edges; retain unpaired cells without guessing."""
    cells = {cell["cell_id"]: cell for cell in graph.get("cells", [])}
    if len(cells) != len(graph.get("cells", [])):
        raise ValueError("Duplicate graph cell IDs")
    lines, paired, edges = [], set(), set()
    for link in graph.get("links", []):
        source, target = cells[link["source_cell_id"]], cells[link["target_cell_id"]]
        if link["label"] == "to_key":
            source, target = target, source
        elif link["label"] != "to_value":
            continue
        edge = (source["cell_id"], target["cell_id"])
        if source["label"] == "key" and target["label"] in ("value", "checkbox") and edge not in edges:
            lines.append(f"{source['text']}: {target['text']}")
            paired.update(edge)
            edges.add(edge)
    lines.extend(cell["text"] for key, cell in cells.items() if key not in paired and cell["text"])
    content = "\n".join(lines)
    refs = tuple(dict.fromkeys(EnrichedRef(document_id, cell["item_ref"]["cref"])
                              for cell in cells.values() if cell.get("item_ref")))
    locations = []
    for cell in cells.values():
        if prov := cell.get("prov"):
            bbox = prov["bbox"]
            locations.append(ElementLocation(
                page_no=prov["page_no"], bbox=tuple(bbox[key] for key in ("l", "t", "r", "b")),
                coord_origin=bbox["coord_origin"],
                charspan=tuple(prov["charspan"]) if prov.get("charspan") is not None else None,
            ))
    return {"content_for_embedding": embedding_text(content) or None, "content_for_llm": content,
            "content_refs": refs, "content_locations": tuple(dict.fromkeys(locations))}


def handle_key_value(item: KeyValueItem, *, ctx: WrappingContext) -> EnrichedKeyValueItem:
    graph = item.graph.model_dump(mode="json")
    return EnrichedKeyValueItem(**extract_common_fields(ctx, item),
                                element_type=EnrichedElementType.KEY_VALUE,
                                graph_data=graph, **graph_text_fields(graph, ctx.document_id))


def handle_form(item: FormItem, *, ctx: WrappingContext) -> EnrichedFormItem:
    graph = item.graph.model_dump(mode="json")
    return EnrichedFormItem(**extract_common_fields(ctx, item),
                            element_type=EnrichedElementType.FORM,
                            graph_data=graph, **graph_text_fields(graph, ctx.document_id))


def handle_field_region(item: FieldRegionItem, *, ctx: WrappingContext) -> EnrichedFieldRegionItem:
    return EnrichedFieldRegionItem(**extract_common_fields(ctx, item),
                                   element_type=EnrichedElementType.FIELD_REGION)


def handle_field_item(item: FieldItem, *, ctx: WrappingContext) -> EnrichedFieldItem:
    return EnrichedFieldItem(**extract_common_fields(ctx, item),
                             element_type=EnrichedElementType.FIELD_ITEM)


def handle_group(item: GroupItem, *, ctx: WrappingContext) -> EnrichedGroupItem:
    return EnrichedGroupItem(**extract_common_fields(ctx, item),
                             element_type=EnrichedElementType.GROUP, name=item.name)


def handle_list_group(item: ListGroup, *, ctx: WrappingContext) -> EnrichedListGroupItem:
    return EnrichedListGroupItem(**extract_common_fields(ctx, item),
                                 element_type=EnrichedElementType.LIST_GROUP, name=item.name)


def handle_inline_group(item: InlineGroup, *, ctx: WrappingContext) -> EnrichedInlineGroupItem:
    return EnrichedInlineGroupItem(**extract_common_fields(ctx, item),
                                   element_type=EnrichedElementType.INLINE_GROUP, name=item.name)
