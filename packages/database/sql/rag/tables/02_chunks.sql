-- Record PdfParse complet : texte, contexte, références, géométries et versions.
CREATE TABLE rag.chunks (
    chunk_id text PRIMARY KEY,
    document_id text NOT NULL REFERENCES rag.documents (document_id) ON DELETE CASCADE,
    record jsonb NOT NULL,

    CHECK (record->>'id' IS NOT DISTINCT FROM chunk_id),
    CHECK (record->>'document_id' IS NOT DISTINCT FROM document_id),
    CHECK (record->>'format' IS NOT DISTINCT FROM 'pdfparse.index_record'),
    CHECK (record->>'schema_version' IS NOT DISTINCT FROM '4')
);
