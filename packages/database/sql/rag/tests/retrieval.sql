-- Exécuter sur une base de test ; aucune fixture n'est conservée.
BEGIN;
SET LOCAL search_path = pg_catalog;

INSERT INTO rag.documents (document_id, filepath, metadata, type_document_id) VALUES
    ('test-rag-a', '/current/a.pdf', '{"category":"manual"}', 1000),
    ('test-rag-b', '/current/b.pdf', '{"category":"other"}', 1032);

INSERT INTO rag.chunks (chunk_id, document_id, record)
SELECT id, doc, jsonb_build_object(
    'id', id, 'document_id', doc, 'format', 'pdfparse.index_record',
    'schema_version', 4, 'text', 'Texte Unicode : été 中文', 'pages', jsonb_build_array(1, 2),
    'locations', jsonb_build_array(jsonb_build_object('page_no', 1)),
    'previous_chunk_id', NULL, 'next_chunk_id', NULL
)
FROM (VALUES ('test-rag-c1', 'test-rag-a'), ('test-rag-c2', 'test-rag-a'),
             ('test-rag-c3', 'test-rag-b')) AS fixture(id, doc);

INSERT INTO rag.visuals (visual_id, document_id, record)
SELECT id, doc, jsonb_build_object(
    'id', id, 'document_id', doc, 'format', 'pdfparse.visual_record',
    'schema_version', 1, 'caption', 'Figure témoin', 'asset_path', '/crops/' || id || '.png'
)
FROM (VALUES ('test-rag-v1', 'test-rag-a'), ('test-rag-v2', 'test-rag-a'),
             ('test-rag-v3', 'test-rag-b')) AS fixture(id, doc);

INSERT INTO rag.chunk_visuals VALUES
    ('test-rag-c1', 'test-rag-v1'), ('test-rag-c2', 'test-rag-v1'),
    -- Lien inter-document volontairement erroné : ne doit pas être suivi.
    ('test-rag-c3', 'test-rag-v2');

INSERT INTO rag.text_embeddings (chunk_id, input_hash, embedding)
SELECT id, 'fixture', (ARRAY[x, y] || array_fill(0::real, ARRAY[1022]))::rag.vector
FROM (VALUES ('test-rag-c1', 1::real, 0::real), ('test-rag-c2', 0.8, 0.2),
             ('test-rag-c3', 0, 1)) AS fixture(id, x, y);

INSERT INTO rag.visual_embeddings (visual_id, input_hash, embedding)
SELECT id, 'fixture', (ARRAY[x, y] || array_fill(0::real, ARRAY[766]))::rag.vector
FROM (VALUES ('test-rag-v1', 1::real, 0::real), ('test-rag-v2', 0.8, 0.2),
             ('test-rag-v3', 0, 1)) AS fixture(id, x, y);

INSERT INTO rag.embedding_profiles VALUES ('test-rag-profile', 'text', 'fixture', 1024, '{}');
INSERT INTO rag.km_profile_documents (profile, document_id, filepath, metadata, type_document_id)
SELECT 'test-rag-profile', document_id, filepath, metadata, type_document_id FROM rag.documents
WHERE document_id IN ('test-rag-a', 'test-rag-b');
INSERT INTO rag.km_profile_units (profile, unit_id, document_id, kind, record)
SELECT 'test-rag-profile', chunk_id, document_id, 'chunk', record FROM rag.chunks
WHERE document_id IN ('test-rag-a', 'test-rag-b');
INSERT INTO rag.km_text_embeddings (profile, chunk_id, input_hash, embedding)
SELECT 'test-rag-profile', e.chunk_id, e.input_hash, e.embedding
FROM rag.text_embeddings e JOIN rag.km_profile_units u ON u.unit_id=e.chunk_id
WHERE u.profile='test-rag-profile';

INSERT INTO rag.km_nodes VALUES ('test-rag-node');
INSERT INTO rag.km_versions (version_node_id, parent_node_id, version_label, version_folder, version_record)
VALUES ('test-rag-ver', 'test-rag-node', '1', 'test-rag-version',
        '{"editionDate":1735689600000,"approbationDate":"2025-02-01","abrogationDate":null}');
INSERT INTO rag.km_files (file_id, version_node_id, document_id, relative_path, filename)
VALUES ('test-rag-file', 'test-rag-ver', 'test-rag-b', 'test-rag-version/b.pdf', 'b.pdf');

DO $$
DECLARE
    tq rag.vector := (ARRAY[1::real] || array_fill(0::real, ARRAY[1023]))::rag.vector;
    vq rag.vector := (ARRAY[1::real] || array_fill(0::real, ARRAY[767]))::rag.vector;
    row_data record;
    total integer;
    ordered_ids text[];
    old_scan text := current_setting('hnsw.iterative_scan');
