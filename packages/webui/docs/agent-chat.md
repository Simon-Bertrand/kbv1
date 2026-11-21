# Chat RAG + LLM avec Athena

L’application Next.js utilise le même protocole que le chat de `packages/km-frontend` :
création d’une exécution Athena par `POST /runs`, puis consultation de
`GET /runs/{id}`. Dans la vue Agent, Athena reste responsable de la recherche documentaire et de
la génération LLM. La [vue Recherche documentaire](document-search.md) appelle
directement le même service KM sans lancer Athena. WebUI contacte PostgreSQL pour les comptes et conversations ; les recherches passent par KM et les appels au modèle par Athena.

## Configuration

Depuis `packages/webui`, copier `.env.example` vers `.env.local` pour le
lancement local. Définir `WEBUI_AUTH_ADMIN_LOGIN` et `WEBUI_AUTH_ADMIN_PASSWORD` pour le
compte administrateur local, `WEBUI_AUTH_SECRET` avec au moins 32 caractères aléatoires,
définir `WEBUI_AUTH_URL` avec l’origine publique exacte de WebUI,
puis les deux origines HTTP accessibles depuis le serveur Next.js :

| Variable | Usage | Valeur Compose par défaut |
| --- | --- | --- |
| `WEBUI_AGENT_URL` | Agent Athena, facultatif pour démarrer WebUI | `http://km-agent:8086` |
| `WEBUI_KM_API_URL` | Recherche documentaire, extraits et fichiers cités | `http://km-api:8000` |
| `WEBUI_CHAT_SECONDS` | Budget agent, de 1 à 900 secondes | `180` |
| `WEBUI_SEARCH_MODE` | `lexical`, `semantic` ou `hybrid` | `hybrid` |

Ces variables sont exclusivement serveur, sans préfixe `NEXT_PUBLIC_`. Les URL
désignent des origines sans chemin, paramètres ni identifiants. Un navigateur
n’a besoin que de l’origine WebUI.

Tous les réglages applicatifs utilisent le préfixe `WEBUI_`. Les paramètres
de connexion sont `WEBUI_AGENT_URL` et `WEBUI_KM_API_URL` ; les paramètres
Compose de publication sont `WEBUI_HOST` et `WEBUI_PORT`. Les variables
d’authentification utilisent le même préfixe, par exemple `WEBUI_AUTH_SECRET`.
Le port interne du serveur reste 4173. Les variables standard du runtime,
comme `NODE_ENV`, restent inchangées.

Next.js lit `.env.local`. Docker Compose lit le `.env` du projet Compose ou le
fichier passé par `--env-file` ; il ne charge pas automatiquement `.env.local`.
Le `compose.yaml` de WebUI transmet toutes les variables ci-dessus. Pour une
utilisation autonome, depuis la racine du dépôt :

```sh
docker compose --env-file packages/webui/.env.local -f packages/webui/compose.yaml up -d --build
```

Les noms `km-agent` et `km-api` ne sont résolus que si WebUI partage leur réseau
Docker. Avec deux projets Compose séparés, rattacher le conteneur WebUI au réseau
KM existant (`docker network connect <réseau-KM> <conteneur-WebUI>`), ou fournir
des origines déjà accessibles depuis WebUI. Une recréation du conteneur nécessite
un nouveau rattachement manuel. Aucun service KM n’est créé ou modifié par WebUI.

Le serveur LLM, son modèle et la connexion PostgreSQL restent configurés dans
Athena/KM. La connexion à WebUI est obligatoire. Credentials vérifie le compte
administrateur défini par `WEBUI_AUTH_ADMIN_LOGIN` et `WEBUI_AUTH_ADMIN_PASSWORD` côté serveur.
Sans ces deux variables, aucune connexion locale n’est acceptée. OIDC reste optionnel.
Voir [l’authentification](authentication.md).

## Flux et autorisations

1. Le navigateur appelle `POST /api/chat/runs` avec `{ "question": "…", "history": [...] }`.
   L’historique facultatif contient au plus 12 tours récents (`user`/`assistant`),
   4 000 caractères par tour et 12 000 au total. Il respecte l’ordre chronologique
   et la version retenue des réponses comparées. Athena l’utilise pour résoudre
   les références de la question actuelle ; les réponses précédentes ne sont pas
   considérées comme des preuves documentaires.
2. Le backend vérifie la session, l’origine et la question (1 à 4 000 caractères),
   puis crée une exécution avec le budget et le mode configurés côté serveur.
