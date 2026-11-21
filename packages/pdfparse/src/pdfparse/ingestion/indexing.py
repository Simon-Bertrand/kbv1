"""Pre-index diagnostics and deterministic, versioned JSONL records."""
from collections import Counter
from collections.abc import Iterator, Mapping
from dataclasses import asdict, dataclass
from enum import StrEnum
import hashlib
import json
import os
from pathlib import Path
import re
from tempfile import NamedTemporaryFile

from .contracts import PdfParseResult
from .text import prepare_text
from pdfparse.enrich import EnrichedPageDescriptionItem, EnrichedPictureItem


class TextOrigin(StrEnum):
    NATIVE = 'native'
    OCR = 'ocr'
    MIXED = 'mixed'
    GENERATED = 'generated'
    UNKNOWN = 'unknown'


@dataclass(frozen=True, slots=True)
class QualityReport:
    searchable_characters: int
    covered_characters: int | None
    coverage: float | None
    uncovered_spans: tuple[tuple[int, int], ...]
    unknown_offset_chunk_ids: tuple[str, ...]
    unindexed_refs: tuple[str, ...]
    pages_without_indexable_text: tuple[int, ...]
    page_inventory_complete: bool
    suspicious_text_refs: tuple[str, ...]
    repetitive_text_refs: tuple[str, ...]
    extraction_evidence_available: bool = False
    unmatched_extraction_regions: tuple[int, ...] = ()
    extracted_but_unindexed_refs: tuple[str, ...] = ()


def assess_quality(result: PdfParseResult, *, _prepared=None) -> QualityReport:
    prepared = _prepared if _prepared is not None else prepare_text(result.document)
    text = prepared.text
    spans, unknown = [], []
    cited = set()
    for chunk in result.chunks:
        cited.update(ref.cref for ref in (chunk.body_refs or chunk.source_refs))
        start, end = chunk.start_index, chunk.end_index
        if start is None and end is None:
            unknown.append(chunk.chunk_id)
            continue
        if (type(start) is not int or type(end) is not int or not 0 <= start < end <= len(text)
                or text[start:end] != chunk.text):
            raise ValueError(f'Invalid canonical offsets for chunk {chunk.chunk_id}')
        spans.append((start, end))
    merged = []
    for start, end in sorted(spans):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(end, merged[-1][1]))
        else:
            merged.append((start, end))
    total = sum(not char.isspace() for char in text)
    covered = sum(sum(not char.isspace() for char in text[start:end]) for start, end in merged)
    gaps, cursor = [], 0
    for start, end in (*merged, (len(text), len(text))):
        if text[cursor:start].strip():
            gaps.append((cursor, start))
        cursor = end
    unindexed = tuple(block.element.element_ref.cref for block in prepared.blocks
                      if block.element.element_ref.cref not in cited)
    text_pages = {loc.page_no for block in prepared.blocks for source in block.sources
                  for loc in (*source.locations, *getattr(source, 'content_locations', ()))}
    observed_pages = {loc.page_no for source in result.document.elements.values()
                      for loc in (*source.locations, *getattr(source, 'content_locations', ()))}
    all_pages = set(result.page_numbers) if result.page_numbers is not None else observed_pages
    suspicious, repetitive = [], []
    for element in result.document.elements.values():
        original = getattr(element, 'original_text', None) or getattr(element, 'content_for_llm', '') or ''
        classification = getattr(element, 'classification', None)
        ocr = classification.get('ocr_text', '') if isinstance(classification, dict) else ''
        texts = (original, ocr) if isinstance(ocr, str) else (original,)
        if any(re.search(r'[\ufffd\x00-\x08\x0b\x0c\x0e-\x1f\x7f]', value) for value in texts):
            suspicious.append(element.element_ref.cref)
        for value in texts:
            lines = Counter(line.strip() for line in value.splitlines() if len(line.strip()) >= 12)
            if (any(count >= 3 for count in lines.values())
                    or re.search(r'\b(\w+)(?:\s+\1){7,}\b', value, re.IGNORECASE)):
                repetitive.append(element.element_ref.cref)
                break
    return QualityReport(total, None if unknown else covered,
                         None if unknown else covered / total if total else 1.0,
                         () if unknown else tuple(gaps), tuple(unknown), unindexed,
                         tuple(sorted(all_pages - text_pages)), result.page_numbers is not None,
                         tuple(suspicious), tuple(repetitive), result.extraction_evidence_available,
                         tuple(i for i, region in enumerate(result.extraction) if not region.source_refs),
                         tuple(dict.fromkeys(ref for region in result.extraction for ref in region.source_refs
                                             if region.text_origin != 'unknown' and ref not in cited)))


