# Objectif du projet KM

Créer un template professionnel d’interface de chatbot LLM/RAG, accompagné d’une bibliothèque de composants UI réutilisables écrite en React/Next.js, fondée sur HTML sémantique et CSS natif. Le résultat doit être moderne, sobre, responsive, accessible et simple à adapter à différentes identités visuelles et à différents fournisseurs de modèles.

## Direction visuelle

- Nom du projet et de l’assistant : **KM**.
- Utiliser des libellés courts, concrets et orientés vers les tâches. Éviter les slogans, les phrases philosophiques et les formulations abstraites. Privilégier l’ergonomie et l’efficacité.

- Privilégier une composition soignée : hiérarchie typographique claire, espacements cohérents, densité maîtrisée et états interactifs aboutis.
- Éviter les clichés visuels des interfaces IA : bleu par défaut, dégradés bleu/violet, halos, effets décoratifs gratuits et accumulation de cartes arrondies.
- Proposer une palette neutre avec un accent intentionnel et interchangeable. La sobriété ne doit pas rendre les contrôles ou les états difficiles à distinguer.
- L’accent validé est un rouge franc moderne en aplats (inspiration flat colors), à la place du bordeaux initial. Définir ses variantes clair/sombre dans `theme.css` et préserver les contrastes.
- Prévoir des thèmes clair et sombre avec des contrastes lisibles.

## Bibliothèque UI et thèmes

- Utiliser **Source Sans 3**, libre sous SIL OFL 1.1, pour les textes, les titres et les contrôles. Réserver **JetBrainsMono Nerd Font** au code. Servir les fontes WOFF2 localement, conserver leurs licences et charger les glyphes Nerd séparément à la demande. Pour les messages, utiliser une taille de 16 px et un interligne de 1,65.

- Séparer les fondations visuelles, les composants UI, les compositions du chat et la logique métier.
- Centraliser les valeurs visuelles dans des tokens CSS : couleurs, typographie, espacements, rayons, bordures, ombres et mouvements.
- Définir dans un fichier dédié `theme.css` les couleurs principales et les tokens sémantiques consommés par les classes des composants. Éviter les couleurs codées en dur dans les styles des composants.
- Fournir dès le départ deux thèmes complets : `light` (clair) et `dark` (sombre). Ils exposent les mêmes tokens et couvrent les surfaces, textes, bordures, accents et états interactifs.
- Piloter le thème par un attribut `data-theme="light"` ou `data-theme="dark"` sur l’élément racine. Respecter la préférence système par défaut et permettre un choix explicite persistant.
- Utiliser des tokens sémantiques, par exemple `--color-surface`, `--color-text`, `--color-accent` et `--color-border`. Un changement de thème doit se faire dans les tokens, sans modifier chaque composant.
- Vérifier les éléments et API standards avant de coder une interaction. Utiliser les dialogues natifs (y compris le tiroir mobile), les formulaires de fermeture, les commandes HTML et les actions déclaratives de popover lorsqu’ils couvrent le besoin. Préserver les replis ciblés et les tests de comportement ; ne pas recréer le focus modal, la validation native ou la mesure de champs en JavaScript.
- Préférer des éléments HTML natifs et une API de classes, attributs et variantes cohérente. Les styles restent indépendants de React et de Next.js.
- Documenter les composants, leurs variantes, leurs états et leur usage dans un catalogue consultable.
- Maintenir `src/css/index.css` comme point d’entrée public CSS, sans styles applicatifs ni dépendance JavaScript. Garder les fontes optionnelles. `npm run build:ui` doit produire une distribution autonome aux URL de fontes relatives, avec leurs licences ; documenter le contrat dans `docs/design-system.md`.
- Utiliser le composant React `Dialog` avec l’élément HTML natif `<dialog>` (pas `<modal>`). Fournir les notifications via `NotificationProvider` et `useNotifications`, sans dépendance réseau. Nettoyer abonnements, timers et lecteurs PDF dans les effets React.
- Centraliser tous les textes statiques affichés et accessibles dans `src/content/fr.json`, y compris les notifications, les placeholders, les métadonnées HTML et les contenus fictifs. Réutiliser les clés communes ; ne pas mettre de HTML de composition dans le JSON. Utiliser `formatText()` pour les paramètres nommés et échapper le texte à la frontière HTML. Préserver les exports `texts`/`texts/fr.json`, sans accès DOM ni réseau. Les identifiants techniques et les contenus utilisateur/backend restent distincts des libellés.
- Mutualiser les dimensions et variantes dans les tokens CSS. Dériver les états visuels des attributs natifs existants, sans synchroniser plusieurs classes ou attributs `hidden` pour une même information. Conserver les attributs `disabled` fonctionnels et laisser les commandes nécessaires, comme « Arrêter », actives.
- Garder le code lisible, factorisé et peu redondant. Ajouter une abstraction lorsqu’elle répond à une réutilisation réelle.

