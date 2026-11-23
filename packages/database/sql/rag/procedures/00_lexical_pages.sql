-- Accept physical page numbers from current records and legacy locators.
CREATE OR REPLACE FUNCTION rag.has_physical_page(p_record jsonb)
RETURNS boolean LANGUAGE sql IMMUTABLE PARALLEL SAFE STRICT
SET search_path = pg_catalog
AS $$
    SELECT p_record @? '$.body_pages[*] ? (@.type() == "number" && @ > 0 && @.floor() == @)'
        OR p_record @? '$.pages[*] ? (@.type() == "number" && @ > 0 && @.floor() == @)'
        OR p_record @? '$.locations[*].page_no ? (@.type() == "number" && @ > 0 && @.floor() == @)'
        OR p_record @? '$.locations[*].page ? (@.type() == "number" && @ > 0 && @.floor() == @)';
$$;
