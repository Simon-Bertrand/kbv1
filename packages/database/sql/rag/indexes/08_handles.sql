-- Stable HTTP handles do not change upstream identities.
CREATE UNIQUE INDEX documents_handle_idx ON rag.documents (md5(document_id));
CREATE UNIQUE INDEX chunks_handle_idx ON rag.chunks (md5(chunk_id));
CREATE UNIQUE INDEX visuals_handle_idx ON rag.visuals (md5(visual_id));
