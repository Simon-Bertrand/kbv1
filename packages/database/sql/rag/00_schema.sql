-- Espace applicatif unique, indépendant du search_path du client.
CREATE SCHEMA IF NOT EXISTS rag;
REVOKE CREATE ON SCHEMA rag FROM PUBLIC;
