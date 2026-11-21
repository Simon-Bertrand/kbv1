-- Adapter la dimension et le profil au modèle choisi avant installation.
-- text-v1 identifie modèle, révision et prétraitement immuables.
CREATE TABLE rag.text_embeddings (
    chunk_id text PRIMARY KEY REFERENCES rag.chunks (chunk_id) ON DELETE CASCADE,
    profile text NOT NULL DEFAULT 'text-v1' CHECK (profile = 'text-v1'),
    input_hash text NOT NULL, -- content_hash du texte contextualisé encodé
    embedding rag.vector(1024) NOT NULL
);
