# Embeddings E5 et indexation KM (km-embeddings)

Ce package implémente deux services FastAPI dans `src/km_embeddings/` :
- `km_embeddings.e5_service:app` : service d'encodage vectoriel E5 (microservice `km-encoder`, port interne 8001).
- `km_embeddings.index_service:app` : service d'indexation séquentielle et de gestion des profils/jobs dans PostgreSQL (microservice `km-indexer`, port interne 8011).

## Utilisation

Encodeur :
```bash
uv run uvicorn km_embeddings.e5_service:app --host 0.0.0.0 --port 8001 --no-proxy-headers
```

Indexeur :
```bash
uv run uvicorn km_embeddings.index_service:app --host 0.0.0.0 --port 8011 --no-proxy-headers
```
