-- Un document par contenu PDF, avec un chemin canonique modifiable.
CREATE TABLE IF NOT EXISTS rag.documents (
    document_id text PRIMARY KEY,
    filepath text NOT NULL,
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    indexed_at timestamptz NOT NULL DEFAULT now(),
    original_name text,
    type_document_id bigint
);