## Réutilisation et migration vers des frameworks

- Le livrable est un template réutilisable, pas une application complète de gestion des conversations. Ne pas ajouter renommer, supprimer, archiver ou d’autres workflows métier sans demande explicite.
- Préserver les exports publics documentés dans `docs/framework-integration.md` : entrée UI pure utilisable au serveur, entrée navigateur explicite, contrats TypeScript et styles séparés. Aucun accès DOM/stockage à l’import de l’entrée serveur.
- Le projet est désormais un template Next.js App Router et une bibliothèque de composants React. React possède le DOM, les événements et l’état UI ; garder le modèle métier, les contrats, les textes et les styles indépendants du framework.
- Fournir de vrais composants React et des hooks avec nettoyage au démontage. Tester les exports et le rendu SSR sans DOM ; fournir les types et un manifeste `dist-ui/` avec React et ReactDOM en peer dependencies. Les anciens exports de contrôleurs DOM `browser` et `interactions` sont retirés.

## Stores et rendu React

- Utiliser les stores `getState`/`subscribe`/`dispatch` et les reducers purs existants ; préserver les références des branches inchangées. Ne jamais muter un snapshot ou une charge utile après dispatch.
- Transmettre des instances stables dans les contextes. Utiliser `useUiSelector`, `useChatSelector` et les actions stables ; réserver `useReno()`/`useChat()` aux usages de compatibilité qui nécessitent tout l’état.
- Garder transport, persistance, timers et UUID en dehors des reducers. Les providers instancient les stores par arbre de rendu, sans singleton partagé entre requêtes. Maintenir un snapshot initial stable pour le SSR et restaurer les préférences dans les effets.
- Conserver l’entrée `store` pure sans React/DOM/stockage. Vérifier les abonnements ciblés, l’immuabilité, l’isolation SSR et le nettoyage. Documenter l’API dans `docs/state-management.md`.

## Client UI autonome

- Garder `src/store/client/` indépendant de React, du DOM et du modèle de chat. `createUiClient()` fonctionne seul ; `UiClientProvider` reçoit l’instance et relie les composants React au store. Les fonctionnalités RAG se branchent via l’adaptateur optionnel `src/components/chat/ui-client-adapter.ts`.
- Produire une seule distribution `dist-ui/` via le pipeline commun de bibliothèque. Préserver les entrées séparées `client`, `react`, `store`, les composants et les styles. Garder le contrôleur sans dépendance runtime à React ; les pairs React/ReactDOM sont facultatifs pour les consommateurs du contrôleur. Vérifier les frontières d’import, l’interopérabilité, le SSR et le nettoyage.
- Séparer les responsabilités : `src/css/` pour les styles, `src/store/` pour l’état, les reducers, les dispatchs et le client autonome, `src/backend/` pour les fonctions serveur, `src/components/` pour le rendu React et ses providers/hooks. `src/app/` reste réservé aux routes Next.js et `src/content/` aux textes centralisés.
- Valider les commandes du backend et leurs cibles avant application, regrouper les notifications des stores et annuler les transports obsolètes. Documenter l’API dans `docs/ui-client.md`.

## Architecture et arborescence

