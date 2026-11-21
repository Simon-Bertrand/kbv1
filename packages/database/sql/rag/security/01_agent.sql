-- À exécuter comme administrateur, sur la base applicative dédiée.
-- Aucun mot de passe ici : init-rag-agent le lit depuis le conteneur PostgreSQL.
BEGIN;

DO $$
DECLARE membership record;
BEGIN
    IF NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = 'rag_executor') THEN
        CREATE ROLE rag_executor NOLOGIN;
    END IF;
    IF NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = 'agent') THEN
        CREATE ROLE agent NOLOGIN;
    END IF;
    -- Ne pas réutiliser silencieusement un ancien compte propriétaire d'objets.
    IF EXISTS (SELECT FROM pg_catalog.pg_class WHERE relowner IN ('agent'::regrole, 'rag_executor'::regrole))
       OR EXISTS (SELECT FROM pg_catalog.pg_namespace WHERE nspowner IN ('agent'::regrole, 'rag_executor'::regrole))
       OR EXISTS (SELECT FROM pg_catalog.pg_proc WHERE proowner = 'agent'::regrole)
       OR EXISTS (SELECT FROM pg_catalog.pg_database WHERE datdba IN ('agent'::regrole, 'rag_executor'::regrole)) THEN
        RAISE EXCEPTION 'agent/rag_executor ne doivent posséder ni base, ni schéma, ni table';
    END IF;
    FOR membership IN
        SELECT parent.rolname AS parent_name, member.rolname AS member_name
        FROM pg_catalog.pg_auth_members m
        JOIN pg_catalog.pg_roles parent ON parent.oid = m.roleid
        JOIN pg_catalog.pg_roles member ON member.oid = m.member
        WHERE member.rolname IN ('agent', 'rag_executor')
    LOOP
        EXECUTE format('REVOKE %I FROM %I', membership.parent_name, membership.member_name);
    END LOOP;
    EXECUTE format('REVOKE ALL ON DATABASE %I FROM agent, rag_executor', current_database());
    -- PUBLIC transmet TEMPORARY à tous les utilisateurs par défaut.
    EXECUTE format('REVOKE CREATE, TEMPORARY ON DATABASE %I FROM PUBLIC', current_database());
    EXECUTE format('GRANT CONNECT ON DATABASE %I TO agent', current_database());
END;
$$;

ALTER ROLE agent NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS;
ALTER ROLE rag_executor NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS;
ALTER ROLE agent SET search_path = pg_catalog, rag;
ALTER ROLE agent SET default_transaction_read_only = on;
ALTER ROLE agent SET statement_timeout = '15s';
ALTER ROLE agent SET idle_in_transaction_session_timeout = '30s';

REVOKE ALL ON SCHEMA public FROM PUBLIC, agent, rag_executor;
REVOKE ALL ON SCHEMA rag FROM PUBLIC, agent, rag_executor;
GRANT USAGE ON SCHEMA rag TO agent, rag_executor;
REVOKE ALL ON ALL TABLES IN SCHEMA rag FROM PUBLIC, agent, rag_executor;
REVOKE ALL ON ALL SEQUENCES IN SCHEMA rag FROM PUBLIC, agent, rag_executor;
REVOKE ALL ON ALL ROUTINES IN SCHEMA rag FROM PUBLIC, agent, rag_executor;

-- Les futures fonctions de l'administrateur ne deviennent pas publiques.
-- Le réglage doit être global : un REVOKE par schéma ne retire pas le défaut global.
ALTER DEFAULT PRIVILEGES REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC;
ALTER DEFAULT PRIVILEGES FOR ROLE rag_executor REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC;

GRANT SELECT ON rag.documents, rag.chunks, rag.visuals, rag.chunk_visuals,
    rag.text_embeddings, rag.visual_embeddings, rag.km_document_types, rag.km_files, rag.km_versions TO rag_executor;
GRANT EXECUTE ON FUNCTION rag.vector_dims(rag.vector), rag.vector_norm(rag.vector),
    rag.cosine_distance(rag.vector, rag.vector),
    rag.date_document_scope(jsonb), rag.km_version_date(jsonb),
    rag.document_type_scope(bigint[],text,text[]) TO rag_executor;

-- Un propriétaire technique en lecture seule, jamais postgres/superutilisateur.
ALTER FUNCTION rag.rag_get_chunks(text[]) OWNER TO rag_executor;
ALTER FUNCTION rag.rag_get_visuals(text[]) OWNER TO rag_executor;
ALTER FUNCTION rag.rag_search(rag.vector, rag.vector, integer, integer, text[], jsonb, bigint[], text, text[], jsonb) OWNER TO rag_executor;
ALTER FUNCTION rag.rag_get_chunks(text[]) SECURITY DEFINER;
ALTER FUNCTION rag.rag_get_visuals(text[]) SECURITY DEFINER;
ALTER FUNCTION rag.rag_search(rag.vector, rag.vector, integer, integer, text[], jsonb, bigint[], text, text[], jsonb) SECURITY DEFINER;
GRANT EXECUTE ON FUNCTION rag.rag_get_chunks(text[]), rag.rag_get_visuals(text[]),
    rag.rag_search(rag.vector, rag.vector, integer, integer, text[], jsonb, bigint[], text, text[], jsonb) TO agent;

COMMIT;
