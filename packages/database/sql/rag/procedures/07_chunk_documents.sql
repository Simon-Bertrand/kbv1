-- Public chunk handles, batch lookup in the active publication or legacy index.
-- Return only business metadata and citeable identifiers, with no storage paths.
CREATE OR REPLACE FUNCTION rag.chunk_document_metadata(p_chunk_ids text[], p_profile text DEFAULT NULL)
RETURNS TABLE(chunk_id text, document_id text, original_name text, type_document_id bigint,
              type_document_label text, perimeter_code text, subperimeter_code text,
              pages jsonb, identifier text, designation text)
LANGUAGE plpgsql STABLE SECURITY INVOKER
SET search_path=pg_catalog,rag,pg_temp
AS $$
BEGIN
    IF p_chunk_ids IS NULL OR cardinality(p_chunk_ids) NOT BETWEEN 1 AND 100 OR EXISTS (
        SELECT 1 FROM unnest(p_chunk_ids) ids(id) WHERE id IS NULL OR id !~ '^c[a-f0-9]{32}$'
    ) THEN
        RAISE EXCEPTION 'Expected 1 to 100 public chunk IDs' USING ERRCODE='22023';
    END IF;
    RETURN QUERY
    WITH requested AS (
        SELECT id, min(position) AS position FROM unnest(p_chunk_ids) WITH ORDINALITY AS ids(id,position)
        GROUP BY id
    ), found AS (
        SELECT r.id,r.position,d.document_id,d.original_name,d.type_document_id,u.record,d.filepath
        FROM requested r
        JOIN rag.km_profile_units u ON u.profile=p_profile AND u.kind='chunk' AND md5(u.unit_id)=substr(r.id,2)
        JOIN rag.km_profile_documents d USING(profile,document_id)
        WHERE p_profile IS NOT NULL
        UNION ALL
        SELECT r.id,r.position,d.document_id,d.original_name,d.type_document_id,c.record,d.filepath
        FROM requested r
        JOIN rag.chunks c ON md5(c.chunk_id)=substr(r.id,2)
        JOIN rag.documents d USING(document_id)
        WHERE p_profile IS NULL
    )
    SELECT f.id,'d'||md5(f.document_id),f.original_name,f.type_document_id,t.label,
           t.perimeter_code,t.subperimeter_code,
           coalesce(nullif(f.record->'body_pages','[]'::jsonb), f.record->'pages','[]'::jsonb),
           v.info->>'identifiant',v.info->>'designation'
    FROM found f
    LEFT JOIN rag.km_document_types t ON t.type_document_id=f.type_document_id
    LEFT JOIN rag.km_files k ON k.document_id=f.document_id
    LEFT JOIN rag.km_versions v ON v.version_node_id=k.version_node_id
    WHERE lower(f.filepath) LIKE '%.pdf'
    ORDER BY f.position;
END;
$$;
