-- Dedicated account for the internal text retrieval executed by the reranker.
BEGIN;
DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname='km_reranker') THEN
        CREATE ROLE km_reranker NOLOGIN;
    END IF;
    IF EXISTS (SELECT FROM pg_auth_members WHERE member='km_reranker'::regrole)
        OR EXISTS (SELECT FROM pg_class WHERE relowner='km_reranker'::regrole)
        OR EXISTS (SELECT FROM pg_namespace WHERE nspowner='km_reranker'::regrole) THEN
        RAISE EXCEPTION 'Reranker role must not inherit privileges or own database objects';
    END IF;
    EXECUTE format('REVOKE ALL ON DATABASE %I FROM km_reranker',current_database());
    EXECUTE format('GRANT CONNECT ON DATABASE %I TO km_reranker',current_database());
END;
$$;
ALTER ROLE km_reranker NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS;
ALTER ROLE km_reranker SET default_transaction_read_only=on;
ALTER ROLE km_reranker SET statement_timeout='10s';
REVOKE ALL ON SCHEMA rag FROM km_reranker;
REVOKE ALL ON ALL TABLES IN SCHEMA rag FROM km_reranker;
REVOKE ALL ON ALL ROUTINES IN SCHEMA rag FROM km_reranker;
GRANT USAGE ON SCHEMA rag TO km_reranker;
GRANT SELECT ON rag.km_embedding_settings, rag.embedding_profiles,
    rag.km_profile_documents, rag.km_profile_units, rag.km_text_embeddings,
    rag.km_files, rag.km_versions, rag.documents, rag.km_document_types TO km_reranker;
GRANT EXECUTE ON FUNCTION rag.rag_search_profile(text,text,rag.vector,text,integer,integer,jsonb,bigint[],text,text[],jsonb),
    rag.date_document_scope(jsonb), rag.km_version_date(jsonb),
    rag.document_type_scope(bigint[],text,text[]),
    rag.vector_dims(rag.vector), rag.vector_norm(rag.vector),
    rag.cosine_distance(rag.vector, rag.vector) TO km_reranker;
COMMIT;
