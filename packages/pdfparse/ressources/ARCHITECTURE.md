# Architecture de PdfParse

L’API comporte une configuration et un orchestrateur :

```python
from pdfparse import PdfParseConfiguration, PdfParsePipeline

pipeline = PdfParsePipeline(PdfParseConfiguration())
result = pipeline.run("document.pdf")
```

`PdfParsePipeline.run()` exécute directement extraction → enrichissement →
chunking. Il n’existe aucune pipeline intermédiaire. Le résultat
`PdfParseResult` contient `document` et `chunks`. Le [diagramme](SCHEMAS.md)
décrit les contrats et les flux.

## Composants injectés

| Champ de PdfParseConfiguration | Contrat | Implémentation par défaut |
| --- | --- | --- |
| `layout` | `BaseLayoutModel.get_options()` ou `None` | Heron natif Docling |
| `enrich` | `BaseEnrichModel.__call__(NodeItem, ctx)` | `HandlerEnrichModel` |
| `picture_enrich` | `BasePictureEnrichModel.__call__(PictureItem, ctx)` | `WrappingPictureEnrichModel` |
| `page_enrich` | `BasePageEnrichModel.describe(image, markdown=...)` ou `None` | Désactivé ; Studio l’active explicitement avec le VLM sélectionné |
| `chunking` | `BaseChunkingModel.chunk(document)` | `ChonkieChunkingModel` |

`PdfParseConfiguration` hérite de `pydantic.BaseModel`. Sa configuration
`ConfigDict(arbitrary_types_allowed=True, frozen=True, extra="forbid",
validate_default=True)` contrôle les instances des contrats, valide les défauts,
refuse les paramètres inconnus et empêche la réaffectation des champs. Les
entrées invalides lèvent `pydantic.ValidationError`, avec les champs concernés.
Il n’existe plus de validation manuelle `__post_init__`.

La configuration crée l’extracteur de production.
Elle porte aussi `asset_store` (désactivé par défaut) et `wrappers` (codecs intégrés
par défaut). Ces dépendances runtime ne sont jamais persistées. Les services
sont réutilisés ; utiliser une configuration par worker si leur concurrence
n’est pas garantie. L’argument `extractor=` de la pipeline permet les tests et
les extracteurs spécialisés sans ajouter de sélection de fournisseur au métier.

## Extraction et layout

`DoclingPdfExtractor` réutilise un `DocumentConverter` et la
`StandardPdfPipeline`, avec OCR désactivé et génération des crops. Les
enrichissements intégrés et les conversions partielles sont refusés.
Les modèles natifs Docling sont initialisés à la conversion ; ceux du registre
sont chargés explicitement par `load()`. Provisionner leurs poids pour
l’exécution hors réseau.

`text_ocr: BaseOcrModel | None` injecte facultativement un service OCR pour les
blocs textuels scannés. Il accepte les options `DoclingLayoutOptions` et le
layout natif Docling, y compris `layout=None`. Dans ce dernier cas,
`NativeOcrLayoutAdapter` délègue la détection au modèle natif, puis réutilise
le même raccord OCR avant l'assemblage. Le modèle natif et son device restent
ceux de Docling ; aucun détecteur du registre n'est substitué.
Le seuil de l'adaptateur vaut `0.35` par défaut ; avec OCR des blocs, il est
également transmis au filtre de confiance du post-traitement Docling.
L'adaptateur déduplique les prédictions avec le post-traitement Docling, conserve
les zones sans cellules textuelles puis reconnaît chaque crop vide une seule
fois. Il réinsère des cellules `from_ocr` avant l'assemblage et l'ordre de
lecture natifs. Les cellules textuelles existantes ne sont pas remplacées.
Après reconnaissance, les propositions textuelles restées vides sont écartées,
y compris les enfants vides des figures, dont le crop relève du routeur de figures.
Le post-traitement n'est pas répété en aval ; l'OCR global Docling reste
désactivé. Le service est exclu du JSON des options, partagé par référence lors
des copies et distingué dans le cache. Sans injection, le comportement est
inchangé. Aucun runtime ni crop n'entre dans les wrappers persistés.

