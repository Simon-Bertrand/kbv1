# Recherche documentaire

La WebUI Next.js interroge l’index PostgreSQL existant (`rag.documents`, `rag.chunks` et `rag.text_embeddings`) via `rag.rag_search_hybrid`. Le mode BM25 est disponible dès que l’index contient du texte. Les modes **Vectoriel** et **Hybride** utilisent la distance cosinus, avec fusion RRF en mode hybride.

## Démarrer

Avec Node.js 22.13+ et les services [PostgreSQL](packages/database/README.md) et [fileserver](packages/fileserver/README.md) démarrés, renseigner `packages/webui/.env.local`.
Les deux guides décrivent aussi le démarrage des services locaux avec Docker Compose.

```bash
cd packages/webui
cp .env.example .env.local
npm ci
# Renseigner .env.local, puis lancer la WebUI
npm run dev
```

Ouvrir [localhost:3000](http://localhost:3000). Pour un lancement avec le build de production, utiliser `npm run build` puis `npm start`.

- **PostgreSQL** — `DATABASE_URL` pointe vers le corpus indexé. Utiliser un compte en lecture seule qui peut exécuter `rag.rag_search_hybrid` et lire ses tables, par exemple `km_reader` si sa connexion est activée et les droits KM appliqués.
- **Chemins** — `rag.documents.filepath` contient un chemin relatif à `DATA_DIR`. Si la base stocke des chemins absolus, indiquer le répertoire racine tel qu’il figure en SQL dans `RAG_PATH_PREFIX`. Par exemple, `/corpus` transforme `/corpus/manuels/notice.pdf` en `manuels/notice.pdf`.
- **Fileserver** — fixer `FILESERVER_URL` à l’origine visible du navigateur et configurer le même `FILESERVER_JWT_SECRET` côté fileserver et dans `.env.local`. Dans le Compose de fileserver, définir `WEBUI_AUTH_URL=http://localhost:3000` pour autoriser cette origine.
- **Encodeur** — pour les modes Vectoriel et Hybride, renseigner `EMBEDDINGS_URL` avec l’URL complète de l’endpoint compatible OpenAI (`…/v1/embeddings`), ainsi que `EMBEDDINGS_MODEL`. Utiliser le même encodeur et le même prétraitement que ceux prévus pour l’index texte de 1 024 dimensions. `EMBEDDINGS_API_KEY` et `EMBEDDINGS_QUERY_PREFIX` sont optionnels.

Au clic, PDF.js ouvre le PDF à la page du passage trouvé. L’API Next.js émet un JWT pour le chemin résolu en base, valable cinq minutes. Le navigateur l’envoie en en-tête `Authorization` au fileserver pour les lectures du PDF et le renouvelle à la pagination si nécessaire. La WebUI écoute sur la boucle locale et donne accès au corpus du compte SQL à toute personne qui peut joindre l’application.

## Développement local sans conteneur

```bash
make setup            # Environnements Python et interfaces Node.js
make models-e5        # À faire une fois si les poids E5 ne sont pas déjà disponibles
make dev              # Démarrage groupé. Ctrl+C arrête les processus lancés.
```

Python 3.13, `uv`, Node.js et npm sont nécessaires. Le `.venv` racine contient
PdfParse et l’indexeur. L’encodeur E5 utilise `.dev/encoder-venv` pour conserver
Transformers 4. PdfParse utilise Transformers 5. pgAdmin a son propre environnement
sous `.dev`. Le module `km-api`, dépendance de l’indexeur et de l’encodeur,
est recherché dans `packages/km-api` puis `../knowledgebase/packages/km-api`.
Indiquer `KM_API_DIR=/chemin/vers/km-api` à `make setup` si besoin.

PostgreSQL 17 doit fournir `vector`, `vectorscale` et `pg_textsearch`.
Le fileserver utilise NGINX avec njs et le module mod_zip patché du dépôt.
Le lanceur trouve les binaires locaux préparés dans `.dev/native` ou utilise
les installations système. Les chemins peuvent être indiqués dans `.env.dev.local`
avec `PG_BIN`, `NGINX_BIN`, `NGINX_MODULE_DIR` et `NGINX_MIME_TYPES`.
Le profil PdfParse par défaut utilise un GPU CUDA et les poids du cache
`HF_HUB_CACHE`. L’encodeur E5 tourne sur CPU et lit `.dev/models/e5` par défaut.

| Service | Port par défaut | Cible individuelle |
| --- | ---: | --- |
| PostgreSQL | 5432 | `make postgres` |
| Fileserver | 8080 | `make fileserver` |
| PdfParse Studio | 8000 | `make pdfparse` |
| Encodeur E5 | 8001 | `make encoder` |
| Indexeur KM | 8011 | `make indexer` |
| WebUI | 3000 | `make webui` |
| pgAdmin | 5050 | `make pgadmin` |

Les services écoutent sur la boucle locale. Les ports se règlent dans
`.env.dev.local` avec `POSTGRES_PORT`, `FILESERVER_PORT`, `PDFPARSE_PORT`,
`ENCODER_PORT`, `INDEXER_PORT`, `WEBUI_PORT` et `PGADMIN_PORT`.
`make dev SERVICES="postgres fileserver webui"` lance un sous-ensemble.
Les journaux sont dans `.dev/log/`. Les secrets sont générés dans
`.dev/secrets.env`, dont le mot de passe Studio pour l’utilisateur `studio`.
La base locale persiste dans `.dev/postgres` et initialise le schéma sur un
cluster vierge. `data/` est le corpus par défaut.

`make pdfparse-run ARGS="--files-manifest /chemin/selection.json"` prépare les
caches avec le profil PdfParse par défaut. `make versions` conserve les copies
immuables nécessaires à la publication par l’indexeur. Pour préparer un seul PDF,
utiliser `make versions ARGS="data/…/files/document.pdf"`. Le démarrage des
services laisse le traitement du corpus à une commande ou à un job explicite.
