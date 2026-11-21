-- NULL means no filter. An empty intersection must select no documents.
CREATE OR REPLACE FUNCTION rag.document_type_scope(
    p_type_document_ids bigint[], p_perimeter_code text, p_type_document_labels text[]
) RETURNS bigint[]
LANGUAGE plpgsql STABLE SECURITY INVOKER
SET search_path=pg_catalog,rag,pg_temp
AS $$
BEGIN
    IF p_type_document_ids IS NULL AND p_perimeter_code IS NULL AND p_type_document_labels IS NULL THEN
        RETURN NULL;
    END IF;
    IF cardinality(p_type_document_ids) > 100 OR cardinality(p_type_document_labels) > 100
       OR EXISTS (
           SELECT 1 FROM unnest(p_type_document_ids) ids(id)
           WHERE NOT EXISTS (SELECT 1 FROM rag.km_document_types t WHERE t.type_document_id = ids.id)
       ) OR EXISTS (
           SELECT 1 FROM unnest(p_type_document_labels) labels(label)
           WHERE NOT EXISTS (SELECT 1 FROM rag.km_document_types t WHERE lower(btrim(t.label)) = lower(btrim(labels.label)))
       ) OR (p_perimeter_code IS NOT NULL AND NOT EXISTS (
           SELECT 1 FROM rag.km_document_types t
           WHERE upper(btrim(t.perimeter_code)) = upper(btrim(p_perimeter_code)) OR lower(btrim(t.perimeter_label)) = lower(btrim(p_perimeter_code))
       )) THEN
        RAISE EXCEPTION 'Unknown document type or perimeter' USING ERRCODE='22023';
    END IF;
    RETURN ARRAY(
        SELECT t.type_document_id FROM rag.km_document_types t
        WHERE (p_type_document_ids IS NULL OR t.type_document_id = ANY(p_type_document_ids))
          AND (p_type_document_labels IS NULL OR EXISTS (
              SELECT 1 FROM unnest(p_type_document_labels) labels(label)
              WHERE lower(btrim(t.label)) = lower(btrim(labels.label))
          ))
          AND (p_perimeter_code IS NULL OR upper(btrim(t.perimeter_code)) = upper(btrim(p_perimeter_code))
               OR lower(btrim(t.perimeter_label)) = lower(btrim(p_perimeter_code)))
        ORDER BY t.type_document_id
    );
END;
$$;
