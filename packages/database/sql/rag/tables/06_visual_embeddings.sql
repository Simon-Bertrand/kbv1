-- Adapter la dimension et le profil au modèle choisi avant installation.
-- visual-v1 identifie modèle, révision et prétraitement immuables.
CREATE TABLE rag.visual_embeddings (
    visual_id text PRIMARY KEY REFERENCES rag.visuals (visual_id) ON DELETE CASCADE,
    profile text NOT NULL DEFAULT 'visual-v1' CHECK (profile = 'visual-v1'),
    input_hash text NOT NULL, -- SHA-256 du contenu du crop, pas de son chemin
    embedding rag.vector(768) NOT NULL
);
