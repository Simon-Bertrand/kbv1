-- PostgreSQL + pgvector >= 0.8.0 (parcours HNSW itératif avec filtres).
-- Les dimensions des paramètres rag.vector ne sont pas contraintes par PostgreSQL
-- dans les signatures de fonctions : les contrôler explicitement ici.
CREATE OR REPLACE FUNCTION rag.rag_search(
    p_text_query rag.vector DEFAULT NULL,
    p_visual_query rag.vector DEFAULT NULL,
    p_top_k integer DEFAULT 10,
    p_candidates integer DEFAULT 100,
    p_document_ids text[] DEFAULT NULL,
    p_metadata jsonb DEFAULT NULL,
    p_type_document_ids bigint[] DEFAULT NULL,
    p_perimeter_code text DEFAULT NULL,
    p_type_document_labels text[] DEFAULT NULL,
    p_date_filters jsonb DEFAULT NULL
)
RETURNS TABLE (
    kind text, id text, score double precision,
    text_rank bigint, visual_rank bigint,
    document_id text, filepath text, metadata jsonb, evidence jsonb
)
LANGUAGE plpgsql STABLE SECURITY INVOKER
SET search_path = pg_catalog, rag, pg_temp
SET plan_cache_mode = force_custom_plan
SET hnsw.iterative_scan = strict_order
SET hnsw.ef_search = 100
AS $$
DECLARE v_type_ids bigint[];
    v_date_ids text[];
