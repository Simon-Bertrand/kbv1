CREATE INDEX IF NOT EXISTS km_profile_units_document_idx ON rag.km_profile_units(profile,document_id);
CREATE INDEX IF NOT EXISTS km_profile_units_handle_idx ON rag.km_profile_units(profile,md5(unit_id));
CREATE INDEX IF NOT EXISTS km_profile_documents_handle_idx ON rag.km_profile_documents(profile,md5(document_id));
CREATE INDEX IF NOT EXISTS km_profile_documents_type_idx ON rag.km_profile_documents(profile,type_document_id,document_id);
CREATE INDEX IF NOT EXISTS km_profile_units_bm25_idx ON rag.km_profile_units
    USING bm25 ((coalesce(record->>'text', ''))) WITH (text_config='simple');
CREATE INDEX IF NOT EXISTS km_text_vectors_hnsw_idx ON rag.km_text_embeddings USING hnsw(embedding rag.vector_cosine_ops);
CREATE INDEX IF NOT EXISTS km_chunk_indexing_current_idx
    ON rag.km_chunk_indexing(document_id,status,chunk_id) WHERE is_current;
CREATE INDEX IF NOT EXISTS km_checkpoints_profile_idx ON rag.km_chunk_indexing(profile,document_id,status) WHERE is_current;
CREATE INDEX IF NOT EXISTS km_embedding_deletions_profile_idx ON rag.km_embedding_deletions(profile,created_at DESC);
CREATE INDEX IF NOT EXISTS km_corpus_syncs_root_idx ON rag.km_corpus_syncs(corpus_root,started_at DESC);
CREATE INDEX IF NOT EXISTS km_corpus_files_path_idx ON rag.km_corpus_files(corpus_root,path);
CREATE INDEX IF NOT EXISTS km_corpus_files_list_idx ON rag.km_corpus_files(corpus_root,lower(path),path) WHERE present;
CREATE INDEX IF NOT EXISTS km_cache_chunks_lookup_idx ON rag.km_cache_chunks(corpus_root,document_id,chunk_id,input_hash);
CREATE INDEX IF NOT EXISTS km_chunk_job_files_queue_idx ON rag.km_chunk_job_files(job_id,status,path,file_id);
