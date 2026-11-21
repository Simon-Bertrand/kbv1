# Développement de PdfParse

## Objectif

Bibliothèque minimale de préparation RAG : PDF → extraction Docling →
enrichissement → chunking Chonkie. Lire [ARCHITECTURE.md](ARCHITECTURE.md) et
[SCHEMAS.md](SCHEMAS.md) avant de modifier un contrat ou un flux.

## Architecture obligatoire

- `PdfParseConfiguration` injecte `layout`, `enrich`, `picture_enrich` et
  `chunking`, ainsi que stockage et codecs si nécessaire. Elle crée l’extracteur
  de production et ne contient aucune orchestration. C’est un `pydantic.BaseModel`
  avec champs figés, types runtime autorisés, défauts validés et extras refusés.
  Utiliser les annotations et `Field`, sans réintroduire de validation manuelle
  équivalente. Les erreurs de configuration sont des `ValidationError`.
- `PdfParsePipeline` est l’unique orchestrateur. `run()` compose directement
  extraction → `enrich_document()` → `chunk_document()`. Les étapes restent
  utilisables et testables séparément. Ne pas réintroduire une seconde pipeline.
- Les composants interchangeables ont des ABC étroites avec des méthodes
  abstraites. Les données sont des dataclasses typées, sans ABC artificielle.
- Le layout utilise `BaseLayoutModel.get_options()`. Les détecteurs restent
  dans `models/layout/`, avec une ABC commune, un fichier par moteur et
  l’adaptateur Docling. `layout=None` conserve Heron natif.
- `BaseEnrichModel` traite les nœuds ordinaires ; `BasePictureEnrichModel`
  traite exclusivement les `PictureItem`. `wrap_document()` effectue le parcours
  et contrôle les résultats. `HandlerEnrichModel` porte les handlers polymorphes.
- `PictureEnrichModel` reçoit un `DocumentClassifierModel` et des routes natives
  vers `BasePictureEnricher`. Les règles reçoivent leurs services par injection.
- `BaseChunkingModel` consomme un document enrichi valide. L’implémentation
  Chonkie préserve références, géométries et versions des transformations.

## Organisation des fichiers

Consulter [l’audit](../../../ressources/docs/FILE_AUDIT.md) avant de créer un module. Les petites
transformations natives restent dans `enrich/handlers.py`, les contrats et
stratégies d’enrichissement dans `enrich/model.py`, les options et la sélection
de layout dans `models/layout/options.py`. Le hook Docling reste avec son
adaptateur. Conserver un fichier par modèle concret ; ne pas recréer les modules
de simple réexport, commandes de renvoi ou anciens chemins internes supprimés.

## Simplicité et efficacité

Composition et injection ; aucun choix de fournisseur dans l’orchestration.
Ne pas ajouter de façade, factory ou registre sans besoin démontré. Les
transformations pures restent des fonctions. Utiliser les Enums pour les choix
fermés, les chaînes pour les catégories natives ouvertes des modèles.

Pas de couches legacy ou d’alias pour conserver une API retirée. Adapter les
appelants, exemples, tests et documents ensemble. Ne pas créer d’abstractions
pour embeddings, retrieval ou génération dans PdfParse : ces responsabilités
sont portées par les packages KM et l’agent.

Une seule extraction, un seul parcours d’enrichissement, un seul crop par
figure, une seule classification. Réutiliser les modèles et convertisseurs.
Figer les registres à la construction ; aucune copie par document. Vérifier
chaque résultat au point où il est produit, puis valider le graphe une seule
fois après enrichissement. Les étapes aval consomment ce document valide.
Éviter les résolutions de références et conversions répétées et les parcours
quadratiques des relations documentaires.

Aucun téléchargement ou chargement de poids à l’import. Le provisionnement est
séparé de l’inférence et partagé entre familles quand leur code est identique.
Les services injectés ne sont pas copiés ; utiliser une instance par worker
si le modèle ne garantit pas les accès concurrents.

## Bibliothèque de modèles

