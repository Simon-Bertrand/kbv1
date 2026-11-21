-- Rôle webui-user dédié à l'application WebUI (moindre privilège)
BEGIN;

DO $$
DECLARE membership record;
BEGIN
    IF NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = 'webui-user') THEN
        CREATE ROLE "webui-user" NOLOGIN;
    END IF;

    -- Supprimer toute appartenance résiduelle
    FOR membership IN
        SELECT parent.rolname AS parent_name, member.rolname AS member_name
        FROM pg_catalog.pg_auth_members m
        JOIN pg_catalog.pg_roles parent ON parent.oid = m.roleid
        JOIN pg_catalog.pg_roles member ON member.oid = m.member
        WHERE member.rolname = 'webui-user'
    LOOP
        EXECUTE format('REVOKE %I FROM %I', membership.parent_name, membership.member_name);
    END LOOP;

    EXECUTE format('REVOKE ALL ON DATABASE %I FROM "webui-user"', current_database());
    EXECUTE format('GRANT CONNECT ON DATABASE %I TO "webui-user"', current_database());
END;
$$;

ALTER ROLE "webui-user" NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS;
ALTER ROLE "webui-user" SET search_path = webui, pg_catalog;
ALTER ROLE "webui-user" SET statement_timeout = '15s';
ALTER ROLE "webui-user" SET idle_in_transaction_session_timeout = '30s';

-- Neutraliser l'accès aux autres schémas
REVOKE ALL ON SCHEMA public FROM "webui-user";
REVOKE ALL ON SCHEMA rag FROM "webui-user";

-- Accès exclusif au schéma webui
GRANT USAGE ON SCHEMA webui TO "webui-user";
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA webui TO "webui-user";
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA webui TO "webui-user";

ALTER DEFAULT PRIVILEGES IN SCHEMA webui GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO "webui-user";
ALTER DEFAULT PRIVILEGES IN SCHEMA webui GRANT USAGE, SELECT ON SEQUENCES TO "webui-user";

COMMIT;
