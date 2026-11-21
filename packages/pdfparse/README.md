# PdfParse

Construction Docker : préparer les dépendances avec `make prepare-build` depuis
la racine sur une machine connectée, ou charger leur bundle avec `make load-build`
hors ligne. Voir le [guide hors réseau](../../README.md#utilisation-hors-réseau).

Bibliothèque Python configurable de préparation RAG : extraction PDF avec
Docling, enrichissement par handlers et chunking Chonkie avec provenance.
Python 3.12+, Docling 2.137.0 et docling-core 2.101.1.

```python
from pdfparse import PdfParseConfiguration, PdfParsePipeline

pipeline = PdfParsePipeline(PdfParseConfiguration())
result = pipeline.run("document.pdf")
```

`PdfParseConfiguration` est un modèle **Pydantic** figé : les types des composants
et les valeurs par défaut sont validés, et les paramètres inconnus sont refusés.
Les erreurs sont des `pydantic.ValidationError` localisées par champ. Les services
injectés restent les mêmes instances ; cette configuration runtime n’est pas un
format de persistance des modèles.

Une configuration, un orchestrateur, quatre composants interchangeables :

| Composant | Responsabilité | Défaut |
| --- | --- | --- |
| `layout` | Configuration du modèle de layout Docling | Heron natif |
| `enrich` | Handlers des nœuds ordinaires | Données natives, tableaux par ligne, formulaires indexables, filtrage des en-têtes/pieds |
| `picture_enrich` | Classification et handlers par catégorie d’image | Wrapping sans inférence |
| `chunking` | Découpage du document enrichi | Chonkie, 512 caractères |

`result.document` conserve les éléments, relations, légendes, pages et
localisations. `result.chunks` conserve le SHA-256 PDF, les références sources,
les géométries et la version de transformation. Les traitements échouent
explicitement ; aucun OCR/VLM ni modèle de secours n’est activé implicitement.

Les handlers intégrés rendent les tableaux sous forme de lignes avec leurs
en-têtes et les formulaires sous forme de paires clé-valeur explicites. Les
en-têtes et pieds de page restent dans le document, hors du texte indexable.
Le chunker regroupe les légendes avec leur propriétaire et conserve leurs sources.

```python
for chunk in result.chunks:
    texte_a_indexer = chunk.contextualized_text  # Titres de sections + extrait.
    extrait_exact = chunk.text                  # Tranche exacte fournie par Chonkie.
```

Avec Chonkie, `chunk.context` contient les titres à ajouter ; les références et
localisations comprennent aussi ces sources de contexte. Un chunk traversant
plusieurs sections reçoit leur chemin commun. Le contexte s’ajoute au budget
de caractères du chunk, sans annoncer de budget de tokens LLM.

## Préparer un index avec la structure documentaire

```python
from pdfparse import PdfParseConfiguration, PdfParsePipeline, StructuralChunkingModel

pipeline = PdfParsePipeline(PdfParseConfiguration(
    chunking=StructuralChunkingModel(chunk_size=512),
))
result = pipeline.run("document.pdf")
quality = result.quality_report()
result.export_jsonl("document.index.jsonl", language="fr")
```

Cette stratégie conserve les blocs courts, listes et code, découpe les grands
tableaux par lignes et ne traverse pas les sections. Les fragments reprennent
titres, légendes et contexte local utile. Son budget inclut tout le
`contextualized_text` : caractères par défaut, ou tokens avec un compteur local
injecté et un `tokenizer_id` explicite. Un repli sur une unité trop longue est
signalé dans `split_reason`.

Le rapport mesure la couverture des caractères non blancs du texte indexable,
sans compter deux fois les chevauchements, et signale les pages sans contenu
indexable, les blocs non cités et les textes suspects. L’export versionné fournit
sections, pages, types de contenu, offsets, géométries et empreintes. La langue
est fournie explicitement ; l’origine native/OCR est déduite des preuves
d’extraction disponibles, ou reste `unknown`. Les annotations de l’application
peuvent la préciser. Voir [le guide pré-RAG](../../ressources/docs/PRERAG.md).

`result.prepare_index()` crée un snapshot immuable de l’export et du diagnostic,
avec une seule construction du texte canonique. `preparation_manifest()` expose
les versions et réglages, `metrics` les durées et le débit. Studio propose le
chunking structurel, des diagnostics cliquables, la comparaison de préparations
et les exports. `EvidenceCase` et `evaluate_retrieval()` permettent de mesurer
la conservation des preuves et d’évaluer les classements de votre moteur RAG.

## Contexte RAG à partir des résultats de recherche

Indexer les chunks courts, puis utiliser leurs IDs pour reconstruire les blocs
sources complets avant l’appel au LLM :

```python
# IDs retenus par votre moteur de recherche, dans son ordre de pertinence.
hit_ids = [chunk.chunk_id for chunk in result.chunks[:2]]
context = result.source_context(hit_ids, max_characters=12_000)
texte_pour_le_llm = context.text
citations = context.source_refs
zones_pdf = context.locations
```

Si le budget le permet, les paragraphes/tableaux sources sont rassemblés dans
l’ordre documentaire, avec titres, légendes et géométries associés. Plusieurs
hits du même tableau ne répètent pas le tableau. `context.expanded` indique ce
mode ou une expansion partielle. Sinon, les chevauchements exacts sont fusionnés
et les passages sont agrandis progressivement dans l’ordre des hits. Si les
extraits minimaux dépassent le budget, une `ValueError` demande
de réduire la sélection ou d’augmenter le budget : aucun hit n’est abandonné
silencieusement. Le budget inclut contexte et séparateurs, en caractères.

Cette opération est locale, sans extraction, modèle ni service de recherche.
Elle fonctionne aussi après rechargement du document et recalcul des chunks.

Voir [ARCHITECTURE.md](ressources/ARCHITECTURE.md), [SCHEMAS.md](ressources/SCHEMAS.md),
[le guide d’enrichissement](../../ressources/docs/ENRICHMENT.md) et
[le guide des classifieurs de figures](../../ressources/docs/PICTURE_CLASSIFIER.md) et
[l’audit fichier par fichier](../../ressources/docs/FILE_AUDIT.md).

## Cache persistant des résultats

`PdfParseConfiguration(cache=True)` est le comportement par défaut. Pour
`/chemin/A.pdf`, `run()` écrit les résultats dans :

```text
/chemin/A.pdf.pdfparsecache/<hash_contenu_et_configuration>/
├── assets/              # figures extraites
├── document.json        # document enrichi et provenance
├── chunks.json          # chunks, références et géométries
├── index.jsonl          # records textuels pour le RAG
├── visuals.jsonl        # records visuels pour le RAG
├── document.md
├── models.json          # provenance des modèles
├── quality.json
├── preparation.json     # configuration et versions
├── result.json          # preuves d’extraction et métriques
└── manifest.json        # identité du PDF et empreintes des fichiers
```

La clé SHA-256 combine le contenu du PDF et la configuration canonique : modèles et provenance
des poids du registre, paramètres, routes, prompts, options Docling, chunking,
et codecs. Les versions des dépendances, le code Python de PdfParse et
l’inventaire des poids natifs ne participent pas à cette clé ; leur provenance
reste enregistrée dans `preparation.json`. Deux configurations conservent des
dossiers distincts. Le contenu SHA-256 du PDF et les empreintes
des fichiers sont vérifiés à la lecture ; un cache incomplet ou corrompu est
recalculé. Une publication atomique et un verrou commun par PDF évitent
qu’un lecteur consomme une écriture partielle.

Un résultat valide est restauré sans extraction, enrichissement ni chunking,
même après redémarrage. Les métriques restaurées décrivent le calcul initial.
Studio démarre sans charger de modèles ; `pdfparse-run` vérifie les caches
avant leur chargement. Les paramètres d’ordonnancement ne modifient pas la clé.
Le PDF original n’est jamais copié dans le cache.

Le dossier du PDF doit être accessible en écriture, y compris dans Docker.
Les images de `run()` sont stockées dans le cache adjacent ; `asset_store`
reste utilisé par `enrich_document()` seul ou avec `cache=False`.
Pour désactiver ce stockage : `PdfParseConfiguration(cache=False)`.
Supprimer une entrée supprime ses crops et force leur recalcul au prochain
appel. Les exports JSONL sont des fichiers de préparation : la publication RAG
viendra seulement après validation manuelle et ne fait pas partie de ce cache.

Les quotas disque s'appliquent par PDF, crops compris : `cache_max_entries=8`,
`cache_max_bytes=536870912` (512 Mio), `cache_max_age_seconds=None`
(sans expiration). Les entrées les moins récemment utilisées sont supprimées après
les traitements ; une entrée dépassant seule le budget provoque une erreur
avant publication. Le nettoyage n'est pas un service planifié.

```python
from pdfparse.ingestion.cache import cache_info, prune_cache

print(cache_info("/chemin/A.pdf"))
prune_cache("/chemin/A.pdf", max_entries=0)  # résultats ET crops supprimés
```

Studio abandonne aussi sa réponse HTTP après purge du cache disque. Son LRU
est limité par `PDFPARSE_CACHE_CAPACITY` et `PDFPARSE_CACHE_MAX_BYTES` ; ces
réglages sont distincts du quota disque. Le registre limite ses instances par
`ModelRegistry(max_instances=4)` ; les objets encore empruntés par un appelant
restent vivants.

Les composants personnalisés doivent déclarer leur identité sémantique, par
exemple `cache_identity={"extracteur": "v2", "seuil": 0.8}`. Inclure toutes
leurs options et versions de poids pertinentes et actualiser cette identité
quand leur comportement change. Charger les modèles intégrés via
`ModelRegistry` fournit la provenance des poids ; sinon fournir explicitement
cette version dans `cache_identity` ou désactiver le cache.
Les poids natifs Docling viennent de `MODELS_DIR/layout` et de
`MODELS_DIR/table_structure`. Le manifeste de préparation conserve leurs
chemins et empreintes.

Le Compose dev monte `MODELS_DIR` en lecture seule dans `/models` et expose
tous les GPU NVIDIA au conteneur. L'hôte doit
avoir NVIDIA Container Toolkit configuré pour Docker. Studio sélectionne le
profil compact partagé avec `pdfparse-run` et lit immédiatement ses caches ;
les modèles sont chargés à la demande pour les PDF sans cache. Depuis la racine :

```bash
docker compose -f compose.dev.yaml up -d --build --force-recreate pdfparse
```

## Application locale

PdfParse Studio fournit un explorateur de dossiers, un viewer PDF et trois
onglets : éléments enrichis avec provenance, Markdown et chunks surlignés dans
le PDF. Le bouton **Paramètres** permet de charger/décharger les modèles locaux,
de choisir leur device et leurs paramètres, et de consulter les objets effectifs
en lecture seule. Le frontend utilise un SDK généré depuis OpenAPI.

La bibliothèque liste récursivement les PDF sans plafond sur le nombre de
documents, en excluant les dossiers `.pdfparsecache`. La colonne de gauche
affiche 50 PDF par page ; sa recherche porte sur tout le corpus du dossier choisi.
L’API `GET /api/documents` accepte `page`, `page_size` (1 à 200) et `search`, et
renvoie `total`, `page`, `page_size` et les documents de cette page.
Pour la configuration appliquée dans Paramètres, les PDF disposant d’un cache
valide apparaissent en vert, y compris après un passage de `pdfparse-run` ou un
redémarrage de Studio. Sélectionner le PDF charge ses résultats sans cliquer sur
« Analyser le document ». Les caches d’une autre configuration, d’un PDF modifié
ou dont les fichiers sont incomplets ne donnent pas le statut vert. Un changement
de configuration appliquée actualise la liste automatiquement.
Le viewer charge directement les PDF avec PDF.js, utilise les plages HTTP,
dispose localement des ressources CMap/polices/WASM et gère les PDF protégés
par mot de passe. Le rendu du canvas est borné en mémoire ; les changements de
document, de page et de zoom annulent les rendus précédents.
Les pages se suivent dans un défilement vertical continu. Le numéro dans la
barre d’outils suit la page visible ; la saisie d’un numéro et les liens de
provenance font défiler le lecteur jusqu’à cette page. Seules les pages proches
de la zone visible gardent un canvas rendu, pour limiter la mémoire consommée.

Après installation de l’extra `studio` et compilation du frontend :

```bash
python -m pdfparse.backend
```

Ouvrir `http://127.0.0.1:8000`. Voir [le guide Studio](../../ressources/docs/STUDIO.md) pour
l’installation, la compilation, l’injection d’une pipeline et les tests navigateur.
Sans `--root` ni `PDFPARSE_SOURCE_ROOT`, une modale demande le chemin du dossier
racine des PDF. Aucun PDF n'est listé avant validation. `--root /chemin/vers/mes/pdf`
permet de fixer ce dossier au démarrage. Les modèles se trouvent dans
`MODELS_DIR/<catégorie>/<nom>`.

Studio démarre avec le profil compact de `pdfparse-run` sélectionné, sans charger
les modèles. Ses caches valides sont immédiatement consultables. Ce profil utilise le layout
natif Docling (Heron) et DFC v2.5 sur CPU, MinerU2.5-Pro-2605 et Qwen3.5-4B NF4
sur `cuda:0`.
L’OCR traite les blocs scannés ; les figures sont décrites en français par le
VLM, y compris en repli DFC (seuil 0,75), sans description des pages entières.
Les chunks récursifs visent 768 caractères. Le profil est défini dans
`backend/defaults.py` et partagé avec `pdfparse-run` et l’UI via `/api/runtime/options`. Préparer les poids avant
leur chargement. Les modèles sont chargés au premier traitement sans cache, ou
avec « Charger et appliquer » dans Paramètres. Les modèles explicitement sélectionnés
par les options CLI sont chargés au démarrage. `--native` sélectionne explicitement
le traitement natif sans le profil OCR/VLM.
Les modèles et devices restent modifiables dans Paramètres.

## Audit CSV des chunks en cache

`pdfparse-cachecheck` analyse les chunks du cache correspondant au même profil
compact que `pdfparse-run`, sans charger de modèle, et écrit les 35 colonnes
statistiques sur stdout. Les diagnostics restent sur stderr.

```bash
pdfparse-cachecheck --data-dir /data --workers 4 --batch-size 128 > chunks.csv
# Depuis la racine, avec le conteneur PdfParse déjà lancé :
bash packages/pdfparse/scripts/check-cache-in-compose.sh --workers 4 > chunks.csv
```

Le calcul IDF/MinHash exige une agrégation préalable sur disque ; le CSV est
ensuite émis et vidé par lots. `--configuration profil.json`, `--work-dir`,
`--strict` et `--input-jsonl` permettent de choisir le profil, le disque de
travail, le traitement des caches absents et une entrée texte indépendante.
Voir [les conventions, les 35 métriques, le DAG, les complexités et les benchmarks](docs/CACHECHECK.md).

## Installation

### Docker

Depuis la racine du dépôt :

```bash
docker compose -f compose.dev.yaml up -d --build pdfparse
```

Compose monte `DATA_DIR` et `MODELS_DIR` et expose les GPU NVIDIA. La paire
Torch 2.10 / Torchvision 0.25 est verrouillée sur l'index CUDA 12.6 du workspace.
L'image contient Studio compilé ainsi que les extras d'inférence du workspace.

Préparer les poids côté hôte. Pour le mode natif, les deux scripts suivants
suffisent. Le profil partagé complet utilise aussi les familles OCR, VLM et DFC.

```bash
export MODELS_DIR=/srv/knowledgebase/models
python3 models/download_layout.py --model heron
python3 models/download_table_structure.py
```

Pour lancer l'image directement :

```bash
docker build -f packages/pdfparse/Dockerfile -t knowledgebase/pdfparse:local .
docker run --rm --gpus all -p 127.0.0.1:8000:8000 \
  --mount "type=bind,src=$MODELS_DIR,dst=/models,readonly" \
  --mount "type=bind,src=$DATA_DIR,dst=/data" \
  knowledgebase/pdfparse:local --host 0.0.0.0 --root /data --native
```

Ouvrir `http://localhost:8000`. NVIDIA Container Toolkit doit être configuré
sur l'hôte. Les crops appartiennent aux caches adjacents aux PDF.

### Kernels CUDA et quantification NF4

Qwen3.5-4B utilise NF4 par défaut et nécessite donc un device CUDA. Pour le charger
sans quantification, notamment sur CPU, préciser `"quantization": "none"`. Les autres
modèles conservent leurs paramètres par défaut.

L'extra `cuda-kernels` ajoute `flash-linear-attention[cuda]==0.6.0` et la
[wheel officielle causal-conv1d 1.7.0](https://github.com/Dao-AILab/causal-conv1d/releases/tag/v1.7.0)
pour Linux x86-64, Python 3.12, Torch 2.10 CUDA 12 et ABI C++11. Elle est
verrouillée par URL et SHA-256 dans `uv.lock` et ne nécessite pas de compilateur
CUDA dans l'image. GCC et les en-têtes libc sont inclus pour les petits lanceurs
C compilés par Triton au premier appel. L'extra est limité à cette plateforme ; adapter la wheel
si la version Python/Torch ou l'architecture change. Il est inclus dans le build
Docker. FLA et causal-conv1d sont détectés automatiquement par Transformers au
démarrage ; `attn_implementation="sdpa"` peut rester inchangé. Ces kernels
DeltaNet sont distincts de `flash-attn`.

Après la fin des analyses, reconstruire et recréer uniquement PdfParse :

```bash
docker compose -f compose.dev.yaml up -d --build --no-deps pdfparse
```

Cette commande interrompt les tâches encore en cours lors de la recréation.
Les poids Hub et les résultats complets dans `DATA_DIR` sont conservés.
Vérifier l'installation dans le conteneur :

```bash
docker compose -f compose.dev.yaml exec pdfparse python -c \
  'import causal_conv1d, fla; print("causal_conv1d:", causal_conv1d.__version__); print("FLA:", fla.__version__)'
```

Dans Studio : **Paramètres → Modèles**, sélectionner DFC v1, MinerU OCR et
le VLM `qwen3_5_4b` sur
`cuda:0`, ouvrir **Paramètres du modèle (JSON)** et régler :

```json
{
  "quantization": "nf4",
  "dtype": "bfloat16",
  "batch_size": 1,
  "max_new_tokens": 512,
  "use_cache": true,
  "enable_thinking": false
}
```

Cliquer **Charger et appliquer** une fois la file de traitement vide. NF4 est
appliqué lors du chargement à partir des poids Hub existants, sans nouveau
checkpoint. Il réduit la mémoire des couches quantifiées, avec un possible
changement des sorties ; mesurer le gain sur un document représentatif.
L'OCR peut conserver sa précision habituelle dans un premier temps.
La première inférence FLA peut compiler ses kernels Triton et être plus lente
que les suivantes.

### Python

La préparation hors réseau utilise l’image de build commune et les
[archives Docker du dépôt](../../docker/README.md). Les volumes de modèles et
de PDF restent à monter séparément.

### Mode Workspace

Depuis la racine du workspace :

```bash
python3.12 scripts/docker/python_environment.py prime
python3.12 scripts/docker/python_environment.py dev .venv
.venv/bin/pdfparse-studio
```

Pour le seul ensemble de dépendances du service PdfParse déjà préparé, utiliser
`UV_OFFLINE=1 python3.12 scripts/docker/python_environment.py pdfparse .venvs/pdfparse`.
Le verrou du workspace est le `uv.lock` racine.

Les extras restent sélectionnables pour une installation partielle : `studio`,
`vlm`, `quantization`, `cuda-kernels`, `ocr-transformers`, `ocr-paddle`,
`ocr-rapid` et `layout-lite`. `layout-lite` inclut `ocr-paddle`.
PaddlePaddle GPU et les kernels CUDA sont ciblés sur Linux x86_64 / Python 3.12.
Pour une autre plateforme, installer séparément un runtime Paddle compatible.

## Configurer les étapes

```python
from chonkie import TokenChunker
from pdfparse import ChonkieChunkingModel, NativeLayoutModel
from pdfparse import PdfParseConfiguration, PdfParsePipeline
from pdfparse.enrich import FileSystemAssetStore

pipeline = PdfParsePipeline(PdfParseConfiguration(
    layout=NativeLayoutModel("heron_101"),
    chunking=ChonkieChunkingModel(
        TokenChunker(tokenizer="character", chunk_size=1024, chunk_overlap=128),
        transformation_id="projet.character-1024-overlap-128.v1",
    ),
    asset_store=FileSystemAssetStore("assets"),
))
result = pipeline.run("document.pdf")
markdown = result.document.to_markdown()
texts_for_embeddings = [chunk.contextualized_text for chunk in result.chunks]
pipeline.codec.save(result.document, "enriched.json")
restored = pipeline.codec.load("enriched.json")
```

Le tokenizer `character` compte les caractères ; aucun budget de tokens LLM
n’est implicite. Un backend Chonkie injecté doit conserver les offsets exacts
et reçoit une version de transformation explicite.

`run()` avec le cache actif persiste les figures dans son sous-dossier `assets/`.
Pour les étapes séparées ou `cache=False`, le stockage est désactivé par défaut
(`asset_store=None`) : injecter `FileSystemAssetStore("assets")` pour persister
les figures, ou `MemoryAssetStore()` pour les garder en mémoire. Les wrappers personnalisés
nécessitent un registre explicite pour leur sauvegarde et leur restauration.

Les contrats abstraits sont `BaseLayoutModel`, `BaseEnrichModel`,
`BasePictureEnrichModel` et `BaseChunkingModel`. Les figures classifiées utilisent
`PictureEnrichModel` avec un `DocumentClassifierModel` et des règles injectées
par catégorie native. Les recettes OCR/VLM appartiennent à l’application.

Les étapes sont réutilisables séparément :

```python
source = pipeline.extractor.extract("document.pdf")
enriched = pipeline.enrich_document("document.pdf", source)
chunks = pipeline.chunk_document(enriched)
```

## Modèles et poids locaux

La bibliothèque possède quatre catégories : `layout`, `ocr`,
`picture_classifier` et `vlm`. Les seize entrées du catalogue comprennent six VLM :
Qwen3-VL 4B Instruct, MechVL 4B RL, Qwen3.5 4B, Qwen3.5 9B,
Granite Vision 4.1 4B et PaddleOCR-VL 1.6.
Les cinq OCR sont PaddleOCR-VL 1.6, MinerU2.5-Pro, GOT-OCR 2.0,
PP-OCRv6 Medium Rec et RapidOCR PP-OCRv4. Les ABC accueillent aussi vos implémentations.

```python
from pdfparse import ModelRegistry

models = ModelRegistry()
models.verify("layout", "heron")
layout = models.load("layout", "heron", device="cuda:0")
pipeline = PdfParsePipeline(PdfParseConfiguration(layout=layout))
```

Tous les moteurs utilisent `predict()` et `predict_batch()`, avec des résultats
propres à leur catégorie. Ils reçoivent `model_path`, `device` et `settings`.
Le registre lit les poids dans `MODELS_DIR/<catégorie>/<nom>`. Les sources de
modèles entraînés peuvent être enregistrées avec un chemin dans cette racine.
Le registre réutilise les instances par device et paramètres. `verify()`
contrôle les fichiers requis et calcule leurs SHA-256.

```bash
export MODELS_DIR=/srv/knowledgebase/models
python -m pdfparse.models.download --category layout --model heron
```

Cette commande vérifie les fichiers déjà provisionnés. Les
[scripts de modèles](../../models/README.md) installent les fichiers manquants.

L'identité de provenance (source, commit et SHA-256 des fichiers) est calculée
au chargement, puis portée par l'instance et les résultats de préparation ;
elle ne constitue pas un second catalogue de poids persistant.

```python
models.download("vlm", "qwen3_vl_4b")
vlm = models.load("vlm", "qwen3_vl_4b", device="cuda:0",
                  settings={"batch_size": 1, "max_pixels": 1_048_576, "dtype": "bfloat16"})
description = vlm.predict(crop, prompt="Décris les informations visibles.")
```

Voir [les VLM intégrés](../../ressources/docs/MODELS.md#vlm-intégrés) pour les noms, options et
prompts propres à Granite et PaddleOCR-VL. Les appels restent locaux aux crops.

Les OCR utilisent le même registre et le contrat `predict(crop) -> str` :

```python
models.download("ocr", "mineru2_5_pro_2605")
ocr = models.load("ocr", "mineru2_5_pro_2605", device="cuda:0")
text = ocr.predict(crop)
```

Installer `pdfparse[ocr-transformers]` pour MinerU/GOT, `pdfparse[ocr-paddle]`
(runtime Paddle GPU inclus sur Linux x86_64 / Python 3.12) pour PP-OCRv6, ou `pdfparse[ocr-rapid]` pour
RapidOCR. **PP-OCRv6 Medium Rec reconnaît des lignes isolées ; RapidOCR traite
des crops multilignes.** Voir [les OCR intégrés](../../ressources/docs/MODELS.md#ocr-intégrés).

## Tests

Depuis la racine du dépôt, après installation des dépendances du workspace :

```bash
PYTHONPATH=packages/pdfparse/src:ressources .venv/bin/python -m pytest packages/pdfparse/tests -q
```

La suite standard utilise des substituts déterministes et de vrais PDF sans
OCR ni téléchargement. Les tests de vrais modèles sont activables par
`PDFPARSE_TEST_LAYOUT_MODELS`, `PDFPARSE_TEST_PICTURE_MODELS` ou
`PDFPARSE_TEST_MODEL_INTEGRATION`. Le test OCR expérimental isolé utilise
`PDFPARSE_TEST_RAPIDOCR_MODELS` ; il ne modifie pas l’extraction de production.

Les anciennes pipelines et alias ont été retirés au profit de cette API unique.
Les documents JSON doivent porter le format et la version explicites courants.
Les embeddings et le retrieval sont portés par [KM](../embeddings/README.md)
et son [API](../km-api/README.md) ; la génération est portée par
[l’agent Athena](../km-agent/README.md). Ces responsabilités restent extérieures
à la bibliothèque PdfParse.

### Routage explicite et diagnostic des performances

Dans **Paramètres → Modèles et découpage**, l'OCR des blocs scannés et le VLM
sur les pages ont maintenant leurs propres cases. Ils sont désactivés par défaut.
Pour les figures, choisir **conserver**, **OCR seul**, **VLM seul** ou **OCR puis
VLM**, une règle de repli et un seuil DFC. Les exceptions JSON utilisent les
libellés natifs du classifieur, par exemple :

```json
{
  "bar_chart": "ocr_vlm",
  "flow_chart": "vlm",
  "logo": "keep"
}
```

La règle d'une classe ne s'applique que si le score DFC est strictement supérieur
au seuil. Sinon, la règle de repli s'applique. « Conserver » garde le contenu
natif et le crop, sans appeler OCR/VLM. Un texte déjà extrait dans la figure
n'est pas relu par l'OCR. Le VLM peut aussi être utilisé seul sur les pages, ou
l'OCR seul sur les blocs, sans classifieur de figures.
Les changements de routage modifient l'identité de préparation et donc le cache.

L'API `/api/runtime` accepte par exemple :

```json
{
  "picture_classifier": {"name": "dfc_v1", "device": "cpu"},
  "ocr": {"name": "mineru2_5_pro_2605", "device": "cuda:0"},
  "vlm": {"name": "qwen3_5_4b", "device": "cuda:0"},
  "enrichment": {
    "figures": "ocr_vlm",
    "class_routes": {"logo": "keep"},
    "fallback": "ocr",
    "confidence_threshold": 0.8,
    "text_ocr": false,
    "page_vlm": false
  }
}
```

**Jobs** affiche les durées par étape et les 500 derniers appels des modèles
injectés : modèle, device, dimensions des images, durée et tokens de génération.
L'onglet de diagnostic du résultat expose également les appels de sa préparation.
Les appels des services injectés aux workers Docling sont inclus ; les moteurs
natifs internes de Docling restent comptés dans la durée globale d'extraction.

La VRAM est **échantillonnée toutes les 50 ms**, sans réinitialiser les compteurs
du runtime. Le pic observé peut manquer une allocation plus courte. La colonne
PyTorch décrit les allocations de ce processus, poids résidents inclus ; la
colonne globale décrit l'utilisation du GPU, autres processus et Paddle inclus.
Ces sondes utilisent CUDA quand le contexte PyTorch est déjà initialisé ; CPU,
MPS et un contexte CUDA non initialisé affichent une valeur absente, jamais zéro
inventé. La mémoire GPU partagée Windows/WSL n'est pas mesurée. Les tokens de
sortie proviennent des séquences générées, hors padding ; ils ne sont pas estimés
à partir du nombre de caractères.

Un résultat chargé depuis le cache conserve les mesures de sa préparation
initiale. Le nouveau job n'a aucun appel d'inférence : ses durées et compteurs
permettent de distinguer un cache hit d'une nouvelle analyse.

## Ordonnancement de `pdfparse-run`

Les devices du profil déterminent les files d'inférence : une file par instance
partagée de modèle CPU, et une file commune par device GPU (`cuda` et `cuda:0`
désignent le même device). Les lots regroupent uniquement les requêtes compatibles
d'un même modèle. Un modèle CPU exécute un lot à la fois ; les modèles indépendants
partagent le pool borné `--cpu-workers`, avec `--cpu-threads` threads de calcul
par worker. Les modèles d'un même GPU exécutent leurs appels successivement.

La préparation des entrées Transformers se fait sur CPU pendant la génération du
lot précédent. `--prefetch` borne le nombre de lots en préparation/exécution par
GPU (2 par défaut). Les plafonds globaux `--queue-size` et `--inflight-mib`
restent actifs. Un lot qui atteint son budget de pixels ou de tokens part sans
attendre l'expiration de `--batch-wait-ms`.

```bash
packages/pdfparse/scripts/run-in-compose.sh --workers 4 --prefetch 3 --batch-size 4
```

Le script transmet les options au CLI dans le conteneur. Augmenter le préchargement
peut augmenter la mémoire consommée ; le débit doit être mesuré sur le corpus.

L'arrêt OCR anticipé est vérifié tous les 8 tokens, après un premier contrôle dès
le premier token, pour réduire les décodages CPU dans la génération. La validation
finale de l'alphabet et des répétitions reste systématique. L'arrêt anticipé peut
être retardé de 7 tokens. Cette cadence est un réglage interne d'exécution, absent
des settings sérialisés et du profil : elle ne change ni l'identité du cache des
modèles, ni celle des règles d'enrichissement, ni le profil utilisé pour retrouver
les résultats existants. Les caches antérieurs du profil par défaut sont réutilisables.

Pour les modèles Transformers compatibles, le worker GPU ne conserve que la
génération et le retour des tokens en mémoire CPU. Le décodage final, la validation
OCR et le parsing des tableaux reprennent sur le pool CPU pendant la génération
du lot suivant. Les crédits mémoire/requêtes restent réservés jusqu'à la fin du
post-traitement. Les traces distinguent `generation_seconds` et
`postprocess_seconds`, et conservent un seul enregistrement par lot.

L'enrichissement commence encore après l'extraction complète de chaque PDF.
Ces files ne garantissent pas un taux fixe d'occupation CPU/GPU.

Au redémarrage d'un corpus partiellement traité, le CLI lance le parsing dès le
premier cache manquant et poursuit la vérification des caches en parallèle.
La file d'attente contient au plus `--workers` chemins de PDF ; seuls les workers
actifs construisent leurs pipelines. Les caches continuent à être intégralement
validés. Un corpus entièrement en cache ne charge aucun modèle.

L'enrichissement asynchrone prépare les nœuds ordinaires pendant les inférences
des figures. Le travail ordinaire est découpé en tranches de 64 nœuds pour laisser
passer les préparations de lots GPU prioritaires. L'assemblage final conserve
l'ordre source, vérifie les références et la géométrie, puis valide le graphe.
Le contexte et le hash du PDF sont calculés une seule fois pour cette phase ;
les contrôles du PDF avant publication du cache restent actifs.

### Plafond E5 et provenance des passages

Avec le tokenizer E5 local, Studio et la CLI bornent le plafond demandé à la
capacité de l’encodeur et à `E5_MAX_TOKENS` quand cette variable est renseignée.
Le plafond inclut le préfixe `passage: `, le contexte et les tokens spéciaux.
Utiliser la même valeur pour PdfParse et l’encodeur. Sous Helm, la valeur commune
est `models.e5MaxTokens`.

Le cache rapide inclut l’identité effective du chunker et du tokenizer. Une
ancienne extraction compatible peut être réutilisée pour recalculer ses chunks.
Avec `respect_page_boundaries`, les blocs multipages sont découpés lorsque leurs
offsets de provenance sont alignés sur le texte canonique. Les blocs à provenance
ambiguë conservent leurs pages connues.