def _json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def _picture_owners(document):
    """Resolve nearest figure ancestors once, including native text inside figures."""
    owners = {None: None}
    for element in document.elements.values():
        pending, ref = [], element.element_ref
        while ref not in owners:
            item = document.get(ref)
            pending.append(item)
            ref = item.parent_ref
        for item in reversed(pending):
            owners[item.element_ref] = item if isinstance(item, EnrichedPictureItem) else owners[item.parent_ref]
    return owners


def _visual_asset(picture, *, role):
    classification = picture.classification or {}
    return {
        'ref': picture.element_ref.cref, 'role': role, 'asset_path': picture.asset_path,
        'caption': picture.caption, 'description': picture.description,
        'ocr_text': classification.get('ocr_text'),
        'classifier_model_id': classification.get('model_id'),
        'enrichment_id': classification.get('transformation_id'),
        'locations': [asdict(loc) for loc in picture.locations],
    }


def visual_records(result: PdfParseResult, *, _models=None, _owners=None) -> Iterator[dict]:
    """Export every figure once, including figures without searchable text."""
    from .preparation import element_model_provenance
    models = _models if _models is not None else element_model_provenance(result)
    owners = _owners if _owners is not None else _picture_owners(result.document)
    chunk_ids = {}
    for chunk in result.chunks:
        figures = {owners[ref].element_ref for ref in chunk.source_refs if owners[ref] is not None}
        for ref in figures:
            chunk_ids.setdefault(ref, []).append(chunk.chunk_id)
    for picture in result.document.elements.values():
        if not isinstance(picture, EnrichedPictureItem):
            continue
        identity = [result.document.document_id, picture.element_ref.cref, picture.asset_path]
        record = {
            'format': 'pdfparse.visual_record', 'schema_version': 1,
            'id': hashlib.sha256(_json(identity).encode()).hexdigest(),
            'document_id': result.document.document_id, 'document_path': str(result.document.pdfpath),
            'assets_dir': str(result.document.assets_dir) if result.document.assets_dir is not None else None,
            **_visual_asset(picture, role='source'),
            'models': models[picture.element_ref.cref],
            'classification': picture.classification,
            'chunk_ids': chunk_ids.get(picture.element_ref, []),
        }
        record['record_hash'] = hashlib.sha256(_json(record).encode()).hexdigest()
        yield record