3. Il retourne une référence signée, liée au compte connecté et valable huit heures.
4. **Transport SSE temps réel (avec repli polling) :**
   - Le navigateur ouvre un flux SSE via `GET /api/chat/runs/{référence}/events`.
   - Le backend BFF (Route Handler Next.js) consulte l'API interne d'Athena (`GET /runs/{id}/events` et `GET /runs/{id}`) de manière bornée avec pagination `after`/`offset`.
   - Les événements bruts sont filtrés par une liste blanche stricte (`projectAthenaEvent`) : seules les activités observables (recherche textuelle, lecture de pages, synthèse, vérification) sont transmises au format SSE `event: activity`.
   - **Protection stricte du secret et du raisonnement privé :** la catégorie `llm`, les prompts, le thinking/chain-of-thought, les brouillons, les variables internes, les URLs privées et les chemins de fichiers sont intégralement supprimés.
   - Les en-têtes SSE (`Content-Type: text/event-stream`, `Cache-Control: private, no-cache, no-store, no-transform`, `X-Accel-Buffering: no`) demandent une transmission sans mise en tampon. Un battement de cœur autonome de 5 secondes (`: ping\n\n`) est émis indépendamment des requêtes amont pour prévenir la fermeture des connexions par les passerelles NAT, AWS/GCP ALB et reverse proxies NGINX lors des pauses d'activité.
   - À l'achèvement du run, un unique `event: complete` est émis avec la réponse finale et ses citations normalisées.
   - En cas d'échec ou d'interruption, `event: error` est émis.
   - En cas d'indisponibilité du SSE ou d'erreur réseau transitoire, le client effectue jusqu'à 3 tentatives puis bascule automatiquement sur le polling HTTP unifié `GET /api/chat/runs/{référence}` (1s) sans duplication d'événements (déduplication côté client par séquence) ni perte d'état.
5. Les boutons de source ouvrent le modal PDF existant. Les extraits viennent
   des passages cités ou de la reconstruction de leur page physique dans KM.
   Une reconstruction peut inclure du texte de pages voisines. Si l’extrait est
   indisponible, la réponse et la référence au PDF restent disponibles.

### Configuration Reverse Proxy recommandée (NGINX / Ingress / ALB)

Pour assurer la transmission instantanée du flux SSE vers les navigateurs clients sans mise en mémoire tampon ni interruption :
- **Désactiver la mise en mémoire tampon** : `proxy_buffering off;` (NGINX honore également l'en-tête `X-Accel-Buffering: no` émis par le handler WebUI).
- **Ajuster les délais d'inactivité** : `proxy_read_timeout 300s;` pour couvrir le budget maximal d'exécution agent (`WEBUI_CHAT_SECONDS`).
- **Heartbeat 5s pour NAT / ALB / NGINX** : Le flux émet un commentaire `: ping\n\n` toutes les 4,5–5s pour limiter les coupures d’inactivité ; les délais et réglages effectifs des intermédiaires restent à vérifier sur le déploiement.
- **Désactivation du cache** : Conserver `no-transform, no-cache, no-store`.

Exemple de bloc NGINX :
```nginx
location /api/chat/runs/ {
    proxy_pass http://127.0.0.1:4173;
    proxy_http_version 1.1;
    proxy_set_header Connection '';
    proxy_buffering off;
    proxy_read_timeout 300s;
    proxy_cache off;
    chunked_transfer_encoding on;
}
```

- Le compteur de flux actifs (maximum 5 flux SSE simultanés par principal) est géré en mémoire par processus Node.js. Dans un déploiement multi-réplicas (ex. 2 instances WebUI derrière un répartiteur de charge sans affinité de session / sticky sessions), cette limite s'applique par processus et ne constitue pas un plafond global distribué au niveau du cluster.
- **Budget de trame et réponse finale :** Le décodeur SSE et le protocole acceptent une ligne / charge utile jusqu'à 8 Mo (`MAX_LINE_LENGTH = 8 Mo`, `MAX_BUFFER_SIZE = 16 Mo`), permettant d'acheminer sans troncature ni simulation de streaming les réponses complètes d'Athena (jusqu'à 1 000 000 caractères et 100 sources documentaires avec extraits). En cas de dépassement strict, une erreur localisée en français (`invalidResponse`) est renvoyée sans divulgation technique.
- **Points de contrôle et reprise sur coupure :** Lorsque les événements amont sont privés (ex. raisonnement LLM filtré), le serveur émet un événement `checkpoint` avec le curseur `next_after` validé. En cas de déconnexion réseau, le client reprend exactement au curseur sans double incrément ni rejeu inutile de l'historique archivé.
- **Gestion de la contre-pression (backpressure) :** L'émission SSE surveille la capacité du flux (`desiredSize`). Si le consommateur ralentit, l'émission attend jusqu'à 5 secondes avant d'interrompre proprement le flux, évitant toute saturation mémoire lors des phases de drain.

