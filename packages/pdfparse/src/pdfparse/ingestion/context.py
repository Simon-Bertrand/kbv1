"""Pure small-to-large source assembly; no retrieval engine or model calls."""
from collections.abc import Iterable
from dataclasses import replace

from .text import prepare_text

from .contracts import PdfParseResult, SourceContext


def build_source_context(result: PdfParseResult, chunk_ids: Iterable[str], *,
                         max_characters: int) -> SourceContext:
    if type(max_characters) is not int or max_characters < 1:
        raise ValueError('max_characters must be a positive integer')
    if isinstance(chunk_ids, (str, bytes)):
        raise TypeError('chunk_ids must be an iterable of chunk IDs, not a string')
    ids = tuple(chunk_ids)
    if any(not isinstance(key, str) or not key.strip() for key in ids):
        raise ValueError('chunk_ids must contain nonblank strings')
    ids = tuple(dict.fromkeys(ids))
    available = {chunk.chunk_id: chunk for chunk in result.chunks}
    selected = [available[key] for key in ids]  # Unknown/foreign hits fail explicitly.
    document = result.document
    requested = dict.fromkeys(ref for chunk in selected for ref in chunk.source_refs)
    for chunk in selected:
        if chunk.document_id != document.document_id:
            raise ValueError('Chunk belongs to a different document')
    for ref in requested:
        ref.resolve(document)
    if not selected:
        return SourceContext(document.document_id, '', (), (), (), False)

    prepared = prepare_text(document)
    # Canonical blocks already group captions and graph cells. Select by owner:
    # a shared caption must not pull in an unrelated owner from another hit.
    blocks, sources = [], {}
    length = 0
    for block in prepared.blocks:
        owner, text, contributors = block.element, block.text, block.sources
        if owner.element_ref not in requested:
            continue
        length += len(text) + (2 if blocks else 0)
        blocks.append(text)
        sources.update((source.element_ref, source) for source in contributors)
    expanded = bool(blocks) and length <= max_characters and requested.keys() <= sources.keys()
    if expanded:
        text = '\n\n'.join(blocks)
        refs = tuple(sources)
        locations = tuple(dict.fromkeys(
            loc for source in sources.values()
            for loc in (*source.locations, *getattr(source, 'content_locations', ()))
        ))
    else:
        excerpts = list(selected)
        # Merge only proven canonical overlap. Equal text at different offsets
        # remains independent, as do custom chunks without exact coordinates.
        for chunk in excerpts:
            if chunk.start_index is not None and (
                chunk.end_index is None or prepared.text[chunk.start_index:chunk.end_index] != chunk.text
            ):
                raise ValueError('Chunk does not match canonical source text')
        merged = []
        for chunk in excerpts:
            overlaps = [i for i, other in enumerate(merged)
                        if chunk.start_index is not None and other.start_index is not None
                        and chunk.start_index <= other.end_index and other.start_index <= chunk.end_index]
            if not overlaps:
                merged.append(chunk)
                continue
            members = [merged[i] for i in overlaps] + [chunk]
            start = min(item.start_index for item in members)
            end = max(item.end_index for item in members)
            context = '\n\n'.join(dict.fromkeys(item.context for item in members if item.context))
            combined = replace(members[0], text=prepared.text[start:end], context=context,
                               start_index=start, end_index=end,
                               source_refs=tuple(dict.fromkeys(ref for item in members for ref in item.source_refs)),
                               locations=tuple(dict.fromkeys(loc for item in members for loc in item.locations)))
            merged[overlaps[0]] = combined
            for i in reversed(overlaps[1:]):
                del merged[i]
        def size(items):
            return sum(len(item.contextualized_text) for item in items) + 2 * (len(items) - 1)
        length = size(merged)
        if length > max_characters:
            raise ValueError(f'Selected chunks require {length} characters; budget is {max_characters}. '
                             'Select fewer chunks or increase max_characters.')
        # Preserve every hit first, then spend remaining capacity in rank order.
        for index, chunk in enumerate(merged):
            if chunk.start_index is None:
                continue
            owners = [block for block in prepared.blocks
                      if block.start < chunk.end_index and block.end > chunk.start_index]
            if not owners:
                continue
            candidate = prepared.chunk(owners[0].start, owners[-1].end, chunk.transformation_id)
            if not set(chunk.source_refs).issubset(candidate.source_refs):
                continue
            trial = merged[:index] + [candidate] + merged[index + 1:]
            # Avoid duplicating another hit when expanding a shared parent.
            if any(other.start_index is not None and candidate.start_index < other.end_index
                   and other.start_index < candidate.end_index for i, other in enumerate(merged) if i != index):
                continue
            if size(trial) <= max_characters and len(candidate.text) > len(chunk.text):
                merged = trial
                expanded = True
        text = '\n\n'.join(chunk.contextualized_text for chunk in merged)
        refs = tuple(dict.fromkeys(ref for chunk in merged for ref in chunk.source_refs))
        locations = tuple(dict.fromkeys(loc for chunk in merged for loc in chunk.locations))
    return SourceContext(document.document_id, text, refs, locations, ids, expanded)
