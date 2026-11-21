# Design system React

KM fournit des composants React typés et du CSS natif séparé. Next.js App Router est l’application de référence ; la distribution unique expose des entrées séparées pour le contrôleur, React et les styles. React et ReactDOM sont des pairs facultatifs, requis lors de l’utilisation des composants React.

## Styles

`src/css/index.css` est l’entrée publique des fondations et composants, sans styles applicatifs ni JavaScript. `theme.css` définit les mêmes tokens sémantiques sous `data-theme="light"` et `data-theme="dark"`. Les fontes restent facultatives, locales et accompagnées de leurs licences.

`npm run build` compile le package puis l’application Next.js. `npm run build:ui` utilise le même pipeline pour produire uniquement `dist-ui/` :

- `client.js` : contrôleur TypeScript autonome, sans React ;
- `react.js` : providers et hooks React, avec directive `use client` ;
- `store.js` : moteur et reducers purs ;
- `components.js` : composants clients React, avec directive `use client` préservée ;
- `types/` : contrats et déclarations TypeScript ;
- `reno-ui.css` : fondations et composants indépendants du template ;
- `reno-template.css` : compositions du chat, catalogue, feedback et lecteur PDF ;
- `fonts.css` et `fonts/` : fontes optionnelles aux URL relatives ;
- `texts.js`, `texts/fr.json`, `contracts.js` : entrées utilisables au serveur ;
- `public/pdfjs/` et `public/documents/` : ressources locales à copier dans le répertoire public de l’hôte.

Les moteurs Markdown et PDF sont des chunks locaux chargés à la demande. Le worker PDF utilise `/pdfjs/pdf.worker.min.mjs`. Pour un déploiement sous un préfixe d’URL, adapter les chemins publics des PDF, du worker et des fontes de l’application.

## Composants

Le catalogue `/catalog` présente les couleurs, actions, formulaires natifs, badges, typographie, dialogues, popovers, comparaison A/B et notifications.

`Button` expose les attributs natifs du bouton, les variantes `primary`, `ghost`, `danger` et un type `button` par défaut. `IconButton` impose un nom accessible. Les champs, cases à cocher, radios, listes, curseurs et disclosures utilisent directement les éléments HTML correspondants et les classes CSS.

`Dialog` enveloppe `<dialog>` : `open`, `onOpenChange`, `title`, contenu React, identifiant et classes optionnels. Échap, inertie de l’arrière-plan et restitution du focus proviennent du navigateur. `NotificationProvider` fournit `useNotifications().notify(message, kind)` et place les notifications dans le dialogue actif. Les timers et abonnements se nettoient au démontage ; les erreurs persistent jusqu’à fermeture.

`ResponseComparison` est contrôlé par `responseA`, `responseB`, `state`, `choice` et `onChoose`. Les réponses vides ou identiques ne sont pas sélectionnables. Ce composant ne transforme pas les pouces en récompenses PPO.

`Markdown` échappe le texte pendant le rendu serveur, puis charge le parseur et DOMPurify après hydratation. Un contenu non fiable n’est jamais injecté sans échappement ou assainissement. Les pictogrammes injectés en SVG utilisent uniquement des tracés statiques internes.

`npm run check:ui` vérifie l’import et le rendu serveur sans DOM, les exports/types, les textes distribués et les chemins des fontes.