L’accès au PDF utilise `GET`/`HEAD /api/chat/runs/{référence}/sources/{numéro}`.
Le backend vérifie la session du compte et la signature de la citation, puis
renvoie une URL Fileserver signée valable cinq minutes. Le navigateur télécharge
le PDF directement auprès de Fileserver, qui contrôle le jeton et sert les plages
d’octets. Aucune URL arbitraire fournie par le navigateur n’est utilisée.

Les nouvelles citations sont indépendantes de l’archive Athena et ne sont pas
limitées aux huit heures du token de suivi du run. Leur révocation et la
compatibilité avec les anciennes références sont décrites ci-dessous.
Les conversations et annotations restent stockées localement, séparées par compte ;
les anciennes données de démonstration ne sont pas importées dans ce nouvel espace.
Les annotations des réponses réelles portent `origin: generated`.

## Comportements pris en charge

- Le fil démarre vide et reçoit les réponses réelles de l’agent.
- Athena fournit une réponse finale : pas de simulation d’un streaming de tokens.
- « Arrêter » interrompt les requêtes et l’attente dans WebUI. Athena ne propose
  pas d’annulation d’une exécution active ; son calcul continue jusqu’à sa fin
  ou jusqu’au budget configuré.
- Le service accepte une question indépendante. Les tours précédents restent
  dans le fil local, mais ne sont pas envoyés comme mémoire conversationnelle.
- Les pièces jointes sont refusées explicitement, car le contrat Athena ne les
  accepte pas. Elles ne sont jamais envoyées ou prétendument analysées.
- Régénérer et comparer créent de nouvelles exécutions avec la même question.
  L’agent ne propose pas de paramètre de variante ; les réponses peuvent être identiques.
- Une saturation de l’agent (429), une indisponibilité, un échec ou un délai dépassé
  donnent une erreur visible ; le chat permet ensuite une nouvelle tentative.

## Organisation et vérification

- `src/backend/chat/` : client Athena/KM, références signées, contrôles d’accès et handlers.
- `src/app/api/chat/` : routes Next.js fines.
- `src/store/chat/http-provider.ts` : transport du chat, polling et arrêt local.
- `src/components/chat/connected-provider.tsx` : branchement de l’application.
- Le `DemoProvider` reste disponible pour les consommateurs de la bibliothèque.

```sh
npm run check
npm run test:agent
npm run test:sse
npm run test:chat
npm run test:store
npm run test:client
npm run test:auth
npm run build
npm run check:ui
# Après le build, avec Chromium Playwright installé :
npm test
# Ou utiliser un Chromium système :
PLAYWRIGHT_CHROMIUM_EXECUTABLE=/usr/bin/chromium npx playwright test --config playwright.agent.config.ts

# Lancement des tests E2E avec serveur de test dédié (environnement conteneurisé sans RST immédiat) :
WEBUI_TEST_PORT=4198 node scripts/agent-test-server.mjs & SERVER_PID=$!; for i in $(seq 1 30); do curl -s http://127.0.0.1:4198/login >/dev/null && break; sleep 0.5; done; WEBUI_TEST_PORT=4198 npx playwright test --config playwright.agent.config.ts agent-chat.e2e.ts --reporter=line; kill $SERVER_PID 2>/dev/null
```

Les tests d’intégration lancent le serveur Next.js de production et des doubles
Athena/KM locaux, sur un port WebUI distinct (`4187`, réglable via
`WEBUI_TEST_PORT`). Dans les environnements conteneurisés où les paquets TCP SYN vers des ports fermés sont ignorés (timeout 63s de la sonde initiale Playwright), démarrer préalablement `scripts/agent-test-server.mjs` permet une exécution instantanée sans attente. Ils n’utilisent pas les services ni le LLM du déploiement.


## Citations conservées dans les conversations

Les nouvelles références PDF sont signées pour le document, le compte et
`WEBUI_SESSION_EPOCH`, indépendamment du run Athena. Elles permettent de rouvrir
une citation après huit heures et après la purge des traces Athena. Chaque
ouverture nécessite toujours une session authentifiée du même compte et émet
un jeton Fileserver valable cinq minutes. La rotation de `WEBUI_AUTH_SECRET` ou
de `WEBUI_SESSION_EPOCH` révoque les références. La recherche documentaire suit
le même principe. Les anciennes URL de chat restent utilisables après huit
heures si leur run terminé est encore archivé ; elles ne peuvent pas être
reconstruites après la suppression de cette archive.

Les sauvegardes de conversations acquittent chaque révision dès son écriture.
Si une conversation ultérieure du lot échoue, les écritures déjà confirmées
restent enregistrées localement et ne sont pas rejouées avec une révision périmée.
