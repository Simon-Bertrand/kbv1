-- One lexical index covers both contextual chunks and exact Markdown spans.
CREATE OR REPLACE FUNCTION rag.rag_search_km(
    p_query text,p_text_query rag.vector DEFAULT NULL,p_mode text DEFAULT 'hybrid',
    p_top_k integer DEFAULT 10,p_candidates integer DEFAULT 100,
    p_document_ids text[] DEFAULT NULL,p_metadata jsonb DEFAULT NULL
)
RETURNS TABLE(kind text,id text,score double precision,lexical_rank bigint,semantic_rank bigint,
              document_id text,filepath text,metadata jsonb,record jsonb)
LANGUAGE plpgsql STABLE SECURITY INVOKER
SET search_path=pg_catalog,rag,pg_temp
SET plan_cache_mode=force_custom_plan
SET hnsw.iterative_scan=strict_order
SET hnsw.ef_search=100
AS $$
BEGIN
    IF p_query IS NULL OR length(btrim(p_query))=0 OR length(p_query)>4000
       OR p_mode IS NULL OR p_mode NOT IN ('lexical','semantic','hybrid')
       OR p_top_k IS NULL OR p_top_k NOT BETWEEN 1 AND 100
       OR p_candidates IS NULL OR p_candidates NOT BETWEEN p_top_k AND 1000
       OR (p_metadata IS NOT NULL AND jsonb_typeof(p_metadata)<>'object') THEN
        RAISE EXCEPTION 'Invalid KM search arguments' USING ERRCODE='22023';
    END IF;
    IF p_mode<>'lexical' AND (p_text_query IS NULL OR rag.vector_dims(p_text_query)<>1024
       OR rag.vector_norm(p_text_query)=0) THEN
        RAISE EXCEPTION 'Expected text vector 1024' USING ERRCODE='22023';
    END IF;
    RETURN QUERY
    WITH lexical AS MATERIALIZED (
        SELECT u.unit_id,u.body OPERATOR(rag.<@>) rag.to_bm25query(p_query,'rag.km_units_bm25_idx') AS distance
        FROM rag.km_search_units u JOIN rag.documents d USING(document_id)
        WHERE p_mode<>'semantic'
          AND (p_document_ids IS NULL OR u.document_id=ANY(p_document_ids))
          AND (p_metadata IS NULL OR d.metadata @> p_metadata)
        ORDER BY u.body OPERATOR(rag.<@>) rag.to_bm25query(p_query,'rag.km_units_bm25_idx')
        LIMIT p_candidates
    ), semantic AS MATERIALIZED (
        SELECT e.chunk_id,e.embedding OPERATOR(rag.<=>) p_text_query AS distance
        FROM rag.text_embeddings e JOIN rag.km_search_units u ON u.unit_id=e.chunk_id
        JOIN rag.documents d ON d.document_id=u.document_id
        WHERE p_mode<>'lexical'
          AND (p_document_ids IS NULL OR u.document_id=ANY(p_document_ids))
          AND (p_metadata IS NULL OR d.metadata @> p_metadata)
        ORDER BY e.embedding OPERATOR(rag.<=>) p_text_query LIMIT p_candidates
    ), ranks AS (
        SELECT l.unit_id,row_number() OVER(ORDER BY l.distance,l.unit_id) AS lr,NULL::bigint AS sr
        FROM lexical l WHERE l.distance<0
        UNION ALL
        SELECT s.chunk_id,NULL::bigint,row_number() OVER(ORDER BY s.distance,s.chunk_id) FROM semantic s
    ), fused AS (
        SELECT r.unit_id,min(r.lr) AS lr,min(r.sr) AS sr,
            coalesce(1.0::double precision/(60+min(r.lr)),0)+coalesce(1.0::double precision/(60+min(r.sr)),0) AS score
        FROM ranks r GROUP BY r.unit_id
    ), winners AS MATERIALIZED (
        SELECT f.* FROM fused f ORDER BY f.score DESC,f.unit_id LIMIT p_top_k
    )
    SELECT u.kind,u.unit_id,w.score,w.lr,w.sr,u.document_id,d.filepath,d.metadata,u.record
    FROM winners w JOIN rag.km_search_units u USING(unit_id) JOIN rag.documents d ON d.document_id=u.document_id
    ORDER BY w.score DESC,u.unit_id;
END;
$$;
