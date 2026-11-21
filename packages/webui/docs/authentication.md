# Authentification Next.js

`src/backend/auth/` contient l’intégration NextAuth, avec la version exacte
`5.0.0-beta.32` verrouillée. L’intégration App Router suit la
[configuration officielle Auth.js](https://authjs.dev/getting-started/installation).

- `config.ts` configure Credentials, OIDC et les callbacks de session.
- `credentials.ts` définit l’adaptateur de vérification des comptes locaux.
- `index.ts` expose `auth`, `signIn`, `signOut`, `handlers` et `authenticateSession`.
- `next-auth.d.ts` type l’identifiant ajouté à `session.user`.
- `src/app/api/auth/[...nextauth]/route.ts` expose les handlers GET/POST.
- `src/app/login/page.tsx` et `components/auth/login-form.tsx` fournissent le formulaire.

## Identifiant et mot de passe

La page `/login` propose un formulaire classique avec autocomplétion navigateur,
validation native, état de chargement et erreur générique. Elle utilise le
[provider Credentials de NextAuth](https://authjs.dev/getting-started/authentication/credentials).
`/api/auth/signin` redirige vers cette page. Après connexion, la destination est
fixée à `/`. Le mot de passe n’est pas conservé dans un store et son champ est
vidé après la tentative.

Par défaut, `createCredentialsVerifier(env)` vérifie le compte administrateur
configuré exclusivement côté serveur avec `WEBUI_AUTH_ADMIN_LOGIN` et
`WEBUI_AUTH_ADMIN_PASSWORD`. Les deux variables sont obligatoires pour la connexion
locale : une configuration absente, partielle, vide ou hors des bornes du
formulaire refuse toute tentative. Aucun identifiant ni mot de passe de secours
n’est fourni, y compris en développement et lorsque OIDC est activé.

L’identifiant est comparé après suppression des espaces aux extrémités ; le mot
de passe doit correspondre exactement, espaces et casse compris. Les comparaisons
utilisent des empreintes SHA-256 de taille fixe et `timingSafeEqual`. Le mot de
passe n’est ni renvoyé au navigateur ni journalisé. Ce compte donne accès au
WebUI ; cette configuration n’ajoute pas de système de rôles métier.

NextAuth crée une session JWT chiffrée dans son cookie HttpOnly, lisible côté
serveur avec `auth()` ou `authenticateSession()`. L’identité reste stable pour
le login administrateur configuré. Le jeton n’est pas renvoyé en clair au
JavaScript du navigateur.

Pour brancher un service de comptes, fournir un vérificateur selon le contrat
`VerifyCredentials` au second argument de `createAuthConfig(env, verifier)` :

```ts
// Schéma d’intégration : accountService appartient à l’application hôte.
export const verifyCredentials: VerifyCredentials = async ({ login, password }, request) => {
  const user = await accountService.verifyPassword({ login, password, request });
  return user ? { id: user.id, name: user.name, email: user.email } : null;
};
```

Le service de comptes est responsable de la vérification du hash de mot de
passe, de l’état du compte et de la limitation des tentatives. Il ne doit pas
journaliser le mot de passe. Retourner `null` pour tout refus, sans révéler si le
compte existe. Pour le compte administrateur par variables d’environnement,
la limitation des tentatives doit être assurée par l’ingress ou le déploiement.

Credentials fonctionne sans OIDC. Configurer `WEBUI_AUTH_ADMIN_LOGIN`,
`WEBUI_AUTH_ADMIN_PASSWORD`, `WEBUI_AUTH_SECRET`, `WEBUI_AUTH_URL` et `WEBUI_AUTH_TRUST_HOST` pour permettre
les sessions locales. Le formulaire en lui-même reste consultable sans secrets.

## Configuration

Un fournisseur OIDC générique est optionnel, par exemple Keycloak ou Authentik.
Le bouton « Se connecter avec mon organisation » apparaît lorsque les trois
variables OIDC sont renseignées. Pour l’ajouter, configurer côté serveur :

```dotenv
WEBUI_AUTH_SECRET=<valeur générée avec openssl rand -base64 32>
WEBUI_AUTH_ADMIN_LOGIN=<identifiant administrateur choisi>
WEBUI_AUTH_ADMIN_PASSWORD=<mot de passe administrateur choisi>
WEBUI_AUTH_URL=https://chat.example.org
WEBUI_AUTH_OIDC_ISSUER=https://identity.example.org/realms/example
WEBUI_AUTH_OIDC_ID=<client-id>
WEBUI_AUTH_OIDC_SECRET=<client-secret>
WEBUI_AUTH_TRUST_HOST=true
```

Enregistrer chez le fournisseur la callback exacte
`https://chat.example.org/api/auth/callback/oidc`. La connexion commence sur
`/login` ; la déconnexion passe par `/api/auth/signout` ou l’action serveur
`signOut()`. Le client NextAuth gère les échanges CSRF pour le formulaire.
Aucun stockage utilisateur, inscription ou réinitialisation de mot de passe
n’est ajouté.

`WEBUI_AUTH_URL` est l’URL publique du WebUI. Pour le développement local, utiliser
`http://localhost:4173` et autoriser cette callback chez le fournisseur.
`WEBUI_AUTH_TRUST_HOST` doit être explicitement activé pour accepter les requêtes
d’authentification : le serveur ou le reverse proxy doit alors contrôler les
en-têtes Host et X-Forwarded-*. Le défaut Compose est `false`. En production,
l’issuer OIDC doit utiliser HTTPS. Les secrets sont transmis au conteneur à
l’exécution ; ils ne sont pas nécessaires au build Docker.

Avec les trois variables OIDC vides, seul Credentials est proposé : le
template peut être construit sans configuration d’identité. Une configuration
OIDC partielle est refusée. Le secret doit contenir au moins 32 caractères lorsque
OIDC ou le compte administrateur est configuré. Pour le lancement Next.js hors Docker, placer
ces variables dans `packages/webui/.env.local` ; le `.env` racine est lu par Compose.
Les identifiants sont transmis au conteneur à l’exécution, sans préfixe
`NEXT_PUBLIC_` ni valeur intégrée au build. Après modification, redémarrer le
processus Next.js ou recréer le conteneur WebUI pour charger son nouvel environnement.
Les variables de déploiement portent toutes le préfixe `WEBUI_`. Renommer les
anciennes clés dans votre `.env` ou `.env.local` : `AUTH_ADMIN_LOGIN` devient
`WEBUI_AUTH_ADMIN_LOGIN`, `AUTH_SECRET` devient `WEBUI_AUTH_SECRET`, etc. Les
anciens noms ne servent pas de repli. La frontière NextAuth traduit uniquement
les noms requis par Auth.js dans le processus serveur WebUI, en ignorant les
anciennes URL et clés de signature éventuellement héritées d’une autre application.
Changer le mot de passe ne révoque pas les sessions déjà émises : faire aussi
tourner `WEBUI_AUTH_SECRET` si ces sessions doivent être invalidées.

## Utilisation côté serveur

```ts
import { auth, authenticateSession } from './auth';

const session = await auth();
// session?.user.id : identité stable, locale ou OIDC, avec espaces de noms séparés.

const user = await authenticateSession();
// { id: string } ou null, dans le contexte d’une requête Next.js.
```

Les sessions JWT sont chiffrées dans les cookies gérés par NextAuth et expirent
après huit heures. Les tokens d’accès du fournisseur ne sont pas exposés par le
callback de session. L’identité OIDC est fondée sur l’issuer et le subject ;
l’identité locale sur l’id renvoyé par l’adaptateur serveur, préfixé par
l’espace de noms Credentials. Ces deux identités ne sont pas liées
automatiquement, même si les emails correspondent. Ni l’email ni un identifiant
envoyé par le navigateur ne font foi.
Une rotation d’`WEBUI_AUTH_SECRET` invalide les sessions existantes ; la révocation
immédiate côté fournisseur n’est pas implémentée par cette stratégie JWT locale.

Le proxy de sources peut recevoir `authenticate: authenticateSession`. Son
résolveur doit encore vérifier l’accès au chat et à la source. L’ajout de
NextAuth ne protège pas automatiquement les pages ou les API existantes :
chaque traitement protégé doit vérifier la session avant de servir ses données.
Les composants et stores n’importent jamais `backend/auth/`. Un éventuel client
React utilisera l’entrée dédiée `next-auth/react`.

## Validation

`npm run test:auth` vérifie la configuration, les identités, l’absence de
confiance dans les mises à jour client et les sessions réelles Auth.js avec
cookies chiffrés, absents ou falsifiés, ainsi que les refus de configuration
absente, les mauvais identifiants/mots de passe et le callback Credentials avec
vérification CSRF. Les tests navigateur utilisent des identifiants explicites
dans `tests/fixtures/auth.mjs`, uniquement injectés par leurs serveurs locaux.
`tests/login.spec.ts` couvre le formulaire sur desktop et
mobile, les refus et l’affichage conditionnel du bouton OIDC. Un test de connexion OIDC complète
nécessite le fournisseur et ses identifiants.
