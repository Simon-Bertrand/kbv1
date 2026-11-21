# Client Python de la base RAG

Ce client couvre l’API SQL historique à trois fonctions. Il ne remplace pas
le client HTTP de [KM](../../../km-api/README.md) ni sa recherche hybride par
profil, ajoutée par les migrations du package database.


`dbclient` appelle les fonctions SQL `rag.rag_search`, `rag.rag_get_chunks` et
`rag.rag_get_visuals`. Les fonctions s'appellent avec `SELECT` et non `CALL`.
Le client utilise Psycopg 3, réutilise la connexion injectée et retourne des
dictionnaires typés (`SearchHit`, `Chunk`, `Visual`). Les champs JSON sont
décodés en objets Python. Aucun modèle d'embedding n'est chargé par le client.
Le compte de connexion `agent` dispose uniquement de l’exécution de ces trois
fonctions applicatives ; il n’a pas besoin de `SELECT` sur les tables RAG.
Son mot de passe est configuré dans le Compose PostgreSQL (`POSTGRES_AGENT_PASSWORD`),
jamais dans ce package.

L'agent RAG ne doit utiliser aucun autre compte que `agent`. `RagClient` vérifie à
l'initialisation que la connexion injectée appartient bien au rôle `agent` (rejet
immédiat si un autre rôle comme `postgres` est utilisé, sauf option explicite
`require_agent_user=False` réservée aux tests d'administration). De plus, `RagClient`
n'expose aucune méthode d'exécution de code SQL brut (`execute`, `query`, etc.) :
seules les trois fonctions RAG sécurisées (`get_chunks`, `get_visuals`, `search`)
sont accessibles. Côté moteur, le compte `agent` voit toutes ses permissions
directes sur les tables et schémas révoquées par PostgreSQL (`insufficient_privilege`).

## Installation

### Mode Workspace (racine du dépôt)

Depuis la racine du dépôt KnowledgeBase :

```bash
uv sync --locked --package dbclient --inexact
```

### Dans une application externe

```bash
pip install /chemin/knowledgebase/packages/database/clients/python
```

## Connexion et lectures

Exécuter les scripts avec `uv run --env-file .env --package dbclient --inexact
python mon_script.py` pour réutiliser la configuration racine. La bibliothèque
ne charge pas de `.env` et ne crée pas de connexion à l'import.

```python
import os

import psycopg
from dbclient import RagClient

with psycopg.connect(
    host="127.0.0.1",
    port=int(os.getenv("POSTGRES_PORT", "5432")),
    dbname="knowledgebase",
    user="agent",
    password=os.environ["POSTGRES_AGENT_PASSWORD"],
    autocommit=True,
) as connection:
    client = RagClient(connection)
    chunks = client.get_chunks(["chunk-1", "chunk-2"])
    visuals = client.get_visuals(["visual-1"])
    for chunk in chunks:
        print(chunk["filepath"], chunk["record"])
```

Entre conteneurs du même réseau Compose, utiliser `host="postgres", port=5432`.
Les lots acceptent 0 à 100 IDs, conservent le premier ordre demandé, dédupliquent
les IDs et omettent les absents. Les limites sont vérifiées par SQL.

## Recherche avec des vecteurs déjà calculés

```python
# Ces vecteurs viennent des modèles d'embedding compatibles avec l'index.
hits = client.search(
    text_query=text_embedding,      # séquence de 1024 nombres
    visual_query=visual_embedding,  # facultatif, séquence de 768 nombres
    top_k=10,
    candidates=100,
    document_ids=["document-1"],
    metadata={"category": "manual"},
)
for hit in hits:
    print(hit["kind"], hit["id"], hit["score"], hit["evidence"])
```

Utiliser cet appel dans le bloc de connexion précédent. Un seul des deux
vecteurs suffit. Les dimensions et la norme non nulle sont contrôlées par SQL ;
le client refuse les séquences vides et les valeurs non finies. Les vecteurs
sont transmis comme paramètres avec un cast explicite en `rag.vector`, sans
adaptateur pgvector ni modification du `search_path`.

`document_ids=None` ne filtre pas, tandis que `document_ids=[]` ne retourne rien.
`metadata=None` ne filtre pas ; les conditions d'un dictionnaire JSON sont
appliquées par inclusion. Le score est un classement RRF, pas une probabilité.

## Transactions et erreurs

Le client ne fait aucun commit ni rollback et ne ferme pas la connexion.
L'autocommit convient pour un appel isolé. Pour lire la recherche puis ses
records depuis le même snapshot, l'application contrôle une transaction courte :

```python
with connection.transaction():
    connection.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
    hits = client.search(text_query=text_embedding)
    chunks = client.get_chunks([hit["id"] for hit in hits if hit["kind"] == "chunk"])
```

Utiliser une connexion en autocommit avant ce bloc pour ouvrir une nouvelle
transaction. Les erreurs SQL remontent comme exceptions Psycopg, notamment
`psycopg.errors.InvalidParameterValue`. Une application qui utilise un pool
injecte une connexion empruntée pour la durée de l'opération.

## Tests

```bash
# Test unitaire ; les intégrations sont ignorées sans DSN explicite.
uv run --package dbclient --group dev --inexact pytest packages/database/clients/python/tests -q

# Base de test dédiée ayant reçu packages/database/sql/rag/ ; fixtures annulées après chaque test.
DBCLIENT_TEST_DSN='postgresql://postgres:mot-de-passe@localhost:5432/base_de_test' \
  uv run --package dbclient --group dev --inexact pytest packages/database/clients/python/tests -q
```