Un OCR implémentant `BaseSpatialOcrModel` fournit aussi des `OcrRegion`
(texte et boîte TOPLEFT en pixels du crop). L'adaptateur utilise cette capacité
pour les zones `table` et `document_index` vides, puis transmet les cellules
localisées à la structure de tableaux avant assemblage. PaddleOCR-VL 1.6 utilise
sa tâche native `Spotting:` ; les tokens de coordonnées sont conservés au décodage.
Pour les `document_index` entièrement issus de l'OCR, `SpatialTableAdapter`
regroupe les lignes par recouvrement vertical et les colonnes numériques par
alignement de leur bord droit. Les chaînes numériques restent inchangées.
Les autres tableaux conservent TableFormer ; un backend de tableaux explicitement
injecté n'est pas remplacé. Le raccord reste dans le plugin Docling existant,
sans extraction supplémentaire ni nouveau modèle chargé. Sa version figure dans
les options pour distinguer le cache et la provenance de préparation.

`BaseTableOcrModel.predict_table(crop)` fournit directement un `TableData`.
MinerU utilise sa tâche native `Table Recognition:` ; le décodage conserve
les tokens OTSL spéciaux et le parseur OTSL de Docling reconstruit les cellules.
Le service OCR injecté est réutilisé pour les textes, tableaux et figures.
Un tableau scanné vide de cellules natives reçoit un seul crop de reconnaissance
structurée. Les boîtes par cellule restent absentes si le modèle ne les prédit
pas ; la provenance OCR du tableau conserve la boîte réelle du crop.
L'OCR global Docling reste désactivé et les tableaux natifs ne sont pas remplacés.

`NativeLayoutModel("heron_101")` sélectionne un preset natif sans manipulation
des options SDK. Il peut aussi adapter des options explicites au
contrat `BaseLayoutModel`. Ce contrat de configuration est distinct de l’ABC
interne Docling du même nom. Les détecteurs implémentent
`BaseLayoutDetector.predict()` dans `models/layout/`, un fichier par
moteur. `DoclingLayoutAdapter` normalise les détections et laisse l’assemblage à
Docling. Les pages valides du lot Docling sont transmises à `predict_batch()` ;
Heron utilise une seule inférence Transformers par lot. Les autres détecteurs
peuvent conserver le batch séquentiel du contrat commun. Aucun runtime n’est chargé à l’import.
L’adaptateur et `DoclingLayoutOptions` restent dans leurs sous-modules techniques,
hors des exports de la catégorie ; implémenter `BaseLayoutDetector` suffit pour
ajouter un moteur.

## Enrichissement

Le routeur de figures exige un score DFC strictement supérieur à 0,80 pour
choisir la route native. En dessous ou à égalité, il utilise exclusivement
le `fallback` injecté ; sans celui-ci, la politique des figures non traitées
s'applique. Studio permet de choisir le mode par défaut, les exceptions par classe et le repli : conservation, OCR seul, VLM seul ou OCR → VLM sur le crop entier. L’OCR des blocs scannés et le VLM de page sont des activations indépendantes, désactivées par défaut.
Scores, seuil et décision sont conservés dans les métadonnées, sans commenter
le classement dans le contenu Markdown.

`pipeline.enrich_document(path, source)` appelle la transformation
`wrap_document()` sur un document Docling déjà extrait. Le parcours inclut body,
furniture, groupes et descendants des figures. Chaque nœud est traité une fois :

- `PictureItem` → `picture_enrich` ;
- tout autre `NodeItem` → `enrich`.

`HandlerEnrichModel` utilise un registre polymorphe figé à sa construction, sans
copie par document. Son argument `handlers={TypeDocling: fonction}` remplace les
handlers correspondants et conserve les autres défauts. Un registre complet
peut aussi être injecté explicitement. Les résultats sont vérifiés à un seul endroit : type,
identité PDF, référence source, conservation exacte des géométries, des parents
et des enfants dans leur ordre source, et enregistrement du wrapper. Le graphe complet
est validé une fois en sortie. Les relations parent/enfant utilisent des ensembles
pour éviter une recherche linéaire parmi les frères de chaque nœud.

Les handlers natifs produisent des tableaux orientés recherche (valeurs
préfixées par les en-têtes de colonnes) tout en conservant le Markdown et les
cellules. Les formulaires et régions clé-valeur deviennent indexables à partir
des relations explicites de leur graphe, avec références et géométries des
cellules. Les en-têtes/pieds de page restent restituables mais non indexables.

