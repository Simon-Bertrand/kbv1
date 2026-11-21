CREATE INDEX visual_embeddings_hnsw_idx
    ON rag.visual_embeddings USING hnsw (embedding rag.vector_cosine_ops);
