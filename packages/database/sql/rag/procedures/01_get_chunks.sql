-- Lecture en lot, dédupliquée et dans l'ordre demandé. IDs absents omis.
CREATE OR REPLACE FUNCTION rag.rag_get_chunks(p_ids text[])
RETURNS TABLE (
    chunk_id text, document_id text, filepath text, metadata jsonb, record jsonb
)
LANGUAGE plpgsql STABLE SECURITY INVOKER
SET search_path = pg_catalog, rag, pg_temp
AS $$
BEGIN
    IF p_ids IS NULL OR cardinality(p_ids) > 100 THEN
        RAISE EXCEPTION 'p_ids doit contenir au plus 100 identifiants (tableau non NULL)'
            USING ERRCODE = '22023';
    END IF;
    RETURN QUERY
    WITH requested AS (
        SELECT u.id, min(u.position) AS position
        FROM unnest(p_ids) WITH ORDINALITY AS u(id, position)
        GROUP BY u.id
    )
    SELECT c.chunk_id, c.document_id, d.filepath, d.metadata, c.record
    FROM requested r
    JOIN rag.chunks c ON c.chunk_id = r.id
    JOIN rag.documents d ON d.document_id = c.document_id
    ORDER BY r.position;
END;
$$;
