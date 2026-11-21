-- Table des conversations utilisateur
CREATE TABLE IF NOT EXISTS webui.conversations (
    id VARCHAR(128) PRIMARY KEY,
    user_id TEXT NOT NULL,
    title VARCHAR(255) NOT NULL DEFAULT 'Nouvelle conversation',
    draft TEXT,
    scroll_top NUMERIC,
    revision BIGINT NOT NULL DEFAULT 1,
    deleted_at TIMESTAMPTZ,
    storage_bytes BIGINT NOT NULL DEFAULT 0 CHECK (storage_bytes >= 0),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT conversations_owner_key UNIQUE (id, user_id)
);

CREATE INDEX IF NOT EXISTS idx_webui_conversations_user
    ON webui.conversations(user_id, updated_at DESC);

-- Table des messages des conversations
CREATE TABLE IF NOT EXISTS webui.messages (
    id VARCHAR(128) PRIMARY KEY,
    conversation_id VARCHAR(128) NOT NULL,
    user_id TEXT NOT NULL,
    role VARCHAR(16) NOT NULL CHECK (role IN ('user', 'assistant')),
    content TEXT NOT NULL,
    sources JSONB DEFAULT '[]'::jsonb,
    interrupted BOOLEAN DEFAULT FALSE,
    error BOOLEAN DEFAULT FALSE,
    error_message TEXT,
    generating BOOLEAN DEFAULT FALSE,
    comparison JSONB,
    context JSONB,
    ordinal INTEGER NOT NULL DEFAULT 0,
    mode VARCHAR(16) CHECK (mode IS NULL OR mode IN ('fast', 'deep')),
    fast_id VARCHAR(128),
    fast_answer TEXT,
    fast_sources JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT messages_conversation_owner_fkey
        FOREIGN KEY (conversation_id, user_id)
        REFERENCES webui.conversations(id, user_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_webui_messages_conv
    ON webui.messages(conversation_id, created_at ASC);
CREATE INDEX IF NOT EXISTS idx_webui_messages_user
    ON webui.messages(user_id);

CREATE INDEX IF NOT EXISTS messages_ordinal_idx ON webui.messages(conversation_id, ordinal);


CREATE TABLE IF NOT EXISTS webui.request_buckets (
 key_hash char(64) PRIMARY KEY, tokens double precision NOT NULL CHECK(tokens>=0), updated_at timestamptz NOT NULL
) WITH (
 autovacuum_vacuum_scale_factor = 0.05,
 autovacuum_vacuum_threshold = 50,
 autovacuum_vacuum_cost_limit = 1000
);
CREATE INDEX IF NOT EXISTS request_buckets_updated ON webui.request_buckets(updated_at);
