-- Separate KM API reads from indexing writes.
BEGIN;
DO $$ DECLARE membership record; BEGIN
 IF NOT EXISTS(SELECT FROM pg_roles WHERE rolname='km_reader') THEN CREATE ROLE km_reader NOLOGIN; END IF;
 IF NOT EXISTS(SELECT FROM pg_roles WHERE rolname='km_writer') THEN CREATE ROLE km_writer NOLOGIN; END IF;
 IF EXISTS(SELECT FROM pg_class WHERE relowner IN ('km_reader'::regrole,'km_writer'::regrole))
    OR EXISTS(SELECT FROM pg_namespace WHERE nspowner IN ('km_reader'::regrole,'km_writer'::regrole)) THEN
  RAISE EXCEPTION 'KM runtime roles must not own database objects';
 END IF;
 FOR membership IN SELECT parent.rolname AS parent_name,member.rolname AS member_name FROM pg_auth_members m
  JOIN pg_roles parent ON parent.oid=m.roleid JOIN pg_roles member ON member.oid=m.member
  WHERE member.rolname IN ('km_reader','km_writer')
 LOOP EXECUTE format('REVOKE %I FROM %I',membership.parent_name,membership.member_name); END LOOP;
 EXECUTE format('REVOKE ALL ON DATABASE %I FROM km_reader,km_writer',current_database());
 EXECUTE format('GRANT CONNECT ON DATABASE %I TO km_reader,km_writer',current_database());
END $$;
ALTER ROLE km_reader NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS;
ALTER ROLE km_writer NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS;
ALTER ROLE km_reader SET default_transaction_read_only=on;
ALTER ROLE km_reader SET statement_timeout='15s';
ALTER ROLE km_writer SET idle_in_transaction_session_timeout='60s';
GRANT USAGE ON SCHEMA rag TO km_reader,km_writer;
GRANT SELECT ON ALL TABLES IN SCHEMA rag TO km_reader;
GRANT SELECT,INSERT,UPDATE,DELETE ON ALL TABLES IN SCHEMA rag TO km_writer;
REVOKE INSERT,UPDATE,DELETE ON rag.km_document_types FROM km_writer;
GRANT USAGE,SELECT ON ALL SEQUENCES IN SCHEMA rag TO km_writer;
GRANT EXECUTE ON ALL ROUTINES IN SCHEMA rag TO km_writer;
DO $$ DECLARE routine record; BEGIN
 FOR routine IN SELECT p.oid::regprocedure AS signature FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
  WHERE n.nspname='rag' AND p.provolatile IN ('s','i')
 LOOP EXECUTE format('GRANT EXECUTE ON FUNCTION %s TO km_reader',routine.signature); END LOOP;
END $$;
ALTER DEFAULT PRIVILEGES IN SCHEMA rag GRANT SELECT ON TABLES TO km_reader;
ALTER DEFAULT PRIVILEGES IN SCHEMA rag GRANT SELECT,INSERT,UPDATE,DELETE ON TABLES TO km_writer;
ALTER DEFAULT PRIVILEGES IN SCHEMA rag GRANT USAGE,SELECT ON SEQUENCES TO km_writer;
ALTER DEFAULT PRIVILEGES IN SCHEMA rag GRANT EXECUTE ON ROUTINES TO km_writer;
COMMIT;
