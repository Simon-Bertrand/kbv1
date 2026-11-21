CREATE INDEX chunks_bm25_idx ON rag.chunks
    USING bm25 ((coalesce(record->>'embedding_text', record->>'text', '')))
    WITH (text_config='simple');