Les catégories sont `layout`, `ocr`, `picture_classifier` et `vlm`. Utiliser
`ModelRegistry` pour inscrire les classes, préparer les poids et charger les
instances ; ne pas créer un deuxième registre ou des téléchargeurs par famille.
Leurs contrats héritent de `BaseInferenceModel`, avec constructeur commun
`model_path`, `device`, `settings` et API `predict` / `predict_batch`.
Un modèle concret implémente l’ABC de sa catégorie ; voir [MODELS.md](../../../ressources/docs/MODELS.md).

Déclarer classe et source une seule fois. Les scripts de `models/` provisionnent
`MODELS_DIR/<catégorie>/<nom>`. Le registre et Docling utilisent ces fichiers
en place. `verify()` contrôle les fichiers requis et calcule leur SHA-256.
Conserver source, révision de référence et empreintes dans la provenance des modèles chargés. Le registre
réutilise les instances par modèle/device/paramètres.
Les runtimes restent propres au worker et ne sont jamais sérialisés.
Un `load()` en cache ne doit pas accéder au disque. Contrôler les modifications
externes avec `verify()` ; invalider les runtimes lorsque la préparation remplace
les poids. Les modèles intégrés valident leurs paramètres via `ModelSettings`
(Pydantic strict, extras refusés) avant toute initialisation du moteur. Les
extensions peuvent déclarer leur propre `settings_model` sans nouvelle factory.

Privilégier `HandlerEnrichModel(handlers={TypeDocling: fonction})` pour remplacer
quelques handlers. Le registre complet est un mécanisme avancé. Exposer les
contrats d’inférence et `NativeLayoutModel` ; garder `DoclingLayoutAdapter` et
`DoclingLayoutOptions` dans leurs sous-modules techniques. Les exemples préparent
uniquement les modèles utilisés ; réserver `download_all()` au besoin explicite.

Transmettre le device demandé au backend, sans fallback silencieux. Les options
Docling doivent réutiliser l’instance layout injectée, l’exclure du JSON et
conserver sa distinction dans le cache. Aucun modèle OCR/VLM par défaut sans
choix explicite de l’application ; les modèles servent aux règles sur les crops,
jamais à activer l’OCR de production sur le PDF entier.

## Invariants de données

- Production : `StandardPdfPipeline`, `do_ocr=False`, crops activés ; aucun
  enrichissement Docling intégré pour contourner les handlers.
- Pas de classification ni d’OCR/VLM implicite. L’OCR explicite reste local au
  crop. Route absente : erreur ou `UnhandledPicturePolicy.KEEP` traçable.
- Conserver modèle, scores natifs, route et version de règle des figures.
- Conserver SHA-256 PDF, références résolubles, parents/enfants, légendes,
  pages et géométries. Un chunk est distinct de son wrapper source, avec un ID
  déterministe et une version de transformation séparée de l’identité PDF.
- Les offsets Chonkie doivent correspondre au texte original. Conserver toutes
  les sources recoupées, y compris en cas de chevauchement. Le tokenizer par
  défaut compte les caractères ; ne pas annoncer un budget de tokens LLM.
- Aucun runtime, modèle, client SDK ou crop PIL dans les données persistées.
  Tout wrapper personnalisé couvre enregistrement et aller-retour JSON.
  Conserver le format versionné courant ; les anciens JSON sans version sont
  refusés. Les changements de schéma versionné exigent une migration explicite.

## Validation

Depuis la racine du dépôt :

```bash
PYTHONPATH=packages/pdfparse/src:ressources .venv/bin/python -m pytest packages/pdfparse/tests -q
```

Tester les contrats utiles : configuration, substitution, ordre et arrêt sur
erreur, cas vide, routage, provenance, JSON, Unicode, répétitions,
chevauchements et sources multipages. Vérifier l’intégration réelle Docling
sans OCR avec un détecteur déterministe. Les tests de vrais modèles nécessitent
une activation explicite ; aucun téléchargement implicite dans les tests.
Les dépendances sont dans `pyproject.toml`, le verrou workspace dans `../../uv.lock` (depuis `packages/pdfparse/`).

## Navigation graphify

Utiliser la compétence graphify pour les questions de codebase et `/graphify`.
Commencer par `graphify query "question"`, puis `path` ou `explain` si utile.
Vérifier le code source avant de modifier. Utiliser le wiki s’il existe et
réserver le rapport complet aux revues larges. Les fichiers de graphe modifiés
sont attendus. Après modification du code : `graphify update .` (AST local).
