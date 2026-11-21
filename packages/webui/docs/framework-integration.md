# Intégration React et Next.js

`packages/webui/` contient l’application Next.js App Router et sa bibliothèque
réutilisable. Les chemins `src/` et `dist-ui/` de ce guide sont relatifs à ce
package. L’application connectée fournit le [chat Athena](agent-chat.md), la
[recherche documentaire KM](document-search.md) et l’[authentification](authentication.md). `src/app/layout.tsx` reste un Server Component : métadonnées, langue, styles, préférence de thème avant hydratation et conversation initiale sérialisable. Les composants interactifs portent `use client` ; aucun accès au DOM ou au stockage n’a lieu à l’import des entrées publiques.

## Utiliser les composants distribués

Après `npm run build:ui`, le manifeste de `dist-ui/` expose le package React :

```tsx
// app/layout.tsx, côté serveur
import 'reno-plus-chat-template/styles.css';
import 'reno-plus-chat-template/template.css'; // facultatif
import 'reno-plus-chat-template/fonts.css';    // facultatif
```

```tsx
'use client';
import { useState } from 'react';
import { Button, Dialog } from 'reno-plus-chat-template';
import { uiTexts } from 'reno-plus-chat-template/texts';

export function Example() {
  const [open, setOpen] = useState(false);
  return <>
    <Button onClick={() => setOpen(true)}>{uiTexts.catalog.ouvrirUnDialogue}</Button>
    <Dialog open={open} onOpenChange={setOpen} title={uiTexts.common.info}>
      <p>{uiTexts.application.ceContenuUtiliseLeModalPartageVous}</p>
    </Dialog>
  </>;
}
```

`Dialog` fonctionne sans provider ; ajouter `NotificationProvider` autour des composants qui utilisent `useNotifications`. Le CSS ne s’importe pas implicitement depuis JavaScript. Copier `dist-ui/public/pdfjs/` dans `public/pdfjs/` si le lecteur PDF est utilisé. Les documents de démonstration sont facultatifs. React et ReactDOM restent fournis par l’application hôte, sans double bundle.

Les anciens exports de fonctions HTML, `browser` et `interactions`, ont été remplacés par les composants et hooks React. Les entrées `texts`, `texts/fr.json`, `contracts` et les styles sont conservés. Il s’agit d’une migration d’API, pas d’un adaptateur de contrôleurs DOM.

## Brancher un backend

Le contrat `ChatProvider` se trouve dans `src/store/chat/types.ts` et l’export `contracts`. Un Client Component crée l’adaptateur puis le transmet à `RenoProvider` avec une `initialConversation` sérialisable ; les fonctions de transport ne traversent pas la frontière Server/Client.

```tsx
'use client';
import { useState } from 'react';
import { RenoProvider, ChatWorkspace } from 'reno-plus-chat-template';
import type { ChatProvider, Conversation } from 'reno-plus-chat-template/contracts';

export function Chat({ initial }: { initial: Conversation }) {
  const [provider] = useState<ChatProvider>(() => createAuthenticatedProvider());
  return <RenoProvider initialConversation={initial} provider={provider}>
    <ChatWorkspace />
  </RenoProvider>;
}
// createAuthenticatedProvider est l’adaptateur propre à l’application hôte.
```

Le transport utilise `fetch` vers une route serveur de l’hôte, respecte `AbortSignal` et fournit les sources de la réponse. Les secrets fournisseur restent au serveur. L’hôte contrôle l’authentification, l’autorisation des documents, la rétention et l’envoi éventuel des annotations. La distribution `dist-ui/` contient les composants et contrats ; l’application
Next.js de ce package implémente ses routes serveur dans `src/backend/` et
`src/app/api/`. Une application hôte qui importe uniquement la bibliothèque
doit fournir son propre backend.

Les hooks de chat utilisent `useSyncExternalStore`, restaure le stockage dans un effet et annule le transport au démontage. Le rendu initial est identique au serveur et à la première hydratation ; les préférences locales sont ensuite restaurées. Les abonnements, timers et rendus PDF sont nettoyés dans leurs effets.

Le store utilise `dispatch`, des reducers purs et des sélecteurs ciblés. Voir [le contrat de gestion d’état](state-management.md) pour `useUiSelector`, `useUiDispatch`, `useChatSelector`, les actions stables et l’entrée serveur `store`. `useTheme` doit être utilisé sous `UiProvider`, déjà inclus dans `RenoProvider`.

Le [contrôleur UI indépendant](ui-client.md) peut être fourni à `UiClientProvider` sans chatbot, ou à `RenoProvider` via la prop `client`. Le backend renvoie des commandes typées au contrôleur, qui met à jour le store et les composants abonnés.

Une seule distribution `dist-ui/` contient les entrées `client`, `react`, `store`, `contracts`, `texts` et les styles. `npm run build` compile ce package puis l’application ; `build:ui` permet de compiler uniquement le package. Les providers et hooks génériques sont regroupés dans `src/components/providers/`.