`PictureEnrichModel` injecte un `DocumentClassifierModel` et des routes par
catégorie native vers `BasePictureEnricher`. Une règle reçoit crop et
classification, légende source `caption` et `source_text` lorsqu'une transcription appartient déjà aux
nœuds de la figure ; elle retourne `PictureEnrichment` et conserve sa version. Le même
crop sert à la classification, à la règle et au stockage. OCR/VLM restent des
services explicitement injectés dans les règles ; aucun enrichissement
sémantique n’est activé par défaut. Une route absente échoue ou conserve le
résultat via `UnhandledPicturePolicy.KEEP`. Les erreurs se propagent.

`OcrVlmPictureEnricher` est une règle optionnelle avec OCR et VLM injectés :
transcription du crop, puis description du même crop avec la transcription
identifiée comme texte OCR dans le prompt. OCR et description restent séparés ;
la catégorie et la version de règle sont conservées. Un échec OCR empêche
l’appel VLM. Le prompt de tâche et la version sont explicitement fournis.
Si `source_text` existe, la règle le réutilise pour le VLM et ne lance pas
d'OCR : le texte reste produit uniquement par ses nœuds sources, la figure
n'ajoutant que sa description et sa légende. Les légendes seules n'empêchent
pas l'OCR. Cette propriété est décidée à l'enrichissement, avant le texte
canonique et le chunking ; aucun filtrage des chunks par leur contenu.
Le prompt minimise le nombre de phrases pour les figures simples, généralement
cinq ou moins, et autorise davantage pour les figures complexes et très détaillées.
La réponse est conservée sans troncature après génération ; aucune
heuristique de taille de crop ou de longueur OCR ne les supprime. Studio
construit ses pipelines avec un stockage disque partagé des PNG extraits,
par défaut sous `packages/pdfparse/.assets`, configurable via `--assets-dir` ou
`PDFPARSE_ASSETS_DIR`. Il reste stable entre chargement, déchargement et
changement de chunking. Une pipeline explicitement injectée conserve ses choix.

## Chunking et provenance

`pipeline.chunk_document(document)` consomme un document enrichi valide.
`ChonkieChunkingModel` concatène une seule fois les représentations d’embedding,
séparées par deux sauts de ligne, puis appelle le backend Chonkie. Ses offsets
exacts identifient les éléments recoupés, sans ambiguïté sur les textes répétés.
Les chevauchements et les chunks traversant plusieurs éléments/pages conservent
toutes leurs références et géométries. Ces dernières sont celles des éléments
complets ; aucune précision au mot n’est inventée.

Les légendes sont rattachées à leur propriétaire indexable par leurs références,
sans réindexer leur nœud autonome. Les cellules déjà représentées dans un
formulaire ne sont pas réindexées séparément. Les wrappers et toutes leurs
sources sont conservés. `Chunk.text` reste l’extrait exact de Chonkie ;
`Chunk.context` ajoute les titres du document et les sections ancêtres communs
qui ne figurent pas déjà intégralement dans cet extrait. Utiliser
`Chunk.contextualized_text` pour la recherche ou le LLM. Les références,
géométries et identifiants incluent ce contexte ; il s’ajoute au budget du backend.

Une légende est considérée déjà présente uniquement comme une séquence complète
de lignes : « Figure 1 » ne disparaît pas devant « Figure 10 ». Cette règle
s’applique au Markdown et à la recherche. La transformation Chonkie native est
versionnée `context.v7`, incluant les coordonnées et métadonnées structurelles décrites dans le
guide d’enrichissement ; les backends injectés doivent faire évoluer leur
`transformation_id` lors de la réindexation avec ces règles.

Le défaut est `RecursiveChunker(tokenizer="character", chunk_size=512)`, sans
téléchargement de tokenizer. Le budget compte des caractères. Tout backend
Chonkie injecté nécessite un `transformation_id` explicite et doit restituer le
texte original avec des offsets exacts. Les identifiants de chunks incluent
source, version, positions, références et texte. La pipeline vérifie unicité,
identité, références résolubles et conservation des géométries. Elle ne refait
pas la validation du graphe déjà effectuée par l’enrichissement. Les géométries
résolues sont réutilisées entre chunks ; celles des cellules de formulaire
sont également contrôlées. `document.to_markdown()` et le chunking partagent
la sélection native des contenus et légendes, avec des représentations
distinctes pour lecture et recherche (voir [enrichissement](../../../ressources/docs/ENRICHMENT.md)).

