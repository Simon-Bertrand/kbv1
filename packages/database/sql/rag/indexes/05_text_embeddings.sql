CREATE INDEX text_embeddings_hnsw_idx
    ON rag.text_embeddings USING hnsw (embedding rag.vector_cosine_ops);
