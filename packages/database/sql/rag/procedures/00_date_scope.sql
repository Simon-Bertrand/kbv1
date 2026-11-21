-- NULL means unrestricted. A supplied bound excludes missing source dates.
CREATE OR REPLACE FUNCTION rag.date_document_scope(filters jsonb) RETURNS text[]
LANGUAGE plpgsql STABLE STRICT SET search_path=pg_catalog,rag
SET plan_cache_mode=force_custom_plan AS $$
DECLARE
    field text;
    bounds jsonb;
    bound text;
    value jsonb;
    lower_date date;
    upper_date date;
BEGIN
    IF jsonb_typeof(filters) <> 'object' OR filters = '{}'::jsonb THEN
        RAISE EXCEPTION 'Invalid date filters' USING ERRCODE='22023';
    END IF;
    FOR field,bounds IN SELECT * FROM jsonb_each(filters) LOOP
        IF field NOT IN ('edition','approval','repeal') OR jsonb_typeof(bounds) <> 'object'
            OR bounds = '{}'::jsonb THEN
            RAISE EXCEPTION 'Invalid date range' USING ERRCODE='22023';
        END IF;
        FOR bound,value IN SELECT * FROM jsonb_each(bounds) LOOP
            IF bound NOT IN ('from','to') OR jsonb_typeof(value) <> 'string'
                OR rag.km_version_date(value) IS NULL THEN
                RAISE EXCEPTION 'Expected ISO calendar date YYYY-MM-DD' USING ERRCODE='22023';
            END IF;
        END LOOP;
        lower_date := rag.km_version_date(bounds->'from');
        upper_date := rag.km_version_date(bounds->'to');
        IF lower_date > upper_date THEN
            RAISE EXCEPTION 'Invalid date range order' USING ERRCODE='22023';
        END IF;
    END LOOP;
    RETURN ARRAY(
        SELECT f.document_id FROM rag.km_files f JOIN rag.km_versions v USING(version_node_id)
        WHERE (filters#>'{edition,from}' IS NULL OR v.edition_date >= rag.km_version_date(filters#>'{edition,from}'))
          AND (filters#>'{edition,to}' IS NULL OR v.edition_date <= rag.km_version_date(filters#>'{edition,to}'))
          AND (filters#>'{approval,from}' IS NULL OR v.approval_date >= rag.km_version_date(filters#>'{approval,from}'))
          AND (filters#>'{approval,to}' IS NULL OR v.approval_date <= rag.km_version_date(filters#>'{approval,to}'))
          AND (filters#>'{repeal,from}' IS NULL OR v.repeal_date >= rag.km_version_date(filters#>'{repeal,from}'))
          AND (filters#>'{repeal,to}' IS NULL OR v.repeal_date <= rag.km_version_date(filters#>'{repeal,to}'))
    );
END;
$$;