Les chunks de toute stratégie doivent contenir du texte non blanc, des IDs et
versions non blancs, un contexte textuel et des références sans doublons.
Leurs géométries doivent correspondre exactement à l’union de celles des
sources déclarées : une page supplémentaire non sourcée est refusée.
Les offsets Chonkie sont des entiers stricts (les booléens sont refusés).

`ingestion/text.py` prépare les coordonnées canoniques et construit les chunks
avec leurs sources. Chonkie l’utilise après son unique appel au backend.
`StructuralChunkingModel` est une autre implémentation injectable : elle regroupe
les unités documentaires dans un budget incluant le contexte, fractionne les
grands tableaux par lignes et trace les replis sur les unités trop longues.
Un compteur de tokens local peut être injecté avec une identité explicite.

`PdfParseResult.quality_report()` mesure la couverture exacte, les pages sans
texte indexable et les anomalies candidates. `index_records()` / `export_jsonl()`
produisent un format d’indexation versionné avec empreintes séparées du contenu
et du record. Ces fonctions pures et l’écriture atomique restent dans
`ingestion/indexing.py`, sans moteur de recherche ni accès réseau. `run()` capture
l’inventaire des pages de l’extraction existante. Aucun champ n’est ajouté au
JSON versionné du document enrichi. Voir [le guide pré-RAG](../../../ressources/docs/PRERAG.md).

## Assemblage du contexte des hits

`PdfParseResult.source_context(chunk_ids, max_characters=12000)` délègue à la
transformation pure `ingestion/context.py`. Elle produit un `SourceContext` :
texte, identité PDF, IDs des hits dédupliqués, références, géométries et indicateur
`expanded`. Les hits doivent appartenir au résultat ; un ID inconnu échoue.
La bibliothèque ne choisit pas les hits et ne fait aucune recherche.

Les blocs complets sont restitués dans l'ordre documentaire lorsqu'ils tiennent.
Sinon les chevauchements sont fusionnés par offsets canoniques exacts, puis les
hits sont agrandis progressivement dans l'ordre de pertinence fourni par
l'appelant. Aucun hit n'est supprimé pour tenir dans le budget ; si les extraits
minimaux dépassent celui-ci, l'appel échoue. Des textes identiques à des positions
différentes restent indépendants. Les géométries restent celles des sources.

La sortie conserve les régions du layout final disponibles, les origines de
leurs cellules, un manifeste déterministe et les timings des trois étapes.
`prepare_index()` crée un snapshot immuable sans cache implicite du document
mutable. L'évaluation pure prend des annotations et, facultativement, des
classements externes. Les détails et limites figurent dans [PRERAG.md](../../../ressources/docs/PRERAG.md).

## Données et persistance

`EnrichedDoclingDocument` désigne les données `EnrichedDocument`, sans conversion
supplémentaire. Les wrappers conservent SHA-256 du PDF, relations, légendes,
pages et positions. Les clients SDK, modèles et images PIL restent dans les
services et le contexte temporaire. `FileSystemAssetStore` persiste les images ;
`MemoryAssetStore` nécessite de conserver l’instance pour les relire.

`pipeline.codec` sauvegarde et restaure les documents et les wrappers
personnalisés. Le format courant `pdfparse.enriched_document`, version 1, reste
inchangé. Les wrappers `pdfparse.form` et `pdfparse.key_value` utilisent la version 2,
avec migration explicite depuis v1 pour reconstruire texte, références et
géométries des cellules à partir du graphe. Les
anciens JSON sans version ne sont plus acceptés. Le décodage refuse les valeurs
non persistables, y compris après migration ou codec personnalisé, et impose
un même chemin PDF au document et à ses éléments. Les codecs intégrés figés
sont construits une seule fois à la première utilisation ; les registres
personnalisables restent indépendants.

Les anciennes classes `IngestionPipeline`, `EnrichmentPipeline`,
`ElementChunker`, le routeur global et les alias historiques ont été retirés.
Les tests et exemples utilisent l’API unique. Les quatre catégories partagent un registre, une interface de construction
et un provisionnement commun des poids ; voir ci-dessous.