BEGIN
    IF p_text_query IS NULL AND p_visual_query IS NULL THEN
        RAISE EXCEPTION 'Fournir au moins un vecteur de requête' USING ERRCODE = '22023';
    END IF;
    IF p_text_query IS NOT NULL AND
       (rag.vector_dims(p_text_query) <> 1024 OR rag.vector_norm(p_text_query) = 0) THEN
        RAISE EXCEPTION 'Le vecteur texte doit avoir 1024 dimensions et une norme non nulle'
            USING ERRCODE = '22023';
    END IF;
    IF p_visual_query IS NOT NULL AND
       (rag.vector_dims(p_visual_query) <> 768 OR rag.vector_norm(p_visual_query) = 0) THEN
        RAISE EXCEPTION 'Le vecteur visuel doit avoir 768 dimensions et une norme non nulle'
            USING ERRCODE = '22023';
    END IF;
    IF p_top_k IS NULL OR p_top_k NOT BETWEEN 1 AND 100 OR
       p_candidates IS NULL OR p_candidates NOT BETWEEN p_top_k AND 1000 THEN
        RAISE EXCEPTION 'Exiger 1 <= top_k <= 100 et top_k <= candidates <= 1000'
            USING ERRCODE = '22023';
    END IF;
    IF p_metadata IS NOT NULL AND jsonb_typeof(p_metadata) <> 'object' THEN
        RAISE EXCEPTION 'p_metadata doit être un objet JSON' USING ERRCODE = '22023';
    END IF;

    v_type_ids := rag.document_type_scope(p_type_document_ids, p_perimeter_code, p_type_document_labels);
    v_date_ids := rag.date_document_scope(p_date_filters);
    RETURN QUERY
    WITH text_nearest AS MATERIALIZED (
        SELECT e.chunk_id, e.embedding OPERATOR(rag.<=>) p_text_query AS distance
        FROM rag.text_embeddings e
        WHERE p_text_query IS NOT NULL
          AND (p_document_ids IS NULL OR EXISTS (
              SELECT 1 FROM rag.chunks c
              WHERE c.chunk_id = e.chunk_id AND c.document_id = ANY(p_document_ids)
          ))
          AND ((p_metadata IS NULL AND v_type_ids IS NULL AND v_date_ids IS NULL) OR EXISTS (
              SELECT 1 FROM rag.chunks c JOIN rag.documents d USING (document_id)
              WHERE c.chunk_id = e.chunk_id AND (p_metadata IS NULL OR d.metadata @> p_metadata)
                AND (v_type_ids IS NULL OR d.type_document_id = ANY(v_type_ids))
          AND (v_date_ids IS NULL OR d.document_id = ANY(v_date_ids))
          ))
        ORDER BY e.embedding OPERATOR(rag.<=>) p_text_query
        LIMIT p_candidates
    ), visual_nearest AS MATERIALIZED (
        SELECT e.visual_id, e.embedding OPERATOR(rag.<=>) p_visual_query AS distance
        FROM rag.visual_embeddings e
        WHERE p_visual_query IS NOT NULL
          AND (p_document_ids IS NULL OR EXISTS (
              SELECT 1 FROM rag.visuals v
              WHERE v.visual_id = e.visual_id AND v.document_id = ANY(p_document_ids)
          ))
          AND ((p_metadata IS NULL AND v_type_ids IS NULL AND v_date_ids IS NULL) OR EXISTS (
              SELECT 1 FROM rag.visuals v JOIN rag.documents d USING (document_id)
              WHERE v.visual_id = e.visual_id AND (p_metadata IS NULL OR d.metadata @> p_metadata)
                AND (v_type_ids IS NULL OR d.type_document_id = ANY(v_type_ids))
          AND (v_date_ids IS NULL OR d.document_id = ANY(v_date_ids))
          ))
        ORDER BY e.embedding OPERATOR(rag.<=>) p_visual_query
        LIMIT p_candidates
    ), text_ranked AS (
        SELECT n.chunk_id, row_number() OVER (ORDER BY n.distance, n.chunk_id) AS rank
        FROM text_nearest n
    ), visual_ranked AS (
        SELECT n.visual_id, row_number() OVER (ORDER BY n.distance, n.visual_id) AS rank
        FROM visual_nearest n
    ), visual_targets AS (
        -- Une image liée contribue au chunk ; une image autonome reste un résultat.
        -- Vérifier le document évite de suivre un lien inter-document mal ingéré.
        SELECT CASE WHEN linked.chunk_id IS NULL THEN 'visual' ELSE 'chunk' END AS kind,
               coalesce(linked.chunk_id, n.visual_id) AS id, n.rank
        FROM visual_ranked n
        JOIN rag.visuals v ON v.visual_id = n.visual_id
        LEFT JOIN LATERAL (
            SELECT c.chunk_id
            FROM rag.chunk_visuals cv JOIN rag.chunks c ON c.chunk_id = cv.chunk_id
            WHERE cv.visual_id = n.visual_id AND c.document_id = v.document_id
        ) linked ON true
    ), contributions AS (
        SELECT 'chunk'::text AS kind, t.chunk_id AS id,
               t.rank AS text_rank, NULL::bigint AS visual_rank
        FROM text_ranked t
        UNION ALL
        SELECT v.kind, v.id, NULL::bigint, min(v.rank)
        FROM visual_targets v
        GROUP BY v.kind, v.id
    ), fused AS (
        -- RRF k=60 : une seule contribution par modalité et résultat.
        SELECT c.kind, c.id, min(c.text_rank) AS text_rank,
               min(c.visual_rank) AS visual_rank,
               coalesce(1.0::double precision / (60 + min(c.text_rank)), 0) +
               coalesce(1.0::double precision / (60 + min(c.visual_rank)), 0) AS score
        FROM contributions c
        GROUP BY c.kind, c.id
    ), winners AS MATERIALIZED (
        SELECT f.* FROM fused f
        ORDER BY f.score DESC, f.kind, f.id
        LIMIT p_top_k
    ), hydrated AS (
        SELECT w.*, c.document_id,
               jsonb_build_object(
                   'text', c.record->'text', 'context', c.record->'context',
                   'pages', c.record->'pages', 'locations', c.record->'locations',
                   'source_refs', c.record->'source_refs',
                   'section_path', c.record->'section_path',
                   'visual_assets', c.record->'visual_assets',
                   'matched_visual_ids', coalesce((
                       SELECT jsonb_agg(n.visual_id ORDER BY n.rank)
                       FROM visual_ranked n
                       JOIN rag.chunk_visuals cv ON cv.visual_id = n.visual_id
                       JOIN rag.visuals v ON v.visual_id = n.visual_id
                       WHERE cv.chunk_id = c.chunk_id AND v.document_id = c.document_id
                   ), '[]'::jsonb),
                   'assets_dir', c.record->'assets_dir',
                   'previous_chunk_id', c.record->'previous_chunk_id',
                   'next_chunk_id', c.record->'next_chunk_id'
               ) AS evidence
        FROM winners w JOIN rag.chunks c ON w.kind = 'chunk' AND c.chunk_id = w.id
        UNION ALL
        SELECT w.*, v.document_id,
               jsonb_build_object(
                   'caption', v.record->'caption', 'description', v.record->'description',
                   'ocr_text', v.record->'ocr_text', 'locations', v.record->'locations',
                   'ref', v.record->'ref', 'asset_path', v.record->'asset_path',
                   'assets_dir', v.record->'assets_dir'
               ) AS evidence
        FROM winners w JOIN rag.visuals v ON w.kind = 'visual' AND v.visual_id = w.id
    )
    SELECT h.kind, h.id, h.score, h.text_rank, h.visual_rank,
           h.document_id, d.filepath, d.metadata, h.evidence
    FROM hydrated h JOIN rag.documents d ON d.document_id = h.document_id
    ORDER BY h.score DESC, h.kind, h.id;
END;
$$;
