# Bibliothèque de contrôle UI et adaptateur React

Le contrôleur TypeScript possède son store et son transport. Il fonctionne sans React, Next.js, RenoProvider ou modèle de chat. L’adaptateur React reçoit une instance du contrôleur, s’abonne à son état et affiche les composants de la bibliothèque : dialogue natif, notifications et contrôles applicatifs.

```text
Backend TypeScript / Next.js
        │ JSON / NDJSON / événements de l’hôte
        ▼
Contrôleur TypeScript — entrée /client
  commandes validées → store observable
        │ instance passée explicitement
        ▼
UiClientProvider — adaptateur React
        │ abonnements ciblés
        ▼
Dialog · notifications · composants de l’hôte
```

## Une distribution, plusieurs entrées

`npm run build` produit l’application Next.js et le package unique `dist-ui/`. `npm run build:ui` permet de compiler seulement ce package avec le même pipeline. Il contient le contrôleur (`client`), les liaisons React (`react`), les composants, les stores, les contrats, les textes et les styles. Une seule configuration TypeScript produit leurs déclarations.

Les pairs React/ReactDOM sont facultatifs pour installer le package et utiliser le contrôleur. Ils sont nécessaires pour les composants et l’adaptateur React. Le build vérifie que les entrées `client`, `store`, `texts` et `contracts` ne chargent aucun module React ou navigateur. Le contrôleur n’importe ni CSS, ni DOM, et n’ouvre aucune connexion à l’import.

Les responsabilités restent séparées dans les sources : `src/store/client/` pour le protocole/transport, `src/store/` pour le moteur pur, `src/components/providers/` pour les providers/hooks et `src/components/` pour le rendu. Le chat/RAG possède son état dans `src/store/chat/` et son adaptateur optionnel dans `src/components/chat/`. Les traitements serveur sont dans `src/backend/` ; `src/app/` ne fait que les exposer aux routes Next.js.

## Contrôler l’interface depuis TypeScript

```ts
import { createUiClient } from 'reno-plus-chat-template/client';

const ui = createUiClient();
ui.theme.set('dark');
ui.sidebar.set({ width: 260, collapsed: false });
ui.modal.open('Export terminé', 'Le fichier est prêt.');
ui.notifications.show('Préférences enregistrées.', 'success');

const unsubscribe = ui.subscribe(state => state.theme, theme => {
  console.log(theme);
});

// Ces opérations fonctionnent avant le montage de React.
// L’état sera affiché lorsque l’adaptateur recevra cette instance.
```

Cette instance ne doit pas être un singleton partagé entre requêtes serveur. Le code ci-dessus illustre une instance possédée par l’hôte ; dans Next.js, la créer une fois dans un Client Component.

## Brancher les composants React, sans chatbot

```tsx
'use client';
import { useState } from 'react';
import { createUiClient } from 'reno-plus-chat-template/client';
import { UiClientProvider } from 'reno-plus-chat-template/react';
import { Button } from 'reno-plus-chat-template';
import { uiTexts } from 'reno-plus-chat-template/texts';

export function Interface() {
  const [ui] = useState(() => createUiClient());
  return <UiClientProvider client={ui} disposeOnUnmount>
    <Button onClick={() => ui.modal.open(
      uiTexts.common.info,
      uiTexts.application.ceContenuUtiliseLeModalPartageVous,
    )}>{uiTexts.catalog.ouvrirUnDialogue}</Button>
  </UiClientProvider>;
}
```

Aucun RenoProvider n’est nécessaire. Le contrôleur ne rend rien lui-même ; l’adaptateur utilise les composants React `Dialog` et `NotificationProvider`. Les contenus du modal sont du texte échappé, jamais du HTML reçu du backend. Importer le CSS public et éventuellement les fontes dans le layout de l’hôte.

`useUiClient()` retrouve l’instance fournie, sans abonnement global. `useUiClientSelector(state => state.theme)` permet de rendre un composant sur une sélection précise. Les hooks sont exportés dans `react` et dans l’entrée React principale.

Le client fourni reste possédé par l’hôte. `disposeOnUnmount` permet de déléguer son nettoyage à l’adaptateur. Sinon, l’hôte appelle `dispose()` lorsqu’il abandonne l’instance. L’adaptateur libère toujours ses abonnements et les timers de notifications. Pour annuler une seule opération lors du démontage d’un composant, utiliser un AbortController ou `cancel(channel)` plutôt que disposer le client partagé.