Embeddings, retrieval et génération restent hors périmètre. Une abstraction
supplémentaire exige une capacité réellement interchangeable et un usage concret.

## Organisation des fichiers

[L’audit fichier par fichier](../../../ressources/docs/FILE_AUDIT.md) justifie les fichiers Python
restants et documente les 12 anciens modules regroupés ou supprimés. Les handlers
natifs sont réunis dans `enrich/handlers.py`, leurs contrats et stratégies par
défaut dans `enrich/model.py`. La configuration du layout est réunie dans
`models/layout/options.py` ; son adaptateur enregistre lui-même le plugin Docling.
Un fichier reste réservé à chaque modèle concret pour préserver sa substitution.

## Registre de modèles par catégorie

`ModelRegistry` centralise `layout`, `ocr`, `picture_classifier` et `vlm`.
Les contrats d’inférence héritent de `BaseInferenceModel` : mêmes arguments
`model_path`, `device`, `settings` et mêmes méthodes publiques `predict` /
`predict_batch`. Les sorties restent typées par catégorie ; la classification
implémente `_predict_batch` pour conserver un batch natif validé.

Les catalogues intégrés déclarent classe et `ModelSource` une seule fois.
`ModelRegistry` sélectionne les classes et réutilise les runtimes ; Hugging Face
possède seul le stockage disque, ses snapshots et ses verrous de téléchargement.
`download()` prépare explicitement les snapshots aux commits figés et réutilise
sans HTTP un snapshot complet. `load()` lit directement ces fichiers hors réseau,
sans manifeste ni répertoire de poids supplémentaire. Les sources locales
explicites sont utilisées sur place et ne sont pas copiées.
`verify()` compare les fichiers aux empreintes natives des blobs (SHA-256 LFS,
SHA-1 Git). L'identité de provenance est calculée au premier chargement et reste
portée par les résultats ; les runtimes ne sont jamais sérialisés.
Le LRU est borné par `max_instances` (4 par défaut), sans détruire les références
encore empruntées. Les préparations invalidant les fichiers invalident les
instances concernées ; les changements externes se contrôlent par `verify()`.
Un `load()` déjà en mémoire n'effectue aucun accès disque ni réseau.
Docker monte le Hub local ; aucun volume de poids PdfParse distinct n'est créé.
Les devices sont validés syntaxiquement ; le backend vérifie leur disponibilité.
Les moteurs intégrés déclarent un `settings_model` Pydantic, dérivé de
`ModelSettings` : extras refusés, valeurs strictes, défauts validés. La validation
précède la lecture des poids et l’import du moteur ; les options normalisées
forment la clé de cache. Les extensions peuvent déclarer leur propre schéma
sans changer la configuration ni le registre.

Un `BaseLayoutDetector` implémente aussi le port `BaseLayoutModel.get_options()` :
il peut être injecté directement dans `PdfParseConfiguration.layout`.
L’adaptateur Docling réutilise le runtime exact. Les options sérialisées excluent
cette instance et portent une clé de cache par instance ; une copie des options
ne copie jamais les poids. `NativeLayoutModel` reste l’adaptateur des presets
natifs, et `layout=None` conserve Heron natif Docling.

La catégorie OCR expose `BaseOcrModel` et quatre moteurs : MinerU2.5-Pro-2605,
GOT-OCR 2.0, PP-OCRv6 Medium Rec (lignes isolées) et RapidOCR PP-OCRv4
(détection et reconnaissance multiligne). MinerU reconnaît uniquement le crop
avec son prompt natif ; son parseur PDF n’est pas utilisé. MinerU, GOT et les
VLM partagent `models/transformers.py` pour charger les poids locaux et générer
du texte par lots. Leurs stratégies de prétraitement restent dans leur catégorie.
Paddle utilise son batch natif ; RapidOCR réutilise deux sessions ONNX vérifiées,
sans modèle d’orientation ni fallback de device. La catégorie VLM expose
`BaseVlmModel` et quatre moteurs : Qwen3.5 4B/9B, Granite Vision 4.1 4B et
PaddleOCR-VL 1.6. Leurs quatre classes réutilisent une inférence Transformers
native avec lots bornés et prompt commun au lot. Les paramètres sont validés
avant toute initialisation du runtime ; Qwen désactive le raisonnement par défaut.
Sources, shards et fichiers des processeurs/tokenizers sont déclarés une seule
fois dans le catalogue VLM, à des révisions immuables. Aucun moteur sémantique
n’est préchargé ni sélectionné implicitement. Une application peut aussi
enregistrer son modèle sans modifier la pipeline.
[MODELS.md](../../../ressources/docs/MODELS.md) détaille l’API et le cycle de vie.

