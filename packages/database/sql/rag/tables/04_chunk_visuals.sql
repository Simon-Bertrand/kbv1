-- Relations plusieurs-à-plusieurs issues de visual_record.chunk_ids.
CREATE TABLE rag.chunk_visuals (
    chunk_id text NOT NULL REFERENCES rag.chunks (chunk_id) ON DELETE CASCADE,
    visual_id text NOT NULL REFERENCES rag.visuals (visual_id) ON DELETE CASCADE,
    PRIMARY KEY (chunk_id, visual_id)
);