`restorePreferences` est désactivé par défaut sur l’adaptateur autonome : son montage conserve l’état préparé par le contrôleur. L’activer permet de restaurer et mémoriser le thème et le menu dans le stockage local. Le thème résolu est appliqué à l’élément racine du document.

## Backend Next.js

Le backend renvoie des commandes sérialisables ; leur réception met à jour le store de l’instance du navigateur.

```ts
// app/api/preferences/route.ts
import { createUiCommands } from 'reno-plus-chat-template/client';

export async function POST() {
  return Response.json(createUiCommands(
    { type: 'theme.set', value: 'dark' },
    { type: 'sidebar.set', width: 240, collapsed: false },
  ), { headers: { 'Cache-Control': 'no-store' } });
}
```

```ts
await ui.request('/api/preferences', {
  method: 'POST',
  channel: 'preferences',
});
```

La fonction serveur `src/backend/ui-demo.ts`, exposée par `src/app/api/ui-demo/route.ts`, est utilisée depuis `/catalog`, dans « Pilotage de l’interface ». Il pilote le même contrôleur que les composants KM.

## Flux et autres transports

`stream(url, options)` lit du NDJSON : une enveloppe complète par ligne, y compris lorsque le réseau coupe une ligne ou un caractère UTF-8 entre deux blocs. Les commandes s’appliquent au fil de la réception.

```ts
const abort = new AbortController();
void ui.stream('/api/ui-events', {
  channel: 'session',
  signal: abort.signal,
}).catch(error => {
  if (!abort.signal.aborted) console.error(error);
});
// Au démontage : abort.abort().
```

Pour WebSocket ou EventSource, transmettre l’objet décodé à `ui.receive(payload)`. Le transport reste possédé par l’hôte, qui ferme ses connexions au démontage.

Une seule requête est active par canal (`ui` par défaut). Une nouvelle requête sur le même canal annule l’ancienne et empêche sa réponse tardive d’écraser l’état récent. Des canaux différents permettent des opérations concurrentes. Le `fetch` de l’hôte peut être injecté via `createUiClient({ fetch })`.

## Contrat des commandes

```json
{
  "version": 1,
  "commands": [
    { "type": "theme.set", "value": "dark" },
    { "type": "modal.open", "title": "Information", "body": "Texte du serveur" }
  ]
}
```

Les commandes générales couvrent le thème, le menu, le modal partagé, les notifications et les annonces accessibles. Un lot contient au plus 50 commandes. Les champs inconnus, les valeurs invalides et les versions non prises en charge sont rejetés. Le lot est validé intégralement avant application et les notifications des stores sont regroupées. Les lignes NDJSON sont limitées à 1 Mio. Un lot valide déjà reçu dans un flux reste appliqué si une ligne ultérieure échoue.

`UiClientError` expose les codes `protocol`, `target` et `http`, et le statut HTTP lorsqu’il existe. Les erreurs remontent à l’hôte, qui choisit leur affichage. La validation ne remplace pas l’authentification et l’autorisation propres au backend.

## Adaptateur KM optionnel

`RenoProvider` utilise `UiClientProvider` et crée son client par défaut. Il accepte aussi une instance extérieure :

```tsx
<RenoProvider client={ui} initialConversation={initial}>
  <ChatWorkspace />
</RenoProvider>
```

`src/components/chat/ui-client-adapter.ts` branche les opérations du chat et la résolution des citations. Le contrôleur autonome n’importe pas ce module. L’adaptateur est attaché pendant le cycle de vie React, puis détaché au démontage. Les méthodes `chat.send`, `chat.stop`, `chat.retry`, `chat.compare`, `chat.choose` et `feedback.vote` délèguent aux services existants, avec les annotations associées.

Les commandes `draft.set`, `source.open` et `feedback.open` nécessitent cet adaptateur. Un brouillon vise explicitement son `conversationId`, afin qu’une réponse tardive ne modifie pas une autre conversation. Une source doit déjà être citée par une réponse complète de la conversation active ; aucun catalogue documentaire n’est ajouté. Les réponses LLM et leur transport restent régis par le contrat ChatProvider.

## Vérification

`npm run test:client` vérifie le contrôleur autonome, le protocole, les cibles, le regroupement des mises à jour, JSON/NDJSON, l’annulation, les réponses obsolètes et la route Next.js. `npm run check:ui` vérifie les entrées pures et React de la distribution commune, leur interopérabilité et le SSR sans accès au navigateur. Les parcours navigateur du catalogue figurent dans `tests/ui-client.spec.ts`.
