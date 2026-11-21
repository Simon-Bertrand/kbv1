# État de la migration Next.js

La référence applicative est Next.js App Router avec React 19 et TypeScript strict. Les routes `/` (Agent), `/search` (Recherche documentaire) et `/catalog`
(bibliothèque de composants) utilisent le layout authentifié `(workspace)` ;
leur rendu dépend de la session. `/login` est l’entrée de connexion. Les anciens contrôleurs DOM, les templates HTML par concaténation et Vite ont été retirés. Les exports React ont remplacé les entrées historiques `browser` et `interactions`.

Le CSS natif, les thèmes clair/sombre rouges, les fontes locales, les textes JSON et les contrats de données sont conservés. Le lecteur PDF garde un moteur impératif isolé derrière un composant React chargé à la demande. Les textes Markdown restent échappés côté serveur avant rendu assaini après hydratation.

Validation : typage strict, build Next.js, distribution React, vérifications SSR sans DOM et tests du moteur de chat. Les tests Playwright couvrent les nouvelles routes et interactions ; leur exécution nécessite un environnement autorisant Chromium et le serveur local.

Les dépendances sont verrouillées dans package-lock.json. Après migration, préparer intégralement le nouveau cache avec `make deps` sur une machine connectée avant de compter sur `make deps-offline`.
