-- Exact encoder identity is published by the upstream indexer.
CREATE TABLE rag.embedding_profiles (
    profile text PRIMARY KEY,
    modality text NOT NULL CHECK (modality IN ('text', 'visual')),
    encoder_fingerprint text NOT NULL,
    dimension integer NOT NULL CHECK (dimension > 0),
    configuration jsonb NOT NULL CHECK (jsonb_typeof(configuration) = 'object')
);
