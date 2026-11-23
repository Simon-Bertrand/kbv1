-- Independent publications preserve the text and provenance belonging to each encoder.
 CREATE TABLE IF NOT EXISTS rag.km_profile_documents (
  profile text NOT NULL REFERENCES rag.embedding_profiles(profile),
  document_id text NOT NULL, filepath text NOT NULL,
  metadata jsonb NOT NULL, published_at timestamptz NOT NULL DEFAULT now(),
  original_name text,
  type_document_id bigint,
  PRIMARY KEY(profile,document_id)
 );
CREATE TABLE IF NOT EXISTS rag.km_profile_units (
 profile text NOT NULL, unit_id text NOT NULL, document_id text NOT NULL,
 kind text NOT NULL CHECK(kind IN ('chunk','visual')),
 record jsonb NOT NULL,
 PRIMARY KEY(profile,unit_id),
 FOREIGN KEY(profile,document_id) REFERENCES rag.km_profile_documents ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS rag.km_text_embeddings (
 profile text NOT NULL, chunk_id text NOT NULL, input_hash text NOT NULL,
 embedding rag.vector(1024) NOT NULL, indexed_at timestamptz NOT NULL DEFAULT now(),
 PRIMARY KEY(profile,chunk_id),
 FOREIGN KEY(profile,chunk_id) REFERENCES rag.km_profile_units(profile,unit_id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS rag.km_profile_labels (
 profile text PRIMARY KEY REFERENCES rag.embedding_profiles(profile),
 label text NOT NULL CHECK(length(btrim(label)) BETWEEN 1 AND 80)
);
CREATE TABLE IF NOT EXISTS rag.km_embedding_settings (
 singleton boolean PRIMARY KEY DEFAULT true CHECK(singleton),
 active_text_profile text REFERENCES rag.embedding_profiles(profile)
);

INSERT INTO rag.km_embedding_settings(singleton) VALUES (true);

-- Checkpoints persist before document publication, hence no foreign key to chunks.
-- Never store source text here; identity/hash/profile bind each saved vector.
CREATE TABLE IF NOT EXISTS rag.km_chunk_indexing (
    document_id text NOT NULL,
    profile text REFERENCES rag.embedding_profiles(profile),
    chunk_id text NOT NULL,
    input_hash text NOT NULL,
    encoder_fingerprint text NOT NULL,
    status text NOT NULL DEFAULT 'pending' CHECK (
        status IN ('pending','encoding','encoded','indexed','failed','interrupted')),
    embedding rag.vector(1024),
    attempts integer NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    last_error text,
    is_current boolean NOT NULL DEFAULT true,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    indexed_at timestamptz,
    PRIMARY KEY (document_id,chunk_id,input_hash,encoder_fingerprint),
    CHECK (status NOT IN ('encoded','indexed') OR embedding IS NOT NULL)
);

CREATE TABLE IF NOT EXISTS rag.km_embedding_deletions (
 id text PRIMARY KEY,
 profile text NOT NULL REFERENCES rag.embedding_profiles(profile),
 scope text NOT NULL CHECK(scope IN ('profile','document','chunk')),
 target text NOT NULL,
 encoder_fingerprint text NOT NULL,
 counts jsonb NOT NULL,
 created_at timestamptz NOT NULL DEFAULT now()
);