- Organiser le code autour de `src/css/`, `src/store/`, `src/backend/` et `src/components/`. Conserver `src/app/` pour les conventions Next.js et `src/content/` pour les textes. Tous les styles CSS résident dans `src/css/`. Les composants et stores ne doivent jamais importer de fonctions `src/backend/` ; les routes API délèguent leurs traitements à ce dossier.


- Exiger une arborescence exemplaire, claire et maintenable, au niveau des meilleurs projets professionnels. L’organisation du dépôt fait partie des critères de qualité du livrable.
- Donner à chaque dossier une responsabilité précise et à chaque fichier un nom explicite. Adopter des conventions de nommage cohérentes dans tout le projet.
- Séparer clairement les fondations et thèmes CSS, les composants UI réutilisables, les compositions du chatbot, la logique applicative, les intégrations et les données de démonstration.
- Garder la bibliothèque UI indépendante de la logique métier et des fournisseurs LLM/RAG ; rendre le sens des dépendances évident et éviter les dépendances circulaires.
- Conserver une racine sobre, réservée aux points d’entrée documentaires et aux configurations nécessaires. Placer le code source, les ressources et la documentation dans des emplacements dédiés et prévisibles.
- Regrouper les fichiers qui évoluent ensemble ; placer les tests spécifiques près du code concerné et réserver un espace distinct aux tests transversaux.
- Éviter les dossiers fourre-tout (`misc`, `common`, `utils` sans périmètre défini), les imbrications inutiles, les fichiers monolithiques et les abstractions prématurées. Ne créer un dossier que lorsqu’il contient une responsabilité réelle.
- Documenter l’arborescence et les règles de dépendance dans le README afin qu’un nouveau contributeur sache immédiatement où trouver et ajouter une fonctionnalité.
- Faire évoluer la structure avec les besoins réels, sans créer de dossiers vides ni de structure complexe par anticipation.

## Expérience chatbot LLM/RAG

- Proposer deux vues : Agent et Recherche documentaire. La recherche directe réutilise le service RAG de l’agent, présente les passages regroupés par document et ouvre les PDF aux pages trouvées. Elle ne constitue pas un catalogue exhaustif des documents.
- Les références donnent accès à l’extrait et au PDF du document cité, dans un modal qui s’ouvre à la page physique indiquée (numérotation à partir de 1). Autoriser la navigation dans ce PDF cité, sans catalogue documentaire ni bouton global donnant accès à toutes les sources.
- Charger PDF.js et son worker local uniquement à la demande. Fournir pagination, accès direct à une page, zoom et texte sélectionnable. Annuler les chargements et rendus à la fermeture. Servir les fichiers par l’application et préserver le contrôle d’accès côté backend.

- Prévoir les compositions nécessaires : navigation des conversations, liste des messages, zone de saisie, pièces jointes et panneau de sources.
- Couvrir les états vide, chargement, réponse en streaming, interruption, erreur et nouvelle tentative.
- Prévoir le rendu des réponses Markdown, des blocs de code et des citations reliées aux documents sources.
- Enregistrer les avis par pouces en un clic, puis proposer un lien facultatif pour ouvrir un modal court et ajouter un commentaire ou un motif. Aucune note ni saisie obligatoire. Expliquer que chaque retour peut aider à améliorer le modèle. Lier l’avis à une copie du prompt, du contexte, de la réponse et des sources citées.
- Afficher la comparaison A/B directement dans le chat, avec un choix simple sous chaque version. Première question : réponse simple ; deuxième question : deux versions automatiquement ; questions suivantes : réponse simple. La version retenue devient le contexte des tours suivants. Ne pas afficher d’entrée Annotations dans le menu du chat ; garder les exports dans le catalogue pour les intégrateurs.
- La conversation de démonstration doit déjà inclure ces deux échanges au chargement. Positionner le fil sur la seconde question afin que le widget de comparaison soit visible sans devoir saisir un nouveau message.
- Séparer la notation humaine pour les données de récompense (PPO) et les préférences entre deux réponses au même prompt (DPO). Ne pas transformer automatiquement un pouce en récompense d’entraînement. Exclure les réponses identiques, les égalités et les doubles rejets des exports de paires DPO.
- Les annotations restent locales et exportables tant qu’aucun backend n’est connecté. Identifier les exemples de démonstration ; conserver les évaluations même si la réponse est régénérée.
- Séparer les composants visuels du transport réseau et des fournisseurs LLM/RAG. Utiliser des données de démonstration tant qu’aucun backend n’est choisi.
- Ne jamais exposer de secret fournisseur dans le navigateur. Traiter les contenus Markdown et les liens externes comme des entrées non fiables.

