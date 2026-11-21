# Stores et rendu ciblé

KM utilise un store classique : `getState()`, `subscribe()` et `dispatch(action)`, avec des reducers purs et des sélecteurs. Aucun package de gestion d’état supplémentaire n’est requis. `useSyncExternalStore` relie les abonnements à React.

## Utiliser le store UI

`RenoProvider` inclut déjà `UiProvider`. Pour utiliser uniquement les composants UI, placer `UiProvider` dans un layout commun aux pages concernées.

```tsx
'use client';
import { Icon, useUiDispatch, useUiSelector } from 'reno-plus-chat-template';
import { uiTexts } from 'reno-plus-chat-template/texts';

export function SidebarToggle() {
  const collapsed = useUiSelector(state => state.sidebar.collapsed);
  const dispatch = useUiDispatch();
  return <button aria-expanded={!collapsed} aria-label={collapsed ? uiTexts.sidebar.deplierLeMenu : uiTexts.common.replierLeMenu}
    onClick={() => dispatch({ type: 'sidebar/toggled' })}>
    <Icon name={collapsed ? 'panelExpand' : 'panelCollapse'} />
  </button>;
}
```

L’action passe dans `uiReducer`. Le changement du menu conserve les références des annotations et des sources. Seuls les abonnements dont la sélection change sont notifiés. Les autres causes habituelles de rendu React (props, état local, autre contexte ou parent) restent possibles.

Les actions UI couvrent le thème, le menu, les sources, les modals de feedback, les annotations et les annonces accessibles. Les préférences sont restaurées dans un effet et les écritures de préférences restent en dehors du reducer. Les états strictement locaux, comme la recherche, le pointeur de redimensionnement et la position responsive, restent locaux aux composants.

## Chat

`useChatSelector(selector, equality?)` lit un fragment du `ChatState`. `useChatDispatch()` donne accès aux actions pures du chat. `useRenoActions()` fournit des services stables : `chat.send()`, `chat.stop()`, sauvegarde des annotations, préférences A/B et notifications. Le transport, les timers, les UUID et le stockage restent en dehors des reducers.

```tsx
'use client';
import { useChatSelector, useRenoActions } from 'reno-plus-chat-template';
import { uiTexts } from 'reno-plus-chat-template/texts';

export function GenerationStatus() {
  const busy = useChatSelector(state => state.busy);
  const { chat } = useRenoActions();
  return <button disabled={!busy} onClick={() => chat.stop()}>
    {uiTexts.chat.arreterLaReponse}
  </button>;
}
```

Le contexte conserve l’instance stable des services et stores, pas une copie de l’état à chaque mise à jour. Le composeur sélectionne seulement son brouillon, ses pièces jointes et l’état occupé. La liste sélectionne les identifiants des messages ; chaque message sélectionne son propre objet. Le titre et le menu ne s’abonnent pas au contenu des réponses. Un observateur séparé suit les changements de messages pour préserver la lecture pendant le streaming.

Un reducer remplace uniquement la branche modifiée. Ne jamais modifier un snapshot ou une charge utile après son dispatch. Les messages inchangés gardent leur identité ; modifier un brouillon conserve le tableau des messages. Un dispatch sans changement conserve le snapshot et ne notifie personne.

## Sélections composées

Pour sélectionner une référence déjà présente dans l’état, la comparaison par défaut `Object.is` suffit. Pour construire un petit tableau ou objet, passer `shallowEqual` :

```tsx
const [theme, collapsed] = useUiSelector(
  state => [state.theme, state.sidebar.collapsed] as const,
  shallowEqual,
);
```

La sélection est mise en cache entre les changements. Ne pas faire de mutation, d’accès au DOM, d’effet réseau ou d’écriture au stockage dans un reducer ou un sélecteur. Une comparaison personnalisée doit comparer toutes les données utilisées par le composant ; ignorer une propriété crée un affichage périmé.

## Next.js, SSR et distribution

Les providers créent leur store une fois par instance montée, sans singleton partagé entre requêtes serveur. Le snapshot initial reste stable pour le rendu serveur et l’hydratation, puis les préférences locales sont restaurées côté navigateur. Placer le provider dans un layout commun conserve son état lors des navigations correspondantes. Un changement du store réagit dans les Client Components abonnés ; il ne relance pas les Server Components et ne remplace pas une mutation ou une revalidation serveur.

Les sources `src/store/` restent pures ; les liaisons React sont dans `src/components/providers/`. L’entrée `reno-plus-chat-template/store` exporte le moteur, `selectStore`, `shallowEqual`, les reducers et les contrats, sans React, DOM ou stockage. Elle permet de préparer un état initial au serveur ; toujours créer les instances par requête/arbre de rendu. Les hooks et providers restent dans l’entrée React principale avec `use client`.

Les anciens hooks `useReno()` et `useChat()` sont conservés comme façades de compatibilité qui s’abonnent à l’état complet. Préférer les sélecteurs dans les nouveaux composants. `ChatModel.getSnapshot()` renvoie désormais un snapshot immuable en convention, et non un numéro de révision ; les consommateurs doivent utiliser les actions pour modifier l’état.

## Vérification

- `npm run test:store` : reducers sur des états gelés, références préservées, notifications ciblées, snapshots SSR et isolation des instances.
- `npm run test:chat` : génération, interruption, comparaison, contexte, restauration et abonnements.
- `npm run check:ui` : imports de l’entrée pure sans DOM et rendu SSR des composants.
- `npm test -- store.spec.ts` : comptage des rendus React avec les vrais providers et le chat monté, puis nettoyage au démontage.

Ces tests vérifient l’isolation des abonnements ; ils ne constituent pas un benchmark de latence ou une garantie de performance pour toutes les tailles de conversations.
