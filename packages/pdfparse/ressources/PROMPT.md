Spécifications du projet :


PdfParse est une librairie de haut niveau, configurable pour le début d'une chaine de traitement RAG : extraction, enrichissement et chunking.

L'objectif est d'avoir une classe PdfParsePipeline qui prend en argument une classe PdfParseConfiguration

L'object PdfParseConfiguration doit prendre en argument ces différents objets : 
- layout : BaseLayoutModel | None 
    Cet objet définit le modèle de Layout appliqué par la pipeline Docling
- enrich : BaseEnrichModel 
    Cet objet définit les différents méthodes "handlers" qui définissent comment chaque Docling NodeItem est augmenté/enrichi par la pipeline sauf l'object PictureItem qui sera traité séparémment
- picture_enrich : BasePictureEnrichModel qui prend en argument un DocumentClassifierModel et qui définit toutes les méthodes "handlers" pour chaque type de classe que retourne le modèle DocumentClassifierModel lorsqu'il est appliqué à une image extraite par la pipeline Docling
- chunking : BaseChunkingModel qui définit comment les chunks sont calculés avec Chonxie à partir du EnrichedDoclingDocument

L'objectif est d'avoir une librairie avec peu de code, un code efficace et optimisée, lisible et dont la chaîne de traitement est facilement paramétrable.
Le code doit être bien organisé, peu redondant, avec tout le code complexe et inutile supprimé. Adapte le AGENTS.MD (réécrit le entièrement) et met à jour SCHEMAS.md pour que le schéma de la PdfParsePipeline soit correctement illustré en schéma bloc.

Respecte le principe de la POO, des classes abstraite pour chaque layout, utilise des Enums quand nécessaires et fait en sorte que tout soit très proprement codé comme en entreprise logiciel de haut niveau.



___ 
Reformulation améliorée du prompt : 
L'architecture doit reposer sur deux classes principales :

PdfParsePipeline : orchestre les traitements.

PdfParseConfiguration : injecte les composants interchangeables suivants :

layout: BaseLayoutModel | None : modèle de layout utilisé par Docling.

enrich: BaseEnrichModel : handlers d'enrichissement des NodeItem Docling, hors PictureItem.

picture_enrich: BasePictureEnrichModel : utilise un DocumentClassifierModel pour classifier les images et déléguer leur enrichissement à un handler spécialisé par classe.

chunking: BaseChunkingModel : stratégie de chunking basée sur Chonkie, appliquée à l'EnrichedDoclingDocument.

Exigences

Privilégier la composition, l'injection de dépendances et des interfaces minimales pour rendre chaque composant remplaçable.

Conserver les fonctionnalités existantes pertinentes et simplifier radicalement l'implémentation : supprimer les abstractions superflues, duplications et codes obsolètes.

Optimiser les traitements et éviter les extractions, classifications et conversions redondantes.

Garantir un code Python typé, lisible, maintenable et testable, avec une API publique simple et stable.

Préserver les métadonnées et les relations entre éléments, images, pages et chunks.

Documentation et validation

Réécrire entièrement AGENTS.md pour documenter les conventions, l'architecture et les règles de développement.

Mettre à jour SCHEMAS.md avec un diagramme bloc Mermaid détaillant les composants, leurs interfaces et les flux de données.

Adapter les tests existants et ajouter les tests essentiels de configuration, d'orchestration et d'interchangeabilité.

Principe directeur : minimiser la complexité et la quantité de code sans sacrifier les performances, l'extensibilité ou la qualité des données produites. Ne pas introduire de framework ou de couche d'abstraction sans nécessité démontrée.