## Application locale FastAPI et frontend

`backend/app.py` fournit une application FastAPI avec pipeline injectable,
exploration bornée à un dossier racine, lecture des PDF, statuts et résultats.
Un worker unique appelle directement `PdfParsePipeline.run()` ; il ne crée
aucune seconde pipeline. Les résultats projetés sont gardés dans un cache LRU
borné, invalidé si le fichier ou la configuration change. Les PDF restent en
lecture seule. PDF.js lit et affiche directement les pages dans le navigateur,
avec défilement continu et rendu des seules pages proches de la zone visible.
Il n’y a plus de seconde lecture des métadonnées par le backend avec pypdf.

`backend/schemas.py` définit les contrats HTTP, `presentation.py` les projections
durables (éléments, Markdown, chunks et options effectives). La génération
OpenAPI n’instancie ni pipeline ni modèle. Les objets runtime sont décrits par
leurs classes et réglages autorisés, sans sérialiser leurs moteurs.

`front/` utilise React, PDF.js et un client Fetch/SDK TypeScript généré avec
Hey API depuis `app.openapi()`. La compilation régénère ce client et vérifie
les types. Le viewer charge le document courant, gère le worker PDF.js et
surligne les géométries sources. Les polices sont locales. FastAPI sert le
frontend compilé sur la même origine ; Vite relaie `/api` en développement.
Le [guide Studio](../../../ressources/docs/STUDIO.md) décrit les commandes et les limites locales.

`progress.py` définit des événements optionnels via un callback dans une portée
`ContextVar`. L'orchestrateur et les services de figures émettent leurs étapes,
sans connaître FastAPI ; aucun callback ne figure dans les documents persistés.
Le backend conserve un historique borné de jobs en mémoire et expose
`GET /api/jobs` au client généré. Le profil CLI initial charge ses modèles sur
le worker après démarrage HTTP, pour rendre l'UI accessible pendant ce travail.
Un échec laisse les API de configuration et de jobs accessibles. L'OCR des
blocs textuels internes à Docling reste inclus dans l'étape extraction/layout.

Studio peut composer explicitement son pipeline au lancement avec les options
`--picture-classifier`, `--ocr` et `--vlm` (requises ensemble), plus `--layout`
si souhaité. Le registre commun charge uniquement les poids locaux préparés.
Les règles OCR → VLM partagent leurs modèles ; le VLM décrit l'image sans
recevoir la catégorie DFC. Sans sélection de modèles, la CLI Studio charge le
profil applicatif Heron et DFC v2.5 sur CPU, MinerU2.5-Pro-2605 pour les
blocs scannés et Qwen3.5-4B NF4 sur CUDA pour les figures, sans description
de pages entières. `backend/defaults.py` définit ce profil partagé ;
`GET /api/runtime/options` le transmet à l’UI. `--native` conserve le wrapping natif. Cette sélection par
défaut appartient à l'application ; les défauts de la bibliothèque et de
`create_app(pipeline=...)` restent inchangés. Les réponses HTTP exposent des notices lorsqu’une figure reste sans
traitement sémantique ou lorsque Markdown/chunks sont vides ; ces diagnostics
ne modifient pas le document persistant.

La modale propose aussi la composition à chaud. `backend/runtime.py` partage
avec la CLI la construction de `PdfParseConfiguration` et des routes natives,
sans traiter de document. Chaque modèle a son device et ses paramètres validés.
Le registre existant reste l'unique propriétaire du cache de modèles. Le même
worker sérialise analyse, chargement et déchargement ; aucun changement n'est
accepté tant que des PDF sont en attente ou en cours. Le changement libère
l'ancienne pipeline et le cache du registre avant de charger les nouveaux poids,
puis invalide tous les résultats. Un changement limité au chunking réutilise
l'extracteur et les services injectés : trois stratégies natives Chonkie
(taille fixe, phrases, récursif), sans embeddings. Leurs options HTTP strictes
sélectionnent un backend injecté dans le même `ChonkieChunkingModel`, avec une
version de transformation distincte par stratégie et paramètres. Le
déchargement conserve le chunking. Un échec libère les chargements partiels et
reste visible ; aucune analyse n'est permise avant un nouveau chargement ou un
déchargement explicite. Les poids restent sur disque, sans téléchargement
implicite. Les objets effectifs gardent leur vue en lecture seule.


