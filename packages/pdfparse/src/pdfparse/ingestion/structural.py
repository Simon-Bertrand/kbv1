"""Structure-first chunking with hard token-budget and source-span invariants.

Integration assumptions (verified against pdfparse/ingestion/text.py):
* prepared.text is the canonical text and prepared.blocks covers each indexable block.
* prepared.chunk(start, end, transformation_id, extra_context=..., split_reason=...)
  returns a Chunk with text == prepared.text[start:end], context and deterministic ID.
* prepared.contextualized_span(...) uses the same context rules as prepared.chunk(...).
* Extra context entries are (text, tuple[EnrichedElement, ...]).
"""
from __future__ import annotations

import re
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass, replace

from pdfparse.enrich import (
    EnrichedDocument,
    EnrichedElement,
    EnrichedListGroupItem,
    EnrichedListItem,
    EnrichedRef,
    EnrichedTableItem,
)

from .contracts import BaseChunkingModel, Chunk
from .text import DocumentText, TextBlock, prepare_text

Context = tuple[tuple[str, tuple[EnrichedElement, ...]], ...]
_SENTENCE = re.compile(r"(?<=[.!?。！？])\s+")
_LINE = re.compile(r"\r?\n+")
_WORD = re.compile(r"\s+")


class ChunkingInvariantError(ValueError):
    """A chunk cannot be emitted without violating a required invariant."""


@dataclass(frozen=True, slots=True)
class _Unit:
    start: int
    end: int
    sections: tuple[EnrichedRef, ...]
    context: Context = ()
    kind: str = "body"
    pages: tuple[int, ...] = ()
    group: EnrichedRef | None = None
    section_drop: int = 0


