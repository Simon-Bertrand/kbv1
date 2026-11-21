# KM API documentaire (km-api)

Ce package implémente l'API REST documentaire de Knowledgebase dans `src/km/`.
L'application FastAPI principale est `km.api:app`.

Ce service expose la recherche et la lecture du corpus PostgreSQL aux clients
Athena (`km-agent`), WebUI et console d'administration KM (`km-frontend`).

## Endpoints principaux

| Méthode & Route | Description | Consommateurs |
| --- | --- | --- |
| `POST /search` | Recherche documentaire (modes `lexical` BM25, `semantic` E5, `hybrid` RRF) avec reranking cross-encoder optionnel. | WebUI direct, Athena |
| `GET /documents/{id}/source-reference` | Retourne la référence de métadonnées `{ document_id, relative_path: "_versions/<sha256>.pdf" }` sans transférer d'octets. | WebUI BFF (signature JWT) |
| `GET /documents/{id}` | Métadonnées complètes du document et inventaire des chunks. | Athena, Console KM |
| `GET /documents/{id}/pages?summary=true` | Plan paginé avec titres et localisations des unités. | Athena |
| `GET /pages/{id}` / `GET /passages/{id}` | Lecture des unités textuelles documentaires. | Athena |

Les routes `GET /profiles` et `POST /profiles/register` appartiennent à
`km-indexer:8011`, pas à ce service.

En mode KM, `/search` accepte `document_ids` et `metadata_filters` et les applique avant le classement. La recherche visuelle accepte le même périmètre quand un encodeur visuel et son profil sont disponibles. Consulter `capabilities.search_scope` et `capabilities.visual_search_scope` dans `/health`. Une recherche filtrée réussie renvoie `scope_applied: true`.

## Intégration du Reranker cross-encoder

Avec `RERANKER_URL` et `RERANKER_DIRECT_SEARCH=true`, `/search` transmet la question,
le vecteur éventuel, les filtres et les limites au reranker. Celui-ci interroge
PostgreSQL et renvoie les résultats finaux. Le pool contient au moins 50
candidats par défaut et reste borné à 100. Avec `RERANKER_FAILURE_MODE=fallback`,
une panne du chemin direct déclenche la recherche SQL de KM API avec la limite
demandée. Le mode `error` renvoie 503. Le chemin HTTP de reclassement classique
reste compatible avec les rerankers qui exposent uniquement `/rerank`.
Consulter le [guide d'intégration](../reranker/docs/integration.md) pour le compte SQL.

## Sécurité et posture d'authentification

> [!NOTE]
> **Vérification d'authentification Bearer :**
> Les endpoints de `km-api` vérifient activement les jetons `KM_API_TOKEN` / `KM_INDEXER_ADMIN_TOKEN` via un middleware HTTP à temps constant.
> En production, toute requête non authentifiée ou avec jeton invalide est rejetée (401/403). La sonde `/health` reste accessible sans authentification pour la supervision.

## Démarrage local

```bash
uv run --directory packages/km-api uvicorn km.api:app --host 0.0.0.0 --port 8000 --no-proxy-headers
```