### Projections multimodales et coût du chunking

Le snapshot d'index partage texte canonique, propriétaires des figures et
provenance entre diagnostics, records textuels v4 et inventaire visuel v1.
Les figures sans texte ont leur propre record ; aucune seconde orchestration
ni moteur d'embedding n'est ajouté. La provenance des services OCR/VLM/DFC est
capturée au moment de l'enrichissement, puis projetée avec les preuves layout.

Le chunker structurel mesure le texte contextualisé candidat avant de construire
un chunk. Hashes, références et géométries sont calculés pour les chunks émis.
`respect_page_boundaries` est une politique injectable, pas une fusion ou un
filtrage a posteriori. Les offsets et références originales restent inchangés.

## Cache des résultats

`run()` consulte le cache adjacent `<PDF>.pdfparsecache/<hash_contenu_et_configuration>`
avant les trois étapes. En cas d’absence ou d’invalidité, il compose les mêmes
étapes puis publie le résultat complet. `ingestion/cache.py` assure uniquement
la persistance, les empreintes et le verrou interprocessus ; il n’orchestre
aucune inférence. `cache=False` désactive ce mécanisme. Le stockage injecté
s’applique alors normalement ; avec le cache activé, les crops vont dans son
sous-dossier `assets/`. Les composants personnalisés déclarent leurs options
et versions dans `cache_identity`. La restauration valide le graphe et les
références des chunks sans charger de runtime depuis les données.

La clé combine SHA-256 du PDF et configuration, y compris les empreintes des
poids natifs Docling préparés. Un verrou commun par PDF couvre lecture,
écriture et rétention de toutes ses configurations. La rétention est bornée
en nombre, octets (crops compris) et âge ; la purge supprime toute l'entrée,
assets inclus. Le prochain traitement réextrait les crops. Les projections
JSONL/Markdown sont des artefacts de préparation, sans publication RAG :
celle-ci viendra après validation manuelle, dans une étape distincte.
Le manifeste v2 vérifie séparément les métadonnées et les crops. Les projections
sont restaurées sans reconstruction pour la réponse HTTP, elle-même bornée en
octets et invalidée si son entrée disque a été purgée.


## Mesures des services injectés

`BaseInferenceModel` instrumente les points d'entrée `predict`, `predict_batch`,
`predict_regions` et `predict_table` uniquement lorsqu'une portée d'observation
est active. La délégation imbriquée d'un même appel ne produit pas de doublons.
`PdfParsePipeline.run()` conserve les traces sérialisables dans `StageMetrics` ;
les jobs HTTP en conservent au plus les 500 dernières et comptent tous les appels.
Aucune image, modèle ou tenseur n'est retenu dans une trace. Les séquences de
Transformers fournissent les nombres de tokens réels hors padding.

Le raccord Docling propage temporairement les observateurs aux services injectés
des workers, puis les retire, y compris en cas d'erreur. Comme pour les modèles,
un extracteur/pipeline appartient à un worker et n'est pas partagé entre
conversions concurrentes. Les moteurs internes natifs de Docling restent mesurés
par la durée de l'étape d'extraction. Les durées de job se rapportent aux étapes
observées ; les durées d'appels qui se chevauchent ne s'additionnent pas en un
temps mural total.

La mémoire CUDA est échantillonnée toutes les 50 ms sur un contexte PyTorch déjà
initialisé, sans réinitialiser les compteurs globaux. Les pics observés ne sont
pas des pics garantis à la microseconde et la mémoire partagée Windows n'est pas
incluse. Les appels ne sont pas ralentis pour charger un contexte CUDA de mesure.
Le cache conserve les métriques de la préparation initiale ; le job d'un cache
hit n'invente aucune nouvelle inférence.