class StructuralChunkingModel(BaseChunkingModel):
    """A conservative chunker for pdfparse's canonical DocumentText.

    All size checks include section context, extra context, E5 prefix and special
    tokens through the injected count_tokens function. On impossible input,
    raise an exception rather than silently truncating or dropping text.
    """

    VERSION = "v5"

    def __init__(
        self,
        *,
        chunk_size: int = 512,
        target_tokens: int = 384,
        min_tokens: int = 128,
        count_tokens: Callable[[str], int] | None = None,
        tokenizer_id: str | None = None,
        respect_page_boundaries: bool = False,
        table_header_getter: Callable[[TextBlock], str | None] | None = None,
    ) -> None:
        if type(respect_page_boundaries) is not bool:
            raise ValueError("respect_page_boundaries must be a boolean")
        if type(chunk_size) is not int or chunk_size < 1:
            raise ValueError("chunk_size must be a positive integer")
        if count_tokens is not None and not callable(count_tokens):
            raise TypeError("count_tokens must be callable")
        if count_tokens is not None and (not isinstance(tokenizer_id, str) or not tokenizer_id.strip()):
            raise ValueError("A custom token counter requires a stable tokenizer_id")
        if count_tokens is None and tokenizer_id not in (None, "character"):
            raise ValueError("A custom tokenizer_id requires count_tokens")

        target = min(target_tokens, chunk_size)
        minimum = min(min_tokens, target)
        if not 0 < minimum <= target <= chunk_size:
            raise ValueError("Require 0 < min <= target <= max")

        self.chunk_size = chunk_size
        self.target_tokens = target
        self.min_tokens = minimum  # Soft goal, never pad or discard small chunks.
        self.count_tokens = count_tokens if count_tokens is not None else len
        self.tokenizer_id = tokenizer_id or "character"
        self.respect_page_boundaries = respect_page_boundaries
        self.table_header_getter = table_header_getter
        self.transformation_id = (
            f"pdfparse.structural.{self.VERSION}:{self.tokenizer_id}:"
            f"{chunk_size}:{target}:{minimum}:"
            f"pages={int(respect_page_boundaries)}"
        )

    def _count(self, text: str) -> int:
        n = self.count_tokens(text)
        if type(n) is not int or n < 0:
            raise ChunkingInvariantError(f"Token counter must return a nonnegative integer, got {n!r}")
        return n

    def _fit(self, prepared, unit, budget, *, trim_context=True):
        if not 0 <= unit.start < unit.end <= len(prepared.text):
            raise ChunkingInvariantError(f"Invalid source span: {unit.start}:{unit.end}")
        variants = len(unit.context) + 1 if trim_context else 1
        for dropped in range(variants):
            candidate = replace(unit, context=unit.context[dropped:])
            text = prepared.contextualized_span(
                unit.start, unit.end, extra_context=candidate.context, section_drop=unit.section_drop,
            )
            count = self._count(text)
            if count <= budget:
                return candidate, count
        return None

    def _emit(self, prepared, unit, reason=None):
        chunk = prepared.chunk(
            unit.start, unit.end, self.transformation_id, extra_context=unit.context,
            section_drop=unit.section_drop, split_reason=reason,
        )
        return replace(chunk, token_count=self._count(chunk.contextualized_text),
                       tokenizer_id=self.tokenizer_id)

    @staticmethod
    def _ancestors(prepared: DocumentText, element: EnrichedElement) -> list[EnrichedElement]:
        """Outermost-to-innermost ancestors, with cycle detection."""
        out: list[EnrichedElement] = []
        visited = {element.element_ref}
        ref = element.parent_ref
        while ref is not None:
            if ref in visited:
                raise ChunkingInvariantError("Cycle in enriched-element ancestry")
            visited.add(ref)
            ancestor = prepared.document.get(ref)
            if ancestor is None:
                raise ChunkingInvariantError(f"Missing parent element {ref!r}")
            out.append(ancestor)
            ref = ancestor.parent_ref
        return list(reversed(out))

    def _units(self, prepared: DocumentText) -> Iterator[_Unit]:
        for block in prepared.blocks:
            if not (0 <= block.start <= block.end <= len(prepared.text)):
                raise ChunkingInvariantError("Invalid TextBlock span")
            if block.start == block.end:
                continue  # Empty source block has no characters to index.
            element = block.element
            parents = self._ancestors(prepared, element)
            sections = tuple(section.element_ref for section in block.sections)
            pages = tuple(sorted({loc.page_no for loc in element.locations}))
            list_roots = [p.element_ref for p in parents if isinstance(p, EnrichedListGroupItem)]
            if isinstance(element, EnrichedListGroupItem):
                list_roots.append(element.element_ref)
            list_root = list_roots[-1] if list_roots else None
            ancestors_context: Context = tuple(
                (p.content_for_embedding, (p,))
                for p in parents
                if isinstance(p, EnrichedListItem) and p.content_for_embedding
            )
            if isinstance(element, EnrichedTableItem):
                kind = "table"
                group = element.element_ref
            elif list_root is not None or isinstance(element, EnrichedListItem):
                kind = "list"
                group = list_root
            else:
                kind = str(element.element_type.value).lower()
                group = None

            unit = _Unit(
                block.start, block.end, sections,
                ancestors_context, kind, pages, group,
            )

            if self.respect_page_boundaries and block.page_spans:
                for left, right, loc in block.page_spans:
                    if left < right:
                        yield replace(unit, start=left, end=right, pages=(loc.page_no,))
                continue

            if not isinstance(element, EnrichedTableItem) or self._fit(
                prepared, unit, self.chunk_size
            ) is not None:
                yield unit
                continue

            body = element.content_for_embedding or ""
            caption = block.text[:-len(body)].rstrip("\n") if body and block.text.endswith(body) else ""
            header = self.table_header_getter(block) if self.table_header_getter else None
            if header is not None and not isinstance(header, str):
                raise TypeError("table_header_getter must return str | None")
            extra = ancestors_context + tuple((value, block.sources) for value in (caption, header) if value)
            cursor = block.start
            if caption and block.text.startswith(caption + "\n"):
                cursor += len(caption) + 1
                yield replace(unit, end=cursor, kind="table_row", context=extra)
            for line in prepared.text[cursor:block.end].splitlines(keepends=True):
                end = cursor + len(line)
                yield replace(unit, start=cursor, end=end, kind="table_row", context=extra)
                cursor = end

    def _merge(self, left: _Unit, right: _Unit) -> _Unit | None:
        if left.end > right.start or left.sections != right.sections:
            return None
        if left.kind != right.kind or left.kind in {"title", "heading", "section"}:
            return None
        if self.respect_page_boundaries and left.pages != right.pages:
            return None
        if left.kind in {"list", "table_row"}:
            if left.group is None or left.group != right.group:
                return None
            if left.context != right.context:
                return None
            context = left.context
        else:
            if left.context != right.context or left.group != right.group:
                return None
            context = left.context
        return replace(
            left,
            end=right.end,
            context=context,
            pages=tuple(sorted(set(left.pages) | set(right.pages))),
        )

    def _boundary_candidate(self, prepared, unit, positions, budget):
        low, high, best = 0, len(positions) - 1, None
        while low <= high:
            mid = (low + high) // 2
            trial = self._fit(prepared, replace(unit, end=positions[mid]), budget, trim_context=False)
            if trial is not None:
                best = trial
                low = mid + 1
            else:
                high = mid - 1
        return best

    def _split(self, prepared: DocumentText, unit: _Unit) -> Iterator[Chunk]:
        cursor = unit.start
        reason = f"oversized_{unit.kind}"
        if unit.kind == "table_row":
            first = prepared.text[unit.start:unit.end].split(" ; ", 1)
            if len(first) == 2:
                unit = replace(unit, context=unit.context + ((first[0], ()),))
        while cursor < unit.end:
            rest = replace(unit, start=cursor)
            one = replace(rest, end=cursor + 1)
            fit = self._fit(prepared, one, self.chunk_size)
            while fit is None and rest.section_drop < len(rest.sections):
                rest = replace(rest, section_drop=rest.section_drop + 1)
                fit = self._fit(prepared, replace(rest, end=cursor + 1), self.chunk_size)
            if fit is None:
                raise ChunkingInvariantError(f"Source text cannot fit chunk_size={self.chunk_size}")
            rest = replace(rest, context=fit[0].context)
            split_reason = reason
            if rest.section_drop:
                split_reason += ";reduced_section_context"
            if rest.context != unit.context:
                split_reason += ";reduced_extra_context"

            # Grow the measured window only while it fits. Long blocks no longer
            # retokenize the entire remaining text for every emitted fragment.
            width = min(unit.end - cursor, max(1, self.target_tokens))
            while True:
                stop = cursor + width
                trial = self._fit(prepared, replace(rest, end=stop), self.chunk_size, trim_context=False)
                if trial is None:
                    break
                if stop == unit.end:
                    yield self._emit(prepared, trial[0], split_reason)
                    return
                width = min(unit.end - cursor, width * 2)

            window = prepared.text[cursor:stop]
            chosen = None
            for budget in (self.target_tokens, self.chunk_size):
                for pattern in (_SENTENCE, _LINE, _WORD):
                    cuts = [cursor + match.end() for match in pattern.finditer(window)]
                    trial = self._boundary_candidate(prepared, rest, cuts, budget)
                    if trial and (chosen is None or trial[1] >= self.min_tokens):
                        chosen = trial
                    if chosen and chosen[1] >= self.min_tokens:
                        break
                if chosen and chosen[1] >= self.min_tokens:
                    break
            if chosen is None or chosen[1] < self.min_tokens:
                trial = self._boundary_candidate(prepared, rest, range(cursor + 1, stop + 1), self.chunk_size)
                if trial:
                    chosen = trial
            fragment = chosen[0] if chosen else fit[0]
            yield self._emit(prepared, fragment, split_reason)
            cursor = fragment.end

    def _validate(self, prepared: DocumentText, chunks: list[Chunk]) -> None:
        ranges: list[tuple[int, int]] = []
        for index, chunk in enumerate(chunks):
            start, end = chunk.start_index, chunk.end_index
            if start is None or end is None or not 0 <= start < end <= len(prepared.text):
                raise ChunkingInvariantError(f"Missing/invalid offsets in chunk {index}")
            if chunk.text != prepared.text[start:end]:
                raise ChunkingInvariantError(f"Chunk {index} does not match its source span")
            actual = self._count(chunk.contextualized_text)
            if actual > self.chunk_size or actual != chunk.token_count:
                raise ChunkingInvariantError(
                    f"Chunk {index}: actual={actual}, stored={chunk.token_count}"
                )
            if chunk.tokenizer_id != self.tokenizer_id:
                raise ChunkingInvariantError(f"Incorrect tokenizer for chunk {index}")
            ranges.append((start, end))

        if ranges != sorted(ranges):
            raise ChunkingInvariantError("Chunk offsets are not ordered")
        if any(left[1] > right[0] for left, right in zip(ranges, ranges[1:])):
            raise ChunkingInvariantError("Overlapping source spans")

        # Every character of every indexable TextBlock must be present exactly
        # once. Two-pointer scan: O(number of source blocks + number of chunks).
        i = 0
        for block in prepared.blocks:
            if block.start == block.end:
                continue
            cursor = block.start
            while i < len(ranges) and ranges[i][1] <= cursor:
                i += 1
            j = i
            while cursor < block.end and j < len(ranges):
                start, end = ranges[j]
                if start > cursor or end <= cursor:
                    break
                cursor = min(end, block.end)
                j += 1
            if cursor != block.end:
                raise ChunkingInvariantError(
                    f"Incomplete source block {block.start}:{block.end}, stopped at {cursor}"
                )

    def chunk(self, document: EnrichedDocument) -> list[Chunk]:
        prepared = prepare_text(document)
        if not prepared.blocks:
            return []
        results: list[Chunk] = []
        pending: _Unit | None = None

        def flush() -> None:
            nonlocal pending
            if pending is None:
                return
            results.append(self._emit(prepared, pending))
            pending = None

        for unit in self._units(prepared):
            safe = self._fit(prepared, unit, self.chunk_size, trim_context=False)
            if safe is None:
                flush()
                results.extend(self._split(prepared, unit))
                continue
            if pending is not None:
                merged = self._merge(pending, unit)
                if merged is not None:
                    budget = self.chunk_size if merged.kind == "list" else self.target_tokens
                    trial = self._fit(prepared, merged, budget, trim_context=False)
                    if trial is not None:
                        pending = trial[0]
                        continue
                flush()
            pending = unit

        flush()
        self._validate(prepared, results)
        return results
