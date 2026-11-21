CREATE OR REPLACE FUNCTION rag.rag_search_profile(
 p_profile text,p_query text,p_text_query rag.vector DEFAULT NULL,p_mode text DEFAULT 'hybrid',
 p_top_k integer DEFAULT 10,p_candidates integer DEFAULT 100,p_metadata jsonb DEFAULT NULL,
    p_type_document_ids bigint[] DEFAULT NULL,
    p_perimeter_code text DEFAULT NULL,
    p_type_document_labels text[] DEFAULT NULL,
    p_date_filters jsonb DEFAULT NULL
)
RETURNS TABLE(kind text,id text,score double precision,lexical_rank bigint,semantic_rank bigint,
 document_id text,filepath text,metadata jsonb,record jsonb)
LANGUAGE plpgsql STABLE SECURITY INVOKER
SET search_path=pg_catalog,rag,pg_temp
SET hnsw.iterative_scan=strict_order
SET hnsw.ef_search=100
AS $$
DECLARE
 v_query tsquery;
 v_type_ids bigint[];
    v_date_ids text[];
 v_exact_limit CONSTANT integer := 2048;
 v_exact_scope boolean := false;
BEGIN
 IF p_query IS NULL OR length(btrim(p_query))=0 OR length(p_query)>4000
  OR p_mode IS NULL OR p_mode NOT IN ('lexical','semantic','hybrid')
  OR p_top_k IS NULL OR p_top_k NOT BETWEEN 1 AND 100
  OR p_candidates IS NULL OR p_candidates NOT BETWEEN p_top_k AND 1000
  OR (p_metadata IS NOT NULL AND jsonb_typeof(p_metadata)<>'object') THEN
  RAISE EXCEPTION 'Invalid profile search arguments' USING ERRCODE='22023';
 END IF;
 IF p_mode<>'lexical' AND (p_text_query IS NULL OR rag.vector_dims(p_text_query)<>1024 OR rag.vector_norm(p_text_query)=0) THEN
  RAISE EXCEPTION 'Expected text vector 1024' USING ERRCODE='22023';
 END IF;
 IF p_mode<>'semantic' THEN
  v_query := websearch_to_tsquery('simple',p_query);
 END IF;
 v_type_ids := rag.document_type_scope(p_type_document_ids, p_perimeter_code, p_type_document_labels);
    v_date_ids := rag.date_document_scope(p_date_filters);
 IF (v_type_ids IS NOT NULL OR v_date_ids IS NOT NULL) AND p_mode <> 'lexical' THEN
  -- Bound the probe so broad filters keep the iterative HNSW path.
  SELECT count(*) <= v_exact_limit INTO v_exact_scope
  FROM (
   SELECT 1
   FROM rag.km_profile_documents d JOIN rag.km_profile_units u USING(profile,document_id)
   JOIN rag.km_text_embeddings e ON e.profile=u.profile AND e.chunk_id=u.unit_id
   WHERE d.profile=p_profile AND (v_type_ids IS NULL OR d.type_document_id=ANY(v_type_ids))
   AND (v_date_ids IS NULL OR d.document_id=ANY(v_date_ids))
    AND (p_metadata IS NULL OR d.metadata @> p_metadata)
   LIMIT v_exact_limit + 1
  ) candidates;
 END IF;
 RETURN QUERY
 WITH lexical AS MATERIALIZED (
  SELECT u.unit_id,ts_rank_cd(u.lexical,v_query) AS relevance
  FROM rag.km_profile_units u JOIN rag.km_profile_documents d USING(profile,document_id)
  WHERE u.profile=p_profile AND u.kind IN ('chunk','markdown') AND p_mode<>'semantic'
   AND u.lexical @@ v_query
   AND (p_metadata IS NULL OR d.metadata @> p_metadata)
          AND (v_type_ids IS NULL OR d.type_document_id = ANY(v_type_ids))
          AND (v_date_ids IS NULL OR d.document_id = ANY(v_date_ids))
  ORDER BY relevance DESC,u.unit_id LIMIT p_candidates
 ), exact_vectors AS MATERIALIZED (
  SELECT e.chunk_id,e.embedding
  FROM rag.km_profile_documents d JOIN rag.km_profile_units u USING(profile,document_id)
  JOIN rag.km_text_embeddings e ON e.profile=u.profile AND e.chunk_id=u.unit_id
  WHERE v_exact_scope AND d.profile=p_profile AND (v_type_ids IS NULL OR d.type_document_id=ANY(v_type_ids))
   AND (v_date_ids IS NULL OR d.document_id=ANY(v_date_ids))
   AND (p_metadata IS NULL OR d.metadata @> p_metadata)
 ), semantic AS MATERIALIZED (
  (SELECT e.chunk_id,e.embedding OPERATOR(rag.<=>) p_text_query AS distance
   FROM exact_vectors e ORDER BY distance,e.chunk_id LIMIT p_candidates)
  UNION ALL
  (
  SELECT e.chunk_id,e.embedding OPERATOR(rag.<=>) p_text_query AS distance
  FROM rag.km_text_embeddings e JOIN rag.km_profile_units u ON u.profile=e.profile AND u.unit_id=e.chunk_id
  JOIN rag.km_profile_documents d ON d.profile=u.profile AND d.document_id=u.document_id
  WHERE NOT v_exact_scope AND e.profile=p_profile AND p_mode<>'lexical' AND (p_metadata IS NULL OR d.metadata @> p_metadata)
          AND (v_type_ids IS NULL OR d.type_document_id = ANY(v_type_ids))
          AND (v_date_ids IS NULL OR d.document_id = ANY(v_date_ids))
  ORDER BY e.embedding OPERATOR(rag.<=>) p_text_query,e.chunk_id LIMIT p_candidates)
 ), ranks AS (
  SELECT l.unit_id,row_number() OVER(ORDER BY l.relevance DESC,l.unit_id) AS lr,NULL::bigint AS sr FROM lexical l
  UNION ALL
  SELECT s.chunk_id,NULL::bigint,row_number() OVER(ORDER BY s.distance,s.chunk_id) FROM semantic s
 ), fused AS (
  SELECT r.unit_id,min(r.lr) AS lr,min(r.sr) AS sr,
   coalesce(1.0::double precision/(60+min(r.lr)),0)+coalesce(1.0::double precision/(60+min(r.sr)),0) AS score
  FROM ranks r GROUP BY r.unit_id
 ), winners AS (SELECT f.* FROM fused f ORDER BY f.score DESC,f.unit_id LIMIT p_top_k)
 SELECT u.kind,u.unit_id,w.score,w.lr,w.sr,u.document_id,d.filepath,d.metadata,u.record
 FROM winners w JOIN rag.km_profile_units u ON u.profile=p_profile AND u.unit_id=w.unit_id
 JOIN rag.km_profile_documents d ON d.profile=u.profile AND d.document_id=u.document_id
 ORDER BY w.score DESC,u.unit_id;
END;
$$;
