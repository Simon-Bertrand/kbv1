"""Canonical text coordinates and provenance shared by chunking strategies."""
from bisect import bisect_right
from dataclasses import dataclass
import hashlib
import json

from pdfparse.enrich import EnrichedDocument, EnrichedElement, EnrichedRef, EnrichedSectionHeaderItem, EnrichedTitleItem
from pdfparse.enrich.abstraction import ElementLocation, EnrichedPageDescriptionItem
from pdfparse.enrich.handlers import document_content, embedding_text
from .contracts import Chunk


@dataclass(frozen=True, slots=True)
class TextBlock:
    element: EnrichedElement
    text: str
    sources: tuple[EnrichedElement, ...]
    sections: tuple[EnrichedElement, ...]
    start: int
    end: int
    page_spans: tuple[tuple[int, int, ElementLocation], ...] = ()

    def body_locations(self, start: int, end: int):
        if self.page_spans:
            return tuple(loc for left, right, loc in self.page_spans if left < end and right > start)
        return (*self.element.locations, *getattr(self.element, 'content_locations', ()))


def _page_spans(element, text, start):
    original = getattr(element, 'original_text', None)
    locations = element.locations
    if not isinstance(original, str) or len({loc.page_no for loc in locations}) < 2:
        return ()
    if any(not loc.charspan for loc in locations):
        return ()
    ordered = sorted(locations, key=lambda loc: loc.charspan)
    cursor, raw_cursor, spans, normalized = start, 0, [], []
    for loc in ordered:
        left, right = loc.charspan
        if not raw_cursor <= left < right <= len(original) or original[raw_cursor:left].strip():
            return ()
        part = embedding_text(original[raw_cursor:right])
        normalized.append(part)
        spans.append((cursor, cursor + len(part), loc))
        cursor += len(part)
        raw_cursor = right
    tail = embedding_text(original[raw_cursor:])
    if tail.strip() or ''.join(normalized) + tail != text:
        return ()
    left, right, loc = spans[-1]
    spans[-1] = (left, right + len(tail), loc)
    return tuple(spans)



@dataclass(slots=True)
class DocumentText:
    document: EnrichedDocument
    text: str
    blocks: list[TextBlock]
    ends: list[int]
    positions: dict[EnrichedRef, tuple[int, int]]

    def blocks_in_span(self, start: int, end: int):
        first = bisect_right(self.ends, start)
        last = first
        while last < len(self.blocks) and self.blocks[last].start < end:
            last += 1
        return self.blocks[first:last]

    def _span(self, start: int, end: int, extra_context, section_drop=0):
        if type(start) is not int or type(end) is not int or not 0 <= start < end <= len(self.text):
            raise ValueError('Chunk offsets must be exact character offsets into canonical text')
        selected = self.blocks_in_span(start, end)
        if not selected:
            raise ValueError('Chunk has no source element')
        common = list(selected[0].sections)
        for block in selected[1:]:
            length = 0
            for left, right in zip(common, block.sections):
                if left.element_ref != right.element_ref:
                    break
                length += 1
            del common[length:]
        prefix = [item for item in common if not (
            start <= self.positions[item.element_ref][0] and self.positions[item.element_ref][1] <= end)]
        prefix = prefix[section_drop:]
        contexts = [item.content_for_embedding for item in prefix]
        contributors = list(prefix)
        excerpt = self.text[start:end]
        for value, items in extra_context:
            if value and f'\n{value}\n' not in f'\n{excerpt}\n' and not excerpt.startswith(value + ' ; '):
                contexts.append(value)
                contributors.extend(items)
        return selected, common, contributors, '\n'.join(contexts), excerpt

    def contextualized_span(self, start: int, end: int, *, extra_context=(), section_drop=0) -> str:
        """Measure candidate text without constructing provenance, hashes or chunks."""
        _, _, _, context, excerpt = self._span(start, end, extra_context, section_drop)
        return f'{context}\n\n{excerpt}' if context else excerpt

    def chunk(self, start: int, end: int, transformation_id: str, *,
              extra_context: tuple[tuple[str, tuple[EnrichedElement, ...]], ...] = (),
              split_reason: str | None = None, section_drop: int = 0) -> Chunk:
        selected, common, contributors, context, excerpt = self._span(start, end, extra_context, section_drop)
        sources = {item.element_ref: item for block in selected for item in block.sources}
        sources.update((item.element_ref, item) for item in (*contributors, *common))
        refs = tuple(sources)
        by_ref = {block.element.element_ref: block.body_locations(start, end) for block in selected}
        locations = tuple(dict.fromkeys(
            loc for ref, item in sources.items()
            for loc in (by_ref[ref] if ref in by_ref else (*item.locations, *getattr(item, 'content_locations', ())))
        ))
        identity = json.dumps([self.document.document_id, transformation_id, start, end,
                               [ref.cref for ref in refs], context, self.text[start:end], split_reason],
                              ensure_ascii=False, separators=(',', ':'))
        return Chunk(hashlib.sha256(identity.encode()).hexdigest(), self.document.document_id,
                     self.text[start:end], refs, locations, transformation_id, context,
                     start, end, tuple(block.element.element_ref for block in selected),
                     tuple(item.element_ref for item in common), split_reason)


def prepare_text(document: EnrichedDocument) -> DocumentText:
    blocks, positions, headings = [], {}, []
    title = None
    sections = ()
    cursor = 0
    for element, text, sources in document_content(document, embedding=True):
        if isinstance(element, EnrichedTitleItem):
            title = element
            headings.clear()
            sections = (title,)
        elif isinstance(element, EnrichedSectionHeaderItem):
            while headings and headings[-1].level >= element.level:
                headings.pop()
            headings.append(element)
            sections = tuple(([title] if title is not None else []) + headings)
        if blocks:
            cursor += 2
        start = cursor
        cursor += len(text)
        positions[element.element_ref] = (start, cursor)
        block_sections = () if isinstance(element, EnrichedPageDescriptionItem) else sections
        blocks.append(TextBlock(element, text, sources, block_sections, start, cursor, _page_spans(element, text, start)))
    return DocumentText(document, '\n\n'.join(block.text for block in blocks), blocks,
                        [block.end for block in blocks], positions)
