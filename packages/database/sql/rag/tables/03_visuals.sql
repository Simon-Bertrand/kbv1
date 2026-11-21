-- Une figure reste indexable même sans chunk textuel associé.
CREATE TABLE rag.visuals (
    visual_id text PRIMARY KEY,
    document_id text NOT NULL REFERENCES rag.documents (document_id) ON DELETE CASCADE,
    record jsonb NOT NULL,

    CHECK (record->>'id' IS NOT DISTINCT FROM visual_id),
    CHECK (record->>'document_id' IS NOT DISTINCT FROM document_id),
    CHECK (record->>'format' IS NOT DISTINCT FROM 'pdfparse.visual_record'),
    CHECK (record->>'schema_version' IS NOT DISTINCT FROM '1')
);
