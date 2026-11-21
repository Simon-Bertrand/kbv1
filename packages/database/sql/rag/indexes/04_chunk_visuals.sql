-- La clé primaire couvre déjà les recherches commençant par chunk_id.
CREATE INDEX chunk_visuals_visual_idx ON rag.chunk_visuals (visual_id);
