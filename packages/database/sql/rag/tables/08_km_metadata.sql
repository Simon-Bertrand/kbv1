-- Alfresco dates are epoch milliseconds. ISO dates are accepted as well.
CREATE OR REPLACE FUNCTION rag.km_version_date(value jsonb) RETURNS date
LANGUAGE plpgsql IMMUTABLE STRICT SET search_path=pg_catalog AS $$
DECLARE raw text := value #>> '{}';
BEGIN
    IF jsonb_typeof(value) = 'number' AND raw ~ '^-?[0-9]{1,15}$' THEN
        RETURN (to_timestamp(raw::double precision / 1000) AT TIME ZONE 'UTC')::date;
    ELSIF jsonb_typeof(value) = 'string' AND raw ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}$' THEN
        RETURN make_date(substr(raw,1,4)::int,substr(raw,6,2)::int,substr(raw,9,2)::int);
    END IF;
    RETURN NULL;
EXCEPTION WHEN datetime_field_overflow THEN RETURN NULL;
END;
$$;

-- KM identities. Source values remain JSON, including nulls and booleans.
CREATE TABLE IF NOT EXISTS rag.km_nodes (
    parent_node_id text PRIMARY KEY
);
CREATE TABLE IF NOT EXISTS rag.km_versions (
    version_node_id text PRIMARY KEY,
    parent_node_id text NOT NULL REFERENCES rag.km_nodes,
    version_label text NOT NULL,
    version_folder text NOT NULL UNIQUE,
    info jsonb,
    version_record jsonb,
    edition_date date GENERATED ALWAYS AS (rag.km_version_date(version_record->'editionDate')) STORED,
    approval_date date GENERATED ALWAYS AS (rag.km_version_date(version_record->'approbationDate')) STORED,
    repeal_date date GENERATED ALWAYS AS (rag.km_version_date(version_record->'abrogationDate')) STORED,
    linked_documents jsonb,
    warnings jsonb NOT NULL DEFAULT '[]'::jsonb,
    updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS rag.km_files (
    file_id text PRIMARY KEY,
    version_node_id text NOT NULL REFERENCES rag.km_versions,
    document_id text NOT NULL UNIQUE REFERENCES rag.documents ON DELETE CASCADE,
    relative_path text NOT NULL UNIQUE,
    filename text NOT NULL,
    original_path text,
    csv_row jsonb NOT NULL DEFAULT '{}'::jsonb
);
CREATE TABLE IF NOT EXISTS rag.km_linked_documents (
    version_node_id text NOT NULL REFERENCES rag.km_versions ON DELETE CASCADE,
    ordinal integer NOT NULL CHECK (ordinal >= 0),
    linked_parent_node_id text,
    raw jsonb NOT NULL,
    PRIMARY KEY (version_node_id, ordinal)
);
CREATE TABLE IF NOT EXISTS rag.km_search_units (
    unit_id text PRIMARY KEY,
    document_id text NOT NULL REFERENCES rag.documents ON DELETE CASCADE,
    kind text NOT NULL CHECK (kind = 'chunk'),
    body text NOT NULL,
    record jsonb NOT NULL,
    CHECK (record->>'id' IS NOT DISTINCT FROM unit_id)
);
