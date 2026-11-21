-- Les trois extensions doivent être installées sur le serveur PostgreSQL.
-- pg_textsearch doit figurer dans shared_preload_libraries avant le démarrage.

-- IF NOT EXISTS ne déplace pas une extension déjà installée dans public.
-- Refuser une installation ambiguë ; réinitialiser la base dans ce cas.
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM pg_catalog.pg_extension e
        JOIN pg_catalog.pg_namespace n ON n.oid = e.extnamespace
        WHERE e.extname IN ('vector', 'vectorscale', 'pg_textsearch') AND n.nspname <> 'rag'
    ) THEN
        RAISE EXCEPTION 'Les extensions doivent être dans rag. Réinitialiser la base avec les scripts courants.';
    END IF;
END;
$$;

CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA rag;
CREATE EXTENSION IF NOT EXISTS vectorscale WITH SCHEMA rag;
CREATE EXTENSION IF NOT EXISTS pg_textsearch WITH SCHEMA rag;
