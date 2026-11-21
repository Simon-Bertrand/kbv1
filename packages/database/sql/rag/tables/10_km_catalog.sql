CREATE TABLE IF NOT EXISTS rag.km_corpus_syncs (
 id text PRIMARY KEY,
 corpus_root text NOT NULL,
 status text NOT NULL CHECK (status IN ('running','succeeded','failed','interrupted')),
 started_at timestamptz NOT NULL DEFAULT now(),
 finished_at timestamptz,
 directories integer NOT NULL DEFAULT 0,
 pdf_count integer NOT NULL DEFAULT 0,
 cache_count integer NOT NULL DEFAULT 0,
 chunks_count bigint NOT NULL DEFAULT 0,
 valid_caches integer NOT NULL DEFAULT 0,
 invalid_caches integer NOT NULL DEFAULT 0,
 error text
);
CREATE TABLE IF NOT EXISTS rag.km_corpus_files (
 corpus_root text NOT NULL,
 id text NOT NULL,
 path text NOT NULL,
 summary jsonb NOT NULL,
 metadata jsonb,
 present boolean NOT NULL DEFAULT true,
 last_seen_sync text NOT NULL,
 observed_at timestamptz NOT NULL DEFAULT now(),
 PRIMARY KEY(corpus_root,id),
 UNIQUE(corpus_root,path)
);
CREATE TABLE IF NOT EXISTS rag.km_corpus_caches (
 corpus_root text NOT NULL,
 document_id text NOT NULL,
 cache_key text NOT NULL,
 path text NOT NULL,
 validation_state text NOT NULL DEFAULT 'pending',
 validation_error text,
 cache_digest text,
 km_document_id text,
 chunk_count integer NOT NULL DEFAULT 0,
 verified_at timestamptz,
 state text NOT NULL CHECK(state IN ('observed','invalid_manifest','unreadable')),
 manifest jsonb,
 error text,
 present boolean NOT NULL DEFAULT true,
 last_seen_sync text NOT NULL,
 observed_at timestamptz NOT NULL DEFAULT now(),
 PRIMARY KEY(corpus_root,document_id,cache_key)
);
COMMENT ON TABLE rag.km_corpus_caches IS 'Filesystem observations only; observed is not an integrity validation of cache exports.';

CREATE TABLE IF NOT EXISTS rag.km_cache_chunks (
 corpus_root text NOT NULL,
 file_id text NOT NULL,
 cache_key text NOT NULL,
 chunk_id text NOT NULL,
 document_id text NOT NULL,
 source_chunk_id text NOT NULL,
 cache_digest text NOT NULL,
 input_hash text NOT NULL,
 ordinal integer NOT NULL,
 pages jsonb NOT NULL,
 record jsonb NOT NULL,
 PRIMARY KEY(corpus_root,file_id,cache_key,chunk_id),
 FOREIGN KEY(corpus_root,file_id,cache_key)
   REFERENCES rag.km_corpus_caches(corpus_root,document_id,cache_key)
);
CREATE TABLE IF NOT EXISTS rag.km_profile_cache_choices (
 corpus_root text NOT NULL,
 file_id text NOT NULL,
 profile text NOT NULL,
 cache_key text NOT NULL,
 selected_at timestamptz NOT NULL DEFAULT now(),
 PRIMARY KEY(corpus_root,file_id,profile),
 FOREIGN KEY(corpus_root,file_id,cache_key)
   REFERENCES rag.km_corpus_caches(corpus_root,document_id,cache_key)
);
CREATE TABLE IF NOT EXISTS rag.km_chunk_job_files (
 job_id text NOT NULL,
 corpus_root text NOT NULL,
 profile text NOT NULL,
 file_id text NOT NULL,
 path text NOT NULL,
 cache_key text NOT NULL,
 cache_digest text NOT NULL,
 missing_count bigint NOT NULL,
 chunk_ids jsonb,
 status text NOT NULL DEFAULT 'pending',
 computed integer NOT NULL DEFAULT 0,
 published boolean NOT NULL DEFAULT false,
 error text,
 PRIMARY KEY(job_id,file_id)
);
COMMENT ON TABLE rag.km_cache_chunks IS 'Authoritative cache chunks before embedding; pages are physical source locators, not processing batches.';
COMMENT ON TABLE rag.km_chunk_job_files IS 'Frozen job targets and per-PDF outcomes. Resume creates a new job from the actual missing vectors, never from stale queue counters.';
COMMENT ON COLUMN rag.km_corpus_caches.document_id IS 'Inventory file ID (24 hex), exposed by the API as file_id.';
