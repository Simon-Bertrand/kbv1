# Schéma PostgreSQL RAG

Construction Docker : préparer les dépendances avec `make prepare-build` depuis
la racine sur une machine connectée, ou charger leur bundle avec `make load-build`
hors ligne. Voir le [guide hors réseau](../../README.md#utilisation-hors-réseau).

## Démarrage avec Docker Compose

Depuis la racine du dépôt, après configuration du `.env` commun (voir le
[README racine](../../README.md#docker-compose)) :

```bash
docker compose --env-file .env -f packages/database/compose.yaml up -d --build
docker compose --env-file .env -f packages/database/compose.yaml logs -f postgres
```

pgAdmin est accessible sur http://localhost:5050 avec `admin@example.com` /
`adminpass`. Le serveur **Knowledgebase** est préconfiguré ; à la connexion,
saisir le mot de passe PostgreSQL `testpass`. La base `knowledgebase` est aussi
accessible sur `localhost:5432`, utilisateur `postgres`.

Ces identifiants sont des valeurs de développement. Les variables d'environnement
`POSTGRES_PASSWORD`, `PGADMIN_EMAIL`, `PGADMIN_PASSWORD`, `POSTGRES_PORT` et
`PGADMIN_PORT` permettent de les personnaliser avant le premier lancement.
Les interfaces d’écoute sont réglables avec `POSTGRES_HOST` et `PGADMIN_HOST`
(toutes les interfaces par défaut).

PostgreSQL et pgAdmin conservent leurs données dans des volumes Docker.
Les identifiants initiaux et les scripts SQL ne sont appliqués qu'à la création
des données correspondantes. pgAdmin attend que PostgreSQL soit prêt.

```bash
# Vérifier les extensions et les fonctions de recherche.
docker compose --env-file .env -f packages/database/compose.yaml exec postgres \
  psql -U postgres -d knowledgebase -c 'SELECT extname, extversion FROM pg_extension;'
docker compose --env-file .env -f packages/database/compose.yaml exec -T postgres \
  psql -U postgres -d knowledgebase -X -v ON_ERROR_STOP=1 < packages/database/sql/rag/tests/retrieval.sql

# Arrêter les services en conservant leurs données.
docker compose --env-file .env -f packages/database/compose.yaml down
```

## Installation du schéma initial

Le schéma initial comporte six tables : `documents`, `chunks`, `visuals`, `chunk_visuals`,
`text_embeddings` et `visual_embeddings`. PostgreSQL doit disposer de pgvector
en version >= 0.8.0, de pgvectorscale (extension SQL `vectorscale`) et de
`pg_textsearch` ; le rôle d'installation doit pouvoir activer ces extensions.
Avant installation, ajouter `pg_textsearch` à `shared_preload_libraries` en
conservant les autres bibliothèques configurées, puis redémarrer PostgreSQL.

Avant installation, adapter les dimensions d'exemple **1024** (texte) et **768**
(image), ainsi que les profils `text-v1` et `visual-v1` dans les scripts.
Adapter également les contrôles de dimensions de `procedures/03_search.sql`.
Chaque profil désigne une configuration immuable : modèle, révision et
prétraitement. Ne pas mélanger des modèles dans une même table vectorielle.

Depuis la racine du dépôt, sur une base dont les tables RAG n'existent pas :

```bash
cat packages/database/sql/rag/00_schema.sql packages/database/sql/rag/extensions/*.sql packages/database/sql/rag/tables/*.sql packages/database/sql/rag/indexes/*.sql packages/database/sql/rag/procedures/*.sql packages/database/sql/rag/security/*.sql \
    | psql "$DATABASE_URL" -X --set=ON_ERROR_STOP=1 --single-transaction --file=-
```

L'ordre des dossiers dans la commande et les préfixes numériques assurent
l'ordre des dépendances : schéma, extensions, tables, index, fonctions, puis
propriétaires et droits d’accès.
Le schéma `rag` est créé explicitement avant les extensions et les tables.
Tous les objets applicatifs et les trois extensions sont installés dans `rag` ;
les types (`rag.vector`), opérateurs, références et fonctions sont qualifiés.
Les index et clés primaires appartiennent au schéma de leur table. Les méthodes
d’accès PostgreSQL (HNSW, DiskANN, BM25) sont des objets globaux au serveur SQL
et ne sont pas rattachées à un schéma.
Ces scripts définissent le schéma initial et sont destinés à une base vide.
Les clés primaires créent déjà leurs index ; `indexes/` contient uniquement
les index supplémentaires, dont deux HNSW pour la distance cosinus (`<=>`).

L'image Docker installe les trois extensions et configure le préchargement de
`pg_textsearch`. Son initialisation exécute les mêmes groupes dans cet ordre,
puis les tests SQL, uniquement lorsque le répertoire de données est vide.
Pour une base existante, reconstruire l’image ne rejoue pas l’initialisation.
Le schéma est installé sur une base réinitialisée : aucune migration depuis
`public` n’est fournie. L’initialisation crée directement les objets dans `rag`.

## Schéma KM et WebUI

L’entrypoint installe le schéma complet au premier démarrage sur un volume PostgreSQL vide. Les tables KM sont réparties entre les métadonnées documentaires, les [publications par profil](sql/rag/tables/09_km_publications.sql) et le [catalogue de caches](sql/rag/tables/10_km_catalog.sql). Les [tables WebUI](sql/webui/10_tables.sql) incluent les révisions, les tombstones et les quotas.

Les caches PdfParse sont des fichiers de `DATA_DIR`. Utiliser la [reprise des caches](../../ressources/TUTO_DEPLOY.md#8-initialisation-de-postgresql-et-reprise-des-caches) pour remplir la nouvelle base à partir du corpus existant.

Les services KM utilisent directement des tables et des fonctions supplémentaires,
dont `rag.rag_search_profile` en `SECURITY INVOKER`. Le rôle historique `agent`,
limité aux trois fonctions décrites ci-dessous, ne convient pas tel quel. Les
droits du compte d’API en lecture (`km_reader`) et de celui d’indexation en écriture (`km_writer`)
doivent correspondre à leurs opérations. Les Compose dev/prod utilisent la base
`POSTGRES_DB` et les mots de passe distincts `POSTGRES_KM_READER_PASSWORD` et
`POSTGRES_KM_WRITER_PASSWORD` du [fichier d’exemple](../../.env.example).
Les droits de ces rôles sont installés au premier démarrage de PostgreSQL.

L’API [KM](../km-api/README.md), l’[indexeur](../embeddings/README.md) et la
[console](../km-frontend/README.md) décrivent l’usage actuel de ce schéma étendu.
Les contrats et exemples SQL qui suivent concernent le schéma initial et son
client `dbclient`.

## Client Python

Le package [`dbclient`](clients/python/README.md) expose `RagClient.search()`,
`get_chunks()` et `get_visuals()` sur une connexion Psycopg injectée. Il qualifie
tous les appels dans `rag`, retourne des dictionnaires typés et conserve la
politique de transaction de l’application.
Le rôle `agent` a `USAGE` sur `rag` et `EXECUTE` uniquement sur les trois
fonctions de cette API, sans aucun accès direct aux tables. Les fonctions
installées par `security/01_agent.sql` sont `SECURITY DEFINER`, avec un
propriétaire technique `rag_executor` sans connexion et sans droit d’écriture.

## Compte agent et mot de passe

La valeur de **`POSTGRES_AGENT_PASSWORD` est définie uniquement dans le bloc
`environment` du service `postgres` de `packages/database/compose.yaml`**. Remplacer
la valeur de développement directement dans ce fichier. Elle n’est pas dans
le SQL, le `.env` partagé, ni l’environnement des autres services. Le Compose de développement `compose.dev.yaml` inclut PostgreSQL et pgAdmin par défaut via `packages/database/compose.yaml`. Le Compose de production hérite également de cette configuration.

Sur une nouvelle base, l’image applique les droits, les vérifie avec les tests
SQL, puis `init-rag-agent` définit le mot de passe SCRAM et active la connexion.
Le rôle reste `NOLOGIN` si cette étape échoue. Les droits ne reposent pas sur
le seul réglage de session « lecture seule » : même si le client le désactive,
les ACL continuent de refuser les accès directs.

Le compte ne reçoit ni rôle système, ni rôle de son propriétaire technique, ni
permission de lecture/écriture sur les tables, ni création de schémas/tables
temporaires. Les droits implicites `PUBLIC` sont retirés des objets RAG et du
schéma `public`. Les nouvelles fonctions créées par l’administrateur ne sont
plus exécutables par `PUBLIC` par défaut. Cette configuration cible une base
RAG dédiée ; d’autres utilisateurs applicatifs nécessitent des grants explicites.
Des règles `pg_hba.conf` imposent l’authentification de `agent`, y compris sur
le socket local, et refusent sa connexion aux autres bases du serveur.

Pour effectuer une rotation du mot de passe de l’agent sur la base en service :

```bash
docker compose --env-file .env -f packages/database/compose.yaml up -d --build postgres
docker compose --env-file .env -f packages/database/compose.yaml exec postgres init-rag-agent
```

Le helper est réexécutable, ne supprime aucune donnée et remplace uniquement
son propre bloc HBA. Il requiert un `POSTGRES_DB` composé de lettres ASCII,
chiffres, `_`, `.` et `-`. Les sessions déjà authentifiées ne sont pas coupées
par une rotation du mot de passe ; reconnecter le client pour utiliser le nouveau.
Après une mise à jour des fonctions SQL, réappliquer ce helper pour conserver
le propriétaire et l’allowlist des droits.

**Limite de PostgreSQL :** un login reste un client SQL. Les ACL ne peuvent pas
interdire toute expression (`SELECT 1`, certains appels natifs et la lecture de
catalogues publics). Pour imposer strictement les trois opérations RAG, ne pas
donner d’outil SQL libre au LLM : exposer uniquement les trois méthodes paramétrées
de `RagClient` derrière une API applicative. Les réglages `statement_timeout`
et lecture seule sont des garde-fous, pas un remplacement de cette frontière.
Voir les [privilèges PostgreSQL](https://www.postgresql.org/docs/17/ddl-priv.html)
et les [précautions SECURITY DEFINER](https://www.postgresql.org/docs/17/sql-createfunction.html#SQL-CREATEFUNCTION-SECURITY).

## Contrat d'ingestion

- Une indexation courante par document et un chemin canonique par PDF.
  `document_id` et les identifiants de records viennent directement de PdfParse.
- `chunks.record` conserve le record textuel v4 complet et `visuals.record`
  le record visuel v1 complet. L'application valide le contenu complet ; SQL
  contrôle les identifiants, le format et la version. Les futurs formats
  nécessitent une migration explicite.
- Les métadonnées métier du fichier vont dans `documents.metadata`.
  `documents.filepath` est le chemin courant ; les chemins présents dans les
  records décrivent l'export d'origine.
- Construire `chunk_visuals` depuis `visual_record.chunk_ids`, en vérifiant
  côté ingestion que les deux records appartiennent au même document.
- Encoder `embedding_text` pour le texte et utiliser `content_hash` comme
  `input_hash`. Pour l'image, encoder le crop et hacher son contenu réel.
  Réutiliser un embedding uniquement si son profil et son entrée sont inchangés.
- Publier une réindexation dans une transaction, retirer les anciens records
  et liens disparus, et mettre à jour `indexed_at` explicitement. Les suppressions
  se propagent aux liens et embeddings par `ON DELETE CASCADE`.
- Les PDF et crops restent dans leur stockage de fichiers ; aucun runtime
  ni image PIL ne doit être inséré dans les JSON.

Le schéma initial conserve un chemin courant et un profil vectoriel par table.
Le schéma KM inclut les identités de fichiers et versions ainsi que
`km_profile_documents`, `km_profile_units` et `km_text_embeddings` pour conserver
plusieurs publications textuelles par profil. `km_embedding_settings` sélectionne
le profil actif ; ce mécanisme est distinct des exemples `rag_search` ci-dessous.

## Fonctions pour l'agent RAG

Les trois scripts du schéma initial sous `sql/rag/procedures/` contiennent des **fonctions** appelables par `SELECT`, pour retourner
directement des lignes. Après application de `security/01_agent.sql`, elles sont
`STABLE`, `SECURITY DEFINER`, sans écriture
ni gestion interne de transaction. Leur `search_path` est fixé à
`pg_catalog, rag, pg_temp`, et leurs tables, fonctions vectorielles et opérateurs
sont qualifiés explicitement. Les appels fonctionnent même si la connexion
n'inclut pas `rag` dans son `search_path`.

| Fonction | Entrée | Sortie |
| --- | --- | --- |
| `rag.rag_search` | Vecteur texte et/ou visuel, limites et filtres facultatifs | Classement unifié, texte ou figure, sources et métadonnées |
| `rag.rag_get_chunks` | Tableau de 0 à 100 IDs | Records textuels complets, chemin courant et métadonnées |
| `rag.rag_get_visuals` | Tableau de 0 à 100 IDs | Records visuels complets, chemin courant et métadonnées |

Les vecteurs sont calculés par l'application avec les profils d'indexation.
SQL n'encode pas une question brute. Pour la recherche texte-vers-image,
le vecteur visuel doit venir de l'encodeur de requêtes compatible avec le modèle
ayant encodé les crops, pas du modèle textuel indépendant.

Exemples paramétrés à exécuter via le pilote PostgreSQL :

```sql
-- Texte uniquement ; $1 est un vecteur du profil text-v1.
SELECT * FROM rag.rag_search(p_text_query => $1::rag.vector, p_top_k => 10);

-- Visuel uniquement ; les figures liées retournent leurs chunks.
SELECT * FROM rag.rag_search(p_visual_query => $1::rag.vector);

-- Les deux modalités, limitées à des documents et métadonnées donnés.
SELECT * FROM rag.rag_search(
    p_text_query => $1::rag.vector,
    p_visual_query => $2::rag.vector,
    p_top_k => 10,
    p_candidates => 100,
    p_document_ids => $3::text[],
    p_metadata => $4::jsonb
);

-- Charger les records complets ou les voisins identifiés dans evidence.
SELECT * FROM rag.rag_get_chunks($1::text[]);
SELECT * FROM rag.rag_get_visuals($1::text[]);
```

`p_document_ids = NULL` ne filtre pas ; un tableau vide ne retourne rien.
`p_metadata` est un objet de conditions d'inclusion JSON (`@>`), par exemple
`{"category":"maintenance"}` ; NULL ne filtre pas. Ces filtres sont appliqués
avant la limite des candidats. Ce ne sont pas des règles d'autorisation :
les droits d'accès doivent être imposés par l'application ou des politiques RLS.
Les lectures par IDs dédupliquent les entrées, conservent leur premier ordre
d'apparition et omettent les IDs absents ; un tableau NULL est refusé.

La recherche produit au plus `p_top_k` lignes (1–100), à partir d'au plus
`p_candidates` voisins par modalité (top_k–1000). Une figure liée à plusieurs
chunks peut contribuer à chacun ; une figure sans chunk reste un résultat
`kind = 'visual'`. La fusion utilise RRF, constante 60, poids égaux : le `score`
est un score de classement, pas une probabilité ni une similarité cosinus.
Plusieurs figures ne multiplient pas le score visuel d'un même chunk : seul
son meilleur rang visuel contribue. Les rangs absents sont NULL.

`evidence` contient les passages originaux, contexte, pages, géométries et assets
utiles aux citations ; `matched_visual_ids` permet de charger les figures ayant
contribué. Une figure autonome fournit légende, description, OCR et chemin du crop.
Les records complets et la provenance restent disponibles via les fonctions
de lecture par IDs. `filepath` est toujours le chemin courant du document.
Le nombre de résultats est borné, pas leur taille en caractères : l'application
doit appliquer le budget de contexte de son LLM sans perdre les citations.

### Transactions et performances

Un `SELECT rag_search(...)` utilise déjà un snapshot cohérent. Ne pas ajouter
de verrous d'écriture pour rechercher. Si plusieurs appels doivent voir exactement
la même indexation (recherche puis lecture de voisins), utiliser une transaction
courte `BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY`, puis `COMMIT` dès que
les données sont lues, avant la génération LLM. Pour un seul appel, l'autocommit
suffit. Le serveur peut appliquer un `statement_timeout` adapté au service.

Les deux recherches conservent `ORDER BY embedding <=> query LIMIT ...`, compatible
avec HNSW. Les JSON de réponse sont construits seulement après sélection des
gagnants. Les filtres facultatifs sont simplifiés grâce à un plan spécifique à
l'appel. Les réglages HNSW locaux à la fonction (`ef_search=100`, parcours itératif
strict) sont restaurés au retour ; aucun réglage de session ne fuit vers un pool.
Le rappel reste approximatif et les filtres peuvent produire moins de résultats
que demandé si la limite de parcours HNSW est atteinte. PostgreSQL peut choisir
un parcours exact, notamment pour une petite table ou un filtre très sélectif.
Mesurer les plans sur le corpus réel avant d'ajouter d'autres index.

Après chargement des données, exécuter `ANALYZE` sur les tables concernées.
Les paramètres proposés sont un point de départ, pas une garantie de performance
maximale sur tout corpus. Références :
[pgvector](https://github.com/pgvector/pgvector#iterative-index-scans) et
[fonctions PostgreSQL](https://www.postgresql.org/docs/current/sql-createfunction.html).

### Validation

Sur une base de test ayant reçu tables, index et fonctions :

```bash
psql "$DATABASE_URL" -X --set=ON_ERROR_STOP=1 --file=packages/database/sql/rag/tests/retrieval.sql
psql "$DATABASE_URL" -X --set=ON_ERROR_STOP=1 --file=packages/database/sql/rag/tests/agent_permissions.sql
```

Les fixtures sont insérées dans une transaction annulée en fin de test.
Les tests s’exécutent comme administrateur ; la seconde suite utilise `SET ROLE`
pour vérifier les accès autorisés et interdits. Pour les connexions réelles,
les mots de passe et les règles HBA dans un conteneur temporaire :

```bash
docker compose --env-file .env -f packages/database/compose.yaml build postgres
RAG_SECURITY_DOCKER_TESTS=1 .venv/bin/python -m pytest packages/database/tests/test_agent.py -q
```

### Identité des PDF et référentiel de types

Le schéma initial crée le référentiel des types et les colonnes de nom original
et de type dans les documents et les publications par profil. Ces colonnes sont
alimentées à l’indexation.

Le [schéma initial](sql/rag/tables/11_km_document_types.sql) charge la photographie
de `DATA_DIR/typeDocuments.json`, avec les sept champs métier. Lors d’un changement
du référentiel, mettre à jour ce SQL avant d’initialiser une nouvelle base.


Les recherches SQL `rag.rag_search` et `rag.rag_search_profile` acceptent les
paramètres facultatifs `p_type_document_ids`, `p_perimeter_code` et
`p_type_document_labels`. Les valeurs d’une liste sont alternatives. Les filtres
combinés s’intersectent avant la sélection des candidats.

`rag.chunk_document_metadata(chunk_ids, profile)` lit jusqu’à 100 références PDF
d’un coup. Les identifiants publics sont les handles `c…` retournés par KM API.
`profile` sélectionne la publication, ou peut être omis pour l’index historique.
KM API expose le référentiel sur `GET /document-types` et la lecture groupée sur
`POST /chunks/metadata` dans le même périmètre d’accès que la recherche.
