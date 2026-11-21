"""KM consumer validation for authoritative cache records; no producer imports."""
def validate(bundle):
    doc = bundle['document_id']
    if not isinstance(doc, str) or not doc or not isinstance(bundle['filepath'], str):
        raise ValueError('Missing PdfParse document identity')
    ids = set()
    for kind, version, records in [('index', 4, bundle['chunks']), ('visual', 1, bundle['visuals'])]:
        for record in records:
            if (not isinstance(record, dict) or record.get('format') != 'pdfparse.' + kind + '_record'
                    or type(record.get('schema_version')) is not int or record['schema_version'] != version
                    or record.get('document_id') != doc or not isinstance(record.get('id'), str) or not record['id']
                    or record['id'] in ids):
                raise ValueError('Invalid, mixed or duplicate PdfParse records')
            ids.add(record['id'])
            if record.get('document_path') != bundle['filepath']:
                raise ValueError('Mixed PdfParse source paths')
            if kind == 'index' and (not isinstance(record.get('embedding_text'), str) or not record['embedding_text'].strip()):
                raise ValueError('Missing authoritative embedding_text')
    chunks = {r['id'] for r in bundle['chunks']}
    if any(not set(r.get('chunk_ids', [])) <= chunks for r in bundle['visuals']):
        raise ValueError('PdfParse visual references an unknown chunk')
    if not bundle['chunks'] and not bundle['visuals']:
        raise ValueError('Cache PdfParse vide : aucun chunk ni visuel exploitable ; document non publié')

