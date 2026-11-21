CREATE INDEX IF NOT EXISTS km_files_version_idx ON rag.km_files(version_node_id);
CREATE INDEX IF NOT EXISTS km_versions_parent_idx ON rag.km_versions(parent_node_id);
CREATE INDEX IF NOT EXISTS km_linked_parent_idx ON rag.km_linked_documents(linked_parent_node_id);
CREATE INDEX IF NOT EXISTS km_units_document_idx ON rag.km_search_units(document_id);
CREATE INDEX IF NOT EXISTS km_units_handle_idx ON rag.km_search_units((md5(unit_id)));
CREATE INDEX IF NOT EXISTS km_units_bm25_idx ON rag.km_search_units USING bm25(body)
WITH (text_config='simple');

CREATE INDEX IF NOT EXISTS km_versions_edition_date_idx ON rag.km_versions (edition_date) WHERE edition_date IS NOT NULL;
CREATE INDEX IF NOT EXISTS km_versions_approval_date_idx ON rag.km_versions (approval_date) WHERE approval_date IS NOT NULL;
CREATE INDEX IF NOT EXISTS km_versions_repeal_date_idx ON rag.km_versions (repeal_date) WHERE repeal_date IS NOT NULL;