BEGIN
    SELECT * INTO row_data FROM rag.rag_search(p_text_query => tq,
        p_document_ids => ARRAY['test-rag-a'], p_top_k => 1);
    IF row_data.id IS DISTINCT FROM 'test-rag-c1' OR
       row_data.filepath IS DISTINCT FROM '/current/a.pdf' OR
       row_data.evidence->>'text' IS DISTINCT FROM 'Texte Unicode : été 中文' OR
       row_data.evidence->'pages' IS DISTINCT FROM '[1,2]'::jsonb THEN
        RAISE EXCEPTION 'Échec classement texte ou citations : %', row_data;
    END IF;

    SELECT count(*) INTO total FROM rag.rag_search(p_text_query => tq,
        p_document_ids => ARRAY['test-rag-a'], p_metadata => '{"category":"other"}');
    IF total <> 0 THEN RAISE EXCEPTION 'Les filtres doivent se cumuler'; END IF;

    SELECT * INTO row_data FROM rag.rag_search(p_text_query => tq, p_top_k => 1,
        p_metadata => '{"category":"other"}', p_document_ids => ARRAY['test-rag-a', 'test-rag-b']);
    IF row_data.id IS DISTINCT FROM 'test-rag-c3' THEN
        RAISE EXCEPTION 'Le filtre doit être appliqué avant LIMIT';
    END IF;

    SELECT * INTO row_data FROM rag.rag_search_profile('test-rag-profile', 'été', tq, 'hybrid',
        p_top_k => 1, p_perimeter_code => 'RTA',
        p_date_filters => '{"edition":{"from":"2025-01-01","to":"2025-01-01"},"approval":{"to":"2025-02-01"}}');
    IF row_data.id IS DISTINCT FROM 'test-rag-c3' OR row_data.semantic_rank IS DISTINCT FROM 1::bigint THEN
        RAISE EXCEPTION 'Le classement doit filtrer par dates et périmètre avant LIMIT';
    END IF;
    SELECT count(*) INTO total FROM rag.rag_search(p_text_query => tq,
        p_date_filters => '{"repeal":{"to":"2025-12-31"}}');
    IF total <> 0 THEN RAISE EXCEPTION 'Le filtre doit exclure les dates manquantes'; END IF;
    BEGIN
        PERFORM rag.date_document_scope('{"edition":{"from":"2025-02-30"}}');
        RAISE EXCEPTION 'Date invalide acceptée';
    EXCEPTION WHEN invalid_parameter_value THEN NULL;
    END;

    SELECT * INTO row_data FROM rag.rag_search_profile('test-rag-profile', 'été', tq, 'hybrid',
        p_top_k => 1, p_perimeter_code => 'RTA', p_type_document_ids => ARRAY[1032]::bigint[]);
    IF row_data.id IS DISTINCT FROM 'test-rag-c3' OR row_data.semantic_rank IS DISTINCT FROM 1::bigint THEN
        RAISE EXCEPTION 'Le classement vectoriel exact doit respecter le périmètre et le type';
    END IF;
    SELECT count(*) INTO total FROM rag.rag_search_profile('test-rag-profile', 'été', tq, 'semantic',
        p_perimeter_code => 'RTA', p_type_document_ids => ARRAY[1000]::bigint[]);
    IF total <> 0 THEN RAISE EXCEPTION 'Intersection vide attendue en recherche vectorielle'; END IF;

    SELECT * INTO row_data FROM rag.rag_search(p_text_query => tq, p_top_k => 1, p_perimeter_code => 'RTA');
    IF row_data.document_id IS DISTINCT FROM 'test-rag-b' THEN
        RAISE EXCEPTION 'Le périmètre doit être appliqué avant LIMIT';
    END IF;
    SELECT count(*) INTO total FROM rag.rag_search(p_text_query => tq, p_perimeter_code => 'RTA',
        p_type_document_ids => ARRAY[1000]::bigint[]);
    IF total <> 0 THEN RAISE EXCEPTION 'Le périmètre et les types doivent se cumuler'; END IF;
    SELECT * INTO row_data FROM rag.rag_search(p_text_query => tq, p_top_k => 1,
        p_type_document_labels => ARRAY['Tableau de composition illustré']);
    IF row_data.document_id IS DISTINCT FROM 'test-rag-a' THEN
        RAISE EXCEPTION 'Le filtre par libellé doit cibler le type attendu';
    END IF;
    BEGIN
        PERFORM * FROM rag.rag_search(p_text_query => tq, p_type_document_ids => ARRAY[999999]::bigint[]);
        RAISE EXCEPTION 'Le référentiel doit refuser un type inconnu';
    EXCEPTION WHEN invalid_parameter_value THEN NULL;
    END;

    SELECT count(*) INTO total FROM rag.rag_search(p_text_query => tq, p_document_ids => ARRAY[]::text[]);
    IF total <> 0 THEN RAISE EXCEPTION 'Le filtre vide doit retourner zéro ligne'; END IF;

    SELECT count(*) INTO total FROM rag.rag_search(p_visual_query => vq,
        p_document_ids => ARRAY['test-rag-a']);
    IF total <> 3 THEN RAISE EXCEPTION 'Attendu : deux chunks liés et une figure autonome'; END IF;
    SELECT * INTO row_data FROM rag.rag_search(p_visual_query => vq,
        p_document_ids => ARRAY['test-rag-a']) WHERE id = 'test-rag-v2';
    IF row_data.kind IS DISTINCT FROM 'visual' OR
       row_data.evidence->>'asset_path' IS DISTINCT FROM '/crops/test-rag-v2.png' THEN
        RAISE EXCEPTION 'Figure autonome perdue ou lien inter-document suivi';
    END IF;

    SELECT * INTO row_data FROM rag.rag_search(tq, vq, p_document_ids => ARRAY['test-rag-a'])
        WHERE id = 'test-rag-c1';
    IF row_data.text_rank IS DISTINCT FROM 1::bigint OR
       row_data.visual_rank IS DISTINCT FROM 1::bigint OR
       abs(row_data.score - 2.0 / 61) > 1e-12 OR
       row_data.evidence->'matched_visual_ids' IS DISTINCT FROM '["test-rag-v1"]'::jsonb THEN
        RAISE EXCEPTION 'Échec fusion RRF ou provenance visuelle : %', row_data;
    END IF;

    SELECT array_agg(c.chunk_id) INTO ordered_ids
    FROM rag.rag_get_chunks(ARRAY['test-rag-c2', 'absent', 'test-rag-c1', 'test-rag-c2']) c;
    IF ordered_ids IS DISTINCT FROM ARRAY['test-rag-c2', 'test-rag-c1'] THEN
        RAISE EXCEPTION 'Lecture en lot : ordre/déduplication incorrects';
    END IF;
    SELECT count(*) INTO total FROM rag.rag_get_chunks(ARRAY[]::text[]);
    IF total <> 0 THEN RAISE EXCEPTION 'Lecture vide incorrecte'; END IF;
    SELECT * INTO row_data FROM rag.rag_get_visuals(ARRAY['test-rag-v2']);
    IF row_data.record->>'schema_version' IS DISTINCT FROM '1' THEN
        RAISE EXCEPTION 'Record visuel complet absent';
    END IF;
    IF current_setting('hnsw.iterative_scan') IS DISTINCT FROM old_scan THEN
        RAISE EXCEPTION 'Fuite du réglage HNSW hors de la fonction';
    END IF;

    BEGIN
        PERFORM * FROM rag.rag_search();
        RAISE EXCEPTION 'Absence de vecteur acceptée';
    EXCEPTION WHEN invalid_parameter_value THEN NULL; END;
    BEGIN
        PERFORM * FROM rag.rag_search(p_text_query => '[1,2]'::rag.vector);
        RAISE EXCEPTION 'Dimension invalide acceptée';
    EXCEPTION WHEN invalid_parameter_value THEN NULL; END;
    BEGIN
        PERFORM * FROM rag.rag_search(p_text_query => array_fill(0::real, ARRAY[1024])::rag.vector);
        RAISE EXCEPTION 'Vecteur nul accepté';
    EXCEPTION WHEN invalid_parameter_value THEN NULL; END;
    BEGIN
        PERFORM * FROM rag.rag_search(tq, p_top_k => NULL);
        RAISE EXCEPTION 'LIMIT NULL accepté';
    EXCEPTION WHEN invalid_parameter_value THEN NULL; END;
    BEGIN
        PERFORM * FROM rag.rag_search(tq, p_candidates => 2);
        RAISE EXCEPTION 'Budget candidats invalide accepté';
    EXCEPTION WHEN invalid_parameter_value THEN NULL; END;
    BEGIN
        PERFORM * FROM rag.rag_search(tq, p_metadata => '[]');
        RAISE EXCEPTION 'Filtre JSON invalide accepté';
    EXCEPTION WHEN invalid_parameter_value THEN NULL; END;
    BEGIN
        PERFORM * FROM rag.rag_get_chunks(array_fill('x'::text, ARRAY[101]));
        RAISE EXCEPTION 'Lot non borné accepté';
    EXCEPTION WHEN invalid_parameter_value THEN NULL; END;
END;
$$;

ROLLBACK;

-- Compatibilité avec une transaction de lecture seule, même sur corpus vide.
BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY;
SELECT count(*) FROM rag.rag_search(
    p_text_query => (ARRAY[1::real] || array_fill(0::real, ARRAY[1023]))::rag.vector,
    p_document_ids => ARRAY['test-rag-a']
);
COMMIT;