def index_records(result: PdfParseResult, *, language: str | None = None,
                  text_origins: Mapping[str, str] | None = None, _prepared=None,
                  _models=None, _owners=None) -> Iterator[dict]:
    if language is not None and (not isinstance(language, str) or not language.strip()):
        raise ValueError('language must be a nonblank language tag or None')
    observed = {}
    for region in result.extraction:
        if region.text_origin != 'unknown':
            for ref in region.source_refs:
                observed.setdefault(ref, set()).add(region.text_origin)
    origins = {ref: next(iter(values)) if len(values) == 1 else 'mixed' for ref, values in observed.items()}
    pages = {}
    for element in result.document.elements.values():
        ref = element.element_ref.cref
        if isinstance(element, EnrichedPageDescriptionItem):
            origins[ref] = TextOrigin.GENERATED
            pages[element.page_no] = {
                'ref': ref, 'page_no': element.page_no, 'description': element.description,
                'model_id': element.model_id, 'transformation_id': element.transformation_id,
                'source_markdown_hash': element.source_markdown_hash,
                'source_refs': [source.cref for source in element.content_refs],
            }
        elif isinstance(element, EnrichedPictureItem):
            declared = (element.classification or {}).get('text_origin')
            if declared is not None:
                origins[ref] = TextOrigin.MIXED if element.caption or ref in origins else TextOrigin(declared)
                continue
            has_ocr = bool((element.classification or {}).get('ocr_text'))
            has_description = bool(element.description and element.description.strip())
            if has_description:
                origins[ref] = TextOrigin.MIXED if has_ocr or element.caption or ref in origins else TextOrigin.GENERATED
            elif has_ocr:
                origins[ref] = TextOrigin.MIXED if element.caption or origins.get(ref) == TextOrigin.NATIVE else TextOrigin.OCR
    origins.update(text_origins or {})
    if any(ref not in result.document.elements for ref in origins):
        raise ValueError('text_origins contains an unknown element reference')
    origins = {ref: TextOrigin(origin) for ref, origin in origins.items()}
    # Shared text fingerprint identifies the coordinate system across exports.
    prepared = _prepared if _prepared is not None else prepare_text(result.document)
    text_hash = hashlib.sha256(prepared.text.encode()).hexdigest()
    from .tables import table_evidence
    table_rows, table_links = table_evidence(prepared)
    preparation_hash = result.preparation_manifest().get('manifest_hash')
    from bisect import bisect_right
    row_ends = [row['end'] for row in table_rows]
    links_by_ref = {}
    for link in table_links:
        for ref in (link['from_ref'], link['to_ref']):
            links_by_ref.setdefault(ref, []).append(link)
    elements = {}
    from .preparation import element_model_provenance
    models = _models if _models is not None else element_model_provenance(result)
    picture_owners = _owners if _owners is not None else _picture_owners(result.document)
    for chunk_index, chunk in enumerate(result.chunks):
        for ref in chunk.source_refs:
            if ref not in elements:
                elements[ref] = ref.resolve(result.document)
        rows = []
        if chunk.start_index is not None:
            index = bisect_right(row_ends, chunk.start_index)
            while index < len(table_rows) and table_rows[index]['start'] < chunk.end_index:
                rows.append(table_rows[index])
                index += 1
        links = {link['from_ref']: link for ref in chunk.source_refs for link in links_by_ref.get(ref.cref, ())}
        body_refs = set(chunk.body_refs)
        if chunk.start_index is not None:
            body_pages = sorted({loc.page_no for block in prepared.blocks_in_span(chunk.start_index, chunk.end_index)
                                 if block.start < chunk.end_index and block.element.element_ref in body_refs
                                 for loc in block.body_locations(chunk.start_index, chunk.end_index)})
        else:
            body_pages = sorted({loc.page_no for ref in body_refs
                                 for loc in (*elements[ref].locations, *getattr(elements[ref], 'content_locations', ()))})
        pictures, body_pictures = {}, set()
        for ref in chunk.source_refs:
            if (picture := picture_owners[ref]) is not None:
                pictures[picture.element_ref] = picture
                if ref in body_refs:
                    body_pictures.add(picture.element_ref)
        record = {
            'format': 'pdfparse.index_record', 'schema_version': 4,
            'id': chunk.chunk_id, 'document_id': chunk.document_id,
            'document_name': result.document.pdfpath.name,
            'document_path': str(result.document.pdfpath),
            'model_provenance': {ref.cref: models[ref.cref] for ref in chunk.source_refs},
            'previous_chunk_id': result.chunks[chunk_index - 1].chunk_id if chunk_index else None,
            'next_chunk_id': result.chunks[chunk_index + 1].chunk_id if chunk_index + 1 < len(result.chunks) else None,
            'text': chunk.text, 'context': chunk.context, 'embedding_text': chunk.contextualized_text,
            'content_hash': hashlib.sha256(chunk.contextualized_text.encode()).hexdigest(),
            'document_text_hash': text_hash, 'transformation_id': chunk.transformation_id,
            'source_refs': [ref.cref for ref in chunk.source_refs],
            'body_refs': [ref.cref for ref in chunk.body_refs],
            'section_refs': [ref.cref for ref in chunk.section_refs],
            'section_path': [elements[ref].content_for_embedding for ref in chunk.section_refs],
            'pages': sorted({loc.page_no for loc in chunk.locations}),
            'body_pages': body_pages,
            'locations': [asdict(loc) for loc in chunk.locations],
            'content_types': sorted({elements[ref].element_type.value for ref in (chunk.body_refs or chunk.source_refs)}),
            'language': language,
            'text_origins': {ref.cref: origins.get(ref.cref, TextOrigin.UNKNOWN) for ref in chunk.source_refs},
            'span': {'start': chunk.start_index, 'end': chunk.end_index} if chunk.start_index is not None else None,
            'split_reason': chunk.split_reason,
            'token_count': chunk.token_count, 'tokenizer_id': chunk.tokenizer_id,
            'preparation_hash': preparation_hash,
            'table_rows': rows, 'table_links': list(links.values()),
            'assets_dir': str(result.document.assets_dir) if result.document.assets_dir is not None else None,
            'visual_assets': [_visual_asset(picture, role='body' if ref in body_pictures else 'context')
                              for ref, picture in pictures.items()],
            'page_contexts': [dict(pages[number]) for number in body_pages if number in pages],
        }
        record['record_hash'] = hashlib.sha256(_json(record).encode()).hexdigest()
        yield record


