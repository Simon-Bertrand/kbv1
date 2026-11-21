-- Lancer comme administrateur après security/01_agent.sql. Aucune fixture conservée.
BEGIN;

INSERT INTO rag.documents(document_id, filepath) VALUES ('test-agent-doc', '/agent/source.pdf');
INSERT INTO rag.chunks(chunk_id, document_id, record) VALUES (
    'test-agent-chunk', 'test-agent-doc',
    '{"id":"test-agent-chunk","document_id":"test-agent-doc","format":"pdfparse.index_record","schema_version":4,"text":"Lecture autorisée"}'
);
INSERT INTO rag.visuals(visual_id, document_id, record) VALUES (
    'test-agent-visual', 'test-agent-doc',
    '{"id":"test-agent-visual","document_id":"test-agent-doc","format":"pdfparse.visual_record","schema_version":1}'
);
INSERT INTO rag.text_embeddings(chunk_id, input_hash, embedding)
VALUES ('test-agent-chunk', 'fixture', (ARRAY[1::real] || array_fill(0::real, ARRAY[1023]))::rag.vector);

CREATE FUNCTION rag.test_agent_forbidden() RETURNS integer LANGUAGE sql AS 'SELECT 1';

SET LOCAL ROLE agent;
DO $$
DECLARE result_count integer; forbidden text;
BEGIN
    SELECT count(*) INTO result_count FROM rag.rag_get_chunks(ARRAY['test-agent-chunk']);
    IF result_count <> 1 THEN RAISE EXCEPTION 'Lecture des chunks refusée à agent'; END IF;
    SELECT count(*) INTO result_count FROM rag.rag_get_visuals(ARRAY['test-agent-visual']);
    IF result_count <> 1 THEN RAISE EXCEPTION 'Lecture des figures refusée à agent'; END IF;
    SELECT count(*) INTO result_count FROM rag.rag_search(
        p_text_query => ('[' || array_to_string(ARRAY[1::real] || array_fill(0::real, ARRAY[1023]), ',') || ']')::rag.vector,
        p_document_ids => ARRAY['test-agent-doc']);
    IF result_count <> 1 THEN RAISE EXCEPTION 'Recherche refusée à agent'; END IF;
    IF current_user <> 'agent' THEN RAISE EXCEPTION 'Le rôle definer fuit dans la session'; END IF;

    FOREACH forbidden IN ARRAY ARRAY[
        'SELECT * FROM rag.documents',
        'SELECT * FROM rag.chunks',
        'SELECT * FROM rag.visuals',
        'SELECT * FROM rag.chunk_visuals',
        'SELECT * FROM rag.text_embeddings',
        'SELECT * FROM rag.visual_embeddings',
        'INSERT INTO rag.documents VALUES (''forbidden'', ''forbidden'', ''{}'', now())',
        'UPDATE rag.documents SET filepath = ''forbidden'' WHERE false',
        'DELETE FROM rag.documents WHERE false',
        'TRUNCATE rag.documents CASCADE',
        'CREATE TABLE rag.forbidden(id integer)',
        'CREATE TABLE public.forbidden(id integer)',
        'CREATE TEMP TABLE forbidden(id integer)',
        'CREATE SCHEMA forbidden',
        'CREATE FUNCTION public.forbidden() RETURNS integer LANGUAGE sql AS ''SELECT 1''',
        'SELECT rag.test_agent_forbidden()',
        'SELECT rag.vector_dims(''[1]''::rag.vector)',
        'SELECT * FROM pg_catalog.pg_authid'
    ] LOOP
        BEGIN
            EXECUTE forbidden;
            RAISE EXCEPTION 'Permission indue pour agent : %', forbidden;
        EXCEPTION WHEN insufficient_privilege THEN
            NULL;
        END;
    END LOOP;
END;
$$;
RESET ROLE;

-- SET ROLE dépend de session_user, pas de current_user : un administrateur
-- ayant fait SET ROLE agent pourrait encore changer de rôle. L'absence
-- d'escalade est donc testée sur une vraie connexion agent dans test_agent.py.

DO $$
BEGIN
    IF EXISTS (
        SELECT FROM pg_catalog.pg_roles
        WHERE rolname IN ('agent', 'rag_executor')
          AND (rolsuper OR rolcreatedb OR rolcreaterole OR rolreplication OR rolbypassrls)
    ) OR EXISTS (
        SELECT FROM pg_catalog.pg_auth_members WHERE member IN ('agent'::regrole, 'rag_executor'::regrole)
    ) THEN
        RAISE EXCEPTION 'Attributs ou appartenances de rôle trop permissifs';
    END IF;
    IF (SELECT count(*) FROM pg_catalog.pg_proc p
        JOIN pg_catalog.pg_namespace n ON n.oid = p.pronamespace
        WHERE n.nspname = 'rag' AND has_function_privilege('agent', p.oid, 'EXECUTE')) <> 3 THEN
        RAISE EXCEPTION 'agent ne doit exécuter que les trois fonctions applicatives autorisées';
    END IF;
END;
$$;
ROLLBACK;
