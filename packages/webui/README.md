# KM — WebUI RAG et bibliothèque Next.js

Template de chatbot LLM/RAG et bibliothèque de composants React, construit avec Next.js App Router, React 19, TypeScript strict et CSS natif. Le chat de l’application est connecté à l’agent Athena pour la recherche RAG et la génération LLM. La vue Recherche documentaire utilise directement le même moteur RAG, sans conversation. La bibliothèque conserve son adaptateur de démonstration.

## Deux parcours utilisateur

| Vue | Route | Traitement |
| --- | --- | --- |
| Agent | `/` | Run Athena : recherche outillée, génération LLM et citations |
| Recherche documentaire | `/search` | Recherche KM directe, lexicale/sémantique/hybride, sans génération LLM |

Les deux parcours exigent une session et partagent le lecteur PDF et l’autorisation
d’accès direct à Fileserver. Le catalogue de composants conserve les contenus de démonstration ;
les vues connectées interrogent les services configurés. La
[console KM](../km-frontend/README.md) est une application d’administration distincte.
Voir les [flux du dépôt](../../MICROSERVICES.md) et l’[index des ressources](../../ressources/README.md).

## Démarrer

Node.js ≥ 22.13 et npm sont requis. Configurer `.env.local` depuis `.env.example`
avant de démarrer : identifiants administrateur, secret de session et origines
des services Athena/KM, avec le préfixe `WEBUI_`. Voir
[l’authentification](docs/authentication.md).
Voir [le branchement RAG + LLM](docs/agent-chat.md) et [la recherche documentaire](docs/document-search.md). Exécuter les commandes suivantes depuis
`packages/webui/` (`cd packages/webui` depuis la racine du dépôt).

```sh
make deps           # installation et préparation du cache local, avec réseau
make dev            # http://localhost:4173
make deps-offline   # réinstallation depuis le cache préalablement préparé
```

Le navigateur reçoit uniquement des ressources locales : fontes, Markdown, PDF.js et worker. `.deps/npm` est privé et n’est jamais servi par Next.js. Après modification du lockfile, relancer `make deps` sur une machine connectée ; un ancien cache ne suffit pas aux nouvelles dépendances.

```sh
npm run check       # TypeScript
npm run test:search # recherche directe, accès PDF et annulation
npm run test:agent  # protocole Athena, autorisations, citations et arrêt local
npm run test:chat   # tests du modèle de chat, sans navigateur
npm run test:store  # reducers, snapshots et abonnements ciblés
npm run test:client # commandes et transports du client UI
npm run build       # distribution complète dist-ui/ et application Next.js .next/
npm run start       # serveur de production, port 4173
npm run build:ui    # package complet seul, via le même pipeline de bibliothèque
npm run check:ui    # exports, SSR sans DOM et ressources de la distribution
npm test            # chat connecté, desktop et mobile (build préalable et Chromium requis)
```

## Export Docker hors ligne

Le WebUI suit le workflow des bundles du dépôt. Depuis la racine, après
préparation des deux images locales de dépendances :

```sh
make webui
```

Cette commande reconstruit WebUI hors ligne et met à jour uniquement ses entrées
dans les bundles existants, sans réexporter les autres services :

- `.tarball/build/webui-runtime.tar` : base Node.js ;
- `.tarball/build/webui.tar` : dépendances npm de construction ;
- `.tarball/dev/webui.tar` et `.tarball/prod/webui.tar` : application Next.js.

Les `manifest.json` et `SHA256SUMS` de chaque dossier sont actualisés. Les autres
archives et leurs entrées restent conservées. Pour exporter l’image déjà
construite sans rebuild : `make webui-export`.
Les anciennes commandes `make -C packages/webui docker-tarball` et
`make -C packages/webui export-tarballs` délèguent à ces cibles racine.

Les commandes globales restent `make prepare-build` / `make export-build`,
`make dev` / `make prod`, puis `make load-build`, `make load-dev` et `make load-prod`
sur la cible. Elles utilisent les mêmes dossiers et le même format de manifeste.

## Sécurité & Chaîne de Confiance

Pour la spécification complète de la sécurité de WebUI, le diagramme de séquence de la chaîne de confiance (*Chain of Trust* NextAuth $\rightarrow$ RAG $\rightarrow$ JWT $\rightarrow$ Fileserver) et le contrôle d'accès unitaire aux documents cités :
👉 **[SECURITY.md](SECURITY.md)**

- L'authentification NextAuth (`docs/authentication.md`) sécurise chaque session avec `WEBUI_AUTH_SECRET`.
- Chaque conversation (*run*) est scellée cryptographiquement avec l'identifiant de l'utilisateur (`principal.id`).
- Un utilisateur ne peut accéder à un document PDF que si celui-ci a été formellement cité par le moteur RAG pour sa question.
- Le backend WebUI est le seul habilité à forger des jetons JWT pour Fileserver (`iss: "webui"`).

## Organisation