## Responsive, accessibilité et qualité

- Sauvegarder localement les conversations et les brouillons séparés par conversation ; restaurer la conversation active et la lecture après rechargement. Ne pas enregistrer les fichiers joints. Indiquer les échecs de sauvegarde sans bloquer l’usage.
- Fournir un menu compact réglable via sa séparation : glissement, clic ouvrant un curseur et navigation au clavier. Ajouter un mode replié de 60 px avec uniquement les icônes, des titres complets au survol et des noms accessibles. Mémoriser séparément le repliage et la largeur dépliée ; retrouver la largeur personnalisée au dépliage. Conserver de la place pour le chat et utiliser un tiroir complet avec libellés sur mobile.
- Prévoir un bouton « Dernière réponse » lorsque l’utilisateur remonte le fil. Le streaming ne doit pas déplacer sa lecture de force.
- Privilégier des libellés lisibles et des commandes tactiles d’au moins 44 px pour les petites actions sur mobile.

- Concevoir du mobile vers les grands écrans ; adapter navigation, panneaux, zone de saisie et débordements des contenus longs.
- Assurer la navigation au clavier, des libellés accessibles, un focus visible et une gestion correcte du focus des éléments superposés.
- Viser WCAG 2.2 AA ; respecter `prefers-reduced-motion` et annoncer les changements utiles sans faire lire chaque token du streaming.
- Utiliser les fonctionnalités HTML/CSS modernes lorsqu’elles apportent un bénéfice concret. Vérifier leur compatibilité et prévoir une amélioration progressive pour les fonctions récentes.
- Ne charger aucune dépendance du navigateur depuis un CDN ou un service externe. Préparer les dépendances via `make deps`, conserver le cache npm dans `.deps/npm` hors des répertoires publics, et préserver la réinstallation `make deps-offline`. Servir les bundles, fontes, workers et ressources PDF.js depuis le serveur de l’application.
- Limiter le JavaScript, les dépendances et les ressources chargées au nécessaire. Justifier chaque ajout significatif.
- Vérifier les parcours principaux, les thèmes, le clavier et les tailles d’écran représentatives ; adapter les tests à la complexité des comportements.

## Choix techniques validés

L’utilisateur a validé la stack suivante. L’initialisation peut s’appuyer sur ces choix sans nouvelle demande de validation.

- Next.js App Router pour le développement, les routes et la production ; React 19 pour les composants, à la demande explicite de l’utilisateur.
- TypeScript strict pour la logique et les contrats de données du chat.
- HTML sémantique et CSS natif pour la bibliothèque, avec propriétés personnalisées, cascade layers, Grid, Flexbox, nesting et container queries selon la compatibilité retenue.
- Composants React et CSS natif sans kit UI ni préprocesseur. SCSS ou Tailwind restent possibles si un besoin concret justifie leur ajout.
- Garder les Server Components pour les routes, métadonnées et données initiales sérialisables ; réserver `use client` aux frontières interactives. Ne pas accéder au DOM ni au stockage lors d’un import serveur.

Les versions sont verrouillées dans package-lock.json ; documenter la cible des navigateurs et les dépendances dans le README et docs/.

<!-- BEGIN:nextjs-agent-rules -->

## This is NOT the Next.js you know

This version has breaking changes — APIs, conventions, and file structure may all differ from your training data. Read the relevant guide in `node_modules/next/dist/docs/` (resolved from this file's directory; in monorepos the `next` package may not be visible from the repo root) before writing any code. Heed deprecation notices.

This block is written and re-added by `next dev` — verify at `node_modules/next/dist/server/lib/generate-agent-files.js`. Removing it from a diff only re-creates the uncommitted change; committing it with your work keeps the tree clean.

<!-- END:nextjs-agent-rules -->
