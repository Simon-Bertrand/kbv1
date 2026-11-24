DO $$
BEGIN
    IF EXISTS (SELECT FROM pg_catalog.pg_hba_file_rules WHERE error IS NOT NULL) THEN
        RAISE EXCEPTION 'Règles HBA invalides';
    END IF;
    IF NOT pg_catalog.pg_reload_conf() THEN
        RAISE EXCEPTION 'Rechargement HBA refusé';
    END IF;
END;
$$;