```text
src/
  css/                  # tous les styles, thèmes et tokens CSS
    index.css           # entrée publique du design system
    template.css        # compositions du template
  store/                # état, reducers, dispatchs et persistance locale
    create-store.ts     # moteur observable pur
    ui-state.ts         # état et reducer de l’interface
    client/             # contrôleur autonome, protocole et transport HTTP
    chat/               # état du chat, contrôleur et simulation
    feedback/           # annotations et exports
    search/             # recherche documentaire et transport annulable
  backend/              # fonctions exécutées côté serveur
    initial-conversation.ts
    ui-demo.ts
    source-files/       # signatures JWT et client fileserver pour les sources autorisées
    auth/               # NextAuth, sessions et identité serveur
    chat/               # client Athena, runs et citations
    http/               # contrôles HTTP partagés
    search/             # moteur RAG partagé, résultats et accès aux PDF
  components/           # bibliothèque et compositions React
    ui/                 # boutons, dialogues, Markdown et notifications
    providers/          # providers et hooks reliant React aux stores
    chat/               # messages, saisie, sources PDF et adaptateur RAG
    layout/             # navigation et coque responsive
    search/             # formulaire et résultats documentaires
    catalog/            # exemples interactifs
  app/                  # routes et layout requis par Next.js App Router
  content/              # dictionnaire fr.json et formatText()
public/
  fonts/                # WOFF2 et licences
  documents/            # PDF fictifs cités
  pdfjs/                # ressources locales générées
scripts/                # préparation, compilation unique et vérification SSR
tests/                  # parcours navigateur transversaux
```

Les routes `app/` délèguent les traitements serveur à `backend/`, qui fournit les données initiales et les réponses API. `backend/` peut utiliser les contrats et le protocole purs du store ; les composants et les stores ne doivent pas importer de code serveur. Le build vérifie cette frontière. React possède le DOM, les événements et l’état UI ; le modèle de chat reste indépendant de React et du transport. Seul le lecteur PDF pilote son conteneur DOM dédié, créé et détruit par un effet React. Les contrats et textes s’importent côté serveur sans DOM ni stockage.

## Adapter

- Modifier `src/css/theme.css` pour les thèmes clair/sombre et l’accent rouge.
- Modifier `src/content/fr.json` pour les textes affichés et accessibles.
- Composer les composants de `src/components/` dans les routes App Router.
- L’application utilise `HttpChatProvider` vers ses routes serveur authentifiées. Pour une autre intégration, fournir un `ChatProvider` à `RenoProvider`. Les appels fournisseur et secrets restent côté serveur.
- Le chat connecté synchronise conversations et brouillons avec le backend SQL, avec une copie locale par compte. La bibliothèque autonome et les annotations restent locales. Les pièces jointes ne sont pas persistées et sont refusées par le contrat Athena actuel.
- Les PDF s’ouvrent depuis les citations du chat ou les résultats documentaires autorisés par le serveur. Aucun explorateur global du corpus n’est fourni ; le périmètre documentaire reste celui exposé par KM.
- La sécurisation des sources PDF repose sur des signatures JWT scellées au chemin et générées à la volée côté serveur (voir la documentation détaillée dans `packages/fileserver/PROD_SECURITY.md`). Le chat connecté utilise ses propres routes autorisées pour les PDF cités, avec détection d’expiration et rafraîchissement transparent.
- L’[authentification NextAuth](docs/authentication.md) fournit `/login` et un bouton OIDC optionnel. Le compte administrateur local exige `WEBUI_AUTH_ADMIN_LOGIN` et `WEBUI_AUTH_ADMIN_PASSWORD`, définis côté serveur avec `WEBUI_AUTH_SECRET`. Sans ces identifiants, les connexions locales sont refusées ; aucun compte de secours n’est fourni.

La compilation produit un seul package dans `dist-ui/`, avec des entrées dédiées :

```ts
import { createUiClient } from 'reno-plus-chat-template/client';
import { UiClientProvider } from 'reno-plus-chat-template/react';
import 'reno-plus-chat-template/styles.css';
```

`src/store/client/` porte le protocole et le transport ; `src/store/` reste sans React ; `src/components/providers/` relie les composants aux stores. Le chat/RAG possède son état dans `store/chat/` et son adaptateur visuel dans `components/chat/`. Les composants et l’adaptateur React nécessitent React et ReactDOM ; l’entrée `client` ne les charge pas.

Le contrôleur `createUiClient()` fonctionne sans React. L’adaptateur `UiClientProvider` reçoit son instance et affiche les composants pilotés par le store. Voir [le client UI indépendant](docs/ui-client.md). Un exemple backend Next.js est disponible dans le catalogue `/catalog`.

Voir [les stores et sélecteurs](docs/state-management.md), [le design system](docs/design-system.md), [l’intégration React/Next.js](docs/framework-integration.md) et [l’état de la migration](docs/audit-front.md).

## Déploiement des conversations et des sources PDF

Les routes `/api/chat/runs/.../sources/...` et `/api/search/sources/...` retournent
une autorisation JSON (`url`, `expiresAt`). PDF.js lit directement Fileserver.
Configurer `FILESERVER_PUBLIC_URL` (HTTPS en production) et le même
`FILESERVER_JWT_SECRET` côté backend WebUI et Fileserver. Le secret n’est jamais
transmis au navigateur. Fileserver doit recevoir `WEBUI_PUBLIC_ORIGIN` égal à
l’origine publique de WebUI (`WEBUI_AUTH_URL` dans Compose).
KM doit exposer `/documents/{id}/source-reference` avec `document_id` et
`relative_path` ; ce contrat doit être vérifié sur l’image effectivement livrée.
Une citation sauvegardée garde son autorisation liée au compte ; chaque ouverture
revalide la référence et émet un JWT de fichier de cinq minutes. Un 401 du lecteur
provoque une nouvelle autorisation, avec une seule tentative par échec.

En production, renseigner `WEBUI_DATABASE_URL` ou les variables `WEBUI_DB_*`.
Les tables WebUI, les quotas, les droits, le mot de passe SCRAM et les règles
HBA sont installés par l’image PostgreSQL sur un volume neuf. Pour reconstruire
la base en conservant les caches documentaires, suivre le [guide de reprise](../../ressources/TUTO_DEPLOY.md#8-initialisation-de-postgresql-et-reprise-des-caches).
