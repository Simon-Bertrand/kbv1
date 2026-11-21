"""Reconstruct a physical page from published records; never parses a PDF."""
from .store import unit

MAX_RECORDS = 1000
MAX_CHARS = 120000
MAX_IMAGES = 8


def pages(record):
    values = record.get('body_pages') or record.get('pages') or [
        loc.get('page_no', loc.get('page')) for loc in record.get('locations', []) if isinstance(loc, dict)]
    return sorted({n for n in values if type(n) is int and n > 0})


def reconstruct(document_id, number, rows, profile=None):
    if len(rows) > MAX_RECORDS:
        raise RuntimeError('Page trop volumineuse ; lire les passages individuellement.')
    chunks = [r for r in rows if r['kind'] == 'chunk']
    by_id = {r['id']: r for r in chunks}
    ordered, seen = [], set()
    def position(row):
        start = (row['record'].get('span') or {}).get('start')
        return (start if type(start) is int else float('inf'), row['id'])
    roots = sorted((r for r in chunks if r['record'].get('previous_chunk_id') not in by_id), key=position)
    for root in roots + sorted(chunks, key=position):
        row = root
        while row and row['id'] not in seen:
            seen.add(row['id']); ordered.append(row)
            row = by_id.get(row['record'].get('next_chunk_id'))
    texts, sources, visuals, warnings = [], [], [], []
    previous = None
    total = 0
    for row in ordered:
        record = row['record']; text = record.get('text') or ''
        if not isinstance(text, str): raise ValueError('Invalid source text')
        if previous:
            old = previous['record']; a = old.get('span') or {}; b = record.get('span') or {}
            if (old.get('document_text_hash') and old.get('document_text_hash') == record.get('document_text_hash')
                    and type(a.get('end')) is int and type(b.get('start')) is int):
                overlap = a['end'] - b['start']
                old_text = old.get('text') or ''
                if 0 < overlap <= min(len(old_text), len(text)) and old_text[-overlap:] == text[:overlap]:
                    text = text[overlap:]
        total += len(text)
        if total > MAX_CHARS: raise RuntimeError('Page trop volumineuse ; lire les passages individuellement.')
        if text: texts.append(text)
        item = unit(row)
        sources.append({'id': item['id'], 'pages': pages(record), 'context': record.get('context') or ''})
        if len(pages(record)) > 1 and 'cross_page_chunks' not in warnings: warnings.append('cross_page_chunks')
        previous = row
    visual_rows = sorted((r for r in rows if r['kind'] == 'visual'), key=position)
    for row in visual_rows[:MAX_IMAGES]:
        item = unit(row); record = row['record']
        visuals.append({'id': item['id'], 'pages': pages(record), 'caption': record.get('caption'),
            'ocr_text': record.get('ocr_text'), 'description': record.get('description'),
            'image_url': '/pages/'+item['id']+'/image' if item['image'] else None})
    if len(visual_rows) > MAX_IMAGES: warnings.append('visuals_truncated')
    result = {'document_id': document_id, 'page_number': number, 'profile': profile,
        'locator': {'type': 'physical_page', 'page': number, 'pages': [number],
            'included_pages': sorted({n for r in rows for n in pages(r['record'])})},
        'text': '\n\n'.join(texts), 'chunks': sources, 'visuals': visuals,
        'image_count': sum(bool(r['record'].get('asset_path')) for r in visual_rows), 'warnings': warnings,
        'reconstruction': True, 'semantics': 'Chunks associés à la page ; les chunks multi-pages peuvent inclure du texte voisin. Les assets sont des figures, pas nécessairement le rendu de la page.'}
    if sum(len(str(v)) for v in result.values()) > 500000:
        raise RuntimeError('Page trop volumineuse ; lire les passages individuellement.')
    return result
