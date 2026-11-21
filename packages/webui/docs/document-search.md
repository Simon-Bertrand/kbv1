# Recherche documentaire directe

La navigation propose deux vues : **Agent** (`/`) et **Recherche documentaire**
(`/search`). La recherche accepte des termes ou une phrase et retourne des passages
classés, regroupés par document. Le classement global reste indiqué sur chaque
passage. Les options permettent de demander 10, 20 ou 50 passages et de choisir
un mode hybride (par défaut), sémantique ou lexical. Les scores du moteur restent
des scores de classement ; l'interface ne les présente pas comme des probabilités.

## Moteur partagé

Le navigateur appelle `POST /api/search` avec `{ question, limit, mode }`.
Le backend WebUI transmet la requête au `POST /search` de `WEBUI_KM_API_URL`,
exactement comme le client de recherche de l'agent. KM calcule l'embedding avec
le profil textuel actif pour les recherches sémantiques/hybrides ; le mode lexical
ne calcule pas d'embedding. La recherche directe ne crée aucune exécution Athena.
Aucun nouvel index ni changement d'identité des caches PdfParse n'est nécessaire.

Le formulaire démarre vide, sans catalogue ou résultat simulé. Il couvre chargement,
annulation, résultat vide, erreur et nouvelle tentative. Une nouvelle soumission
annule la précédente ; les réponses obsolètes sont ignorées. Quitter la vue annule
son transport. Cette annulation ne garantit pas l'arrêt du calcul déjà lancé par KM.

## Sources et autorisations

La session est obligatoire sur la page et les routes API. Le POST vérifie l'origine,
le type JSON, la taille du corps, les paramètres et les bornes (4 000 caractères,
1 à 100 passages côté API). Les réponses du moteur sont bornées et normalisées ;
les chemins internes, erreurs techniques et URL amont ne sont pas transmis.

Les résultats utilisent le lecteur PDF existant. Le serveur émet une référence
signée du document retourné, liée au compte et à `WEBUI_SESSION_EPOCH`, sans
expiration liée à la recherche. `GET` et `HEAD` sur
`/api/search/sources/{référence}` revérifient la session et la signature avant
d’émettre une URL Fileserver valable cinq minutes. Le navigateur télécharge le
PDF directement auprès de Fileserver. Les références antérieures avec une date
d’expiration restent vérifiées avec leur signature d’origine.
Un autre compte ne peut pas réutiliser ce lien ; une rotation du secret ou de
l’époque de session le révoque. Le périmètre documentaire est celui exposé par KM
au service ; ces références ne remplacent pas un contrôle documentaire propre à KM.

## Organisation et tests

- `src/backend/search/` : requêtes KM, normalisation, références et handlers.
- `src/store/search/` : contrats et contrôleur observable indépendant de React.
- `src/components/search/` : formulaire, états et regroupement des passages.
- `src/app/api/search/` : routes Next.js fines.
- `src/backend/source-files/km.ts` : proxy PDF commun aux deux vues.

Configuration : `WEBUI_AUTH_SECRET`, `WEBUI_AUTH_URL` et `WEBUI_KM_API_URL`, déjà utilisés
par le chat. La connexion locale exige aussi `WEBUI_AUTH_ADMIN_LOGIN` et
`WEBUI_AUTH_ADMIN_PASSWORD` ; voir l’[authentification](authentication.md).
Tous ces réglages ont le préfixe `WEBUI_` et restent côté serveur.

```sh
npm run check
npm run test:search
npm run test:agent
npm run build
npm test
```

Les tests navigateur utilisent des services locaux de test et couvrent les deux
vues sur ordinateur et mobile. Ils ne mesurent pas la pertinence de l'index réel.