def export_jsonl(result: PdfParseResult, path: Path | str, *, language: str | None = None,
                 text_origins: Mapping[str, str] | None = None) -> None:
    _write_records(index_records(result, language=language, text_origins=text_origins), path)


def _write_records(records, path):
    target = Path(path)
    temporary = None
    try:
        with NamedTemporaryFile(mode='w', encoding='utf-8', newline='\n', dir=target.parent,
                                prefix=f'.{target.name}.', suffix='.tmp', delete=False) as stream:
            temporary = Path(stream.name)
            for record in records:
                stream.write(_json(record) + '\n')
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


@dataclass(frozen=True, slots=True)
class IndexSnapshot:
    """Immutable export snapshot; later edits to the document cannot stale it."""
    records_json: tuple[str, ...]
    quality: QualityReport
    manifest_json: str | None
    visual_records_json: tuple[str, ...] = ()
    model_provenance_json: str = '{}'

    def index_records(self):
        return (json.loads(value) for value in self.records_json)

    def export_jsonl(self, path: Path | str) -> None:
        _write_records(self.index_records(), path)

    def visual_records(self):
        return (json.loads(value) for value in self.visual_records_json)

    def export_visual_jsonl(self, path: Path | str) -> None:
        _write_records(self.visual_records(), path)


def prepare_index(result: PdfParseResult, *, language=None, text_origins=None) -> IndexSnapshot:
    from .preparation import element_model_provenance
    prepared = prepare_text(result.document)
    models, owners = element_model_provenance(result), _picture_owners(result.document)
    quality = assess_quality(result, _prepared=prepared)
    records = tuple(_json(record) for record in index_records(result, language=language,
                    text_origins=text_origins, _prepared=prepared, _models=models, _owners=owners))
    visuals = tuple(_json(record) for record in visual_records(result, _models=models, _owners=owners))
    return IndexSnapshot(records, quality, result.manifest_json, visuals, _json(models))


def migrate_index_record(record: Mapping) -> dict:
    """Explicit v1/v2/v3 → v4 migration; unavailable source metadata stays empty."""
    value = json.loads(_json(dict(record)))
    if value.get('format') != 'pdfparse.index_record' or value.get('schema_version') not in (1, 2, 3, 4):
        raise ValueError('Unsupported index record format/version')
    if value['schema_version'] == 1:
        value.update(schema_version=2, preparation_hash=None, table_rows=[], table_links=[])
    if value['schema_version'] == 2:
        value.update(schema_version=3, assets_dir=None, visual_assets=[], page_contexts=[])
    if value['schema_version'] == 3:
        value.update(schema_version=4, document_path=None, model_provenance={},
                     previous_chunk_id=None, next_chunk_id=None)
        value.pop('record_hash', None)
        value['record_hash'] = hashlib.sha256(_json(value).encode()).hexdigest()
    return value
