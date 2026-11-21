# HTML et CSS natifs dans React

La migration conserve les contrôles du navigateur : `<dialog>`, formulaires, `<details>/<summary>`, popovers, champs fichier, listes, radios, cases à cocher et curseurs. React orchestre leur état et leurs événements ; les anciens contrôleurs génériques de mutation du DOM ont disparu.

Le thème repose sur les propriétés personnalisées, les cascade layers et `data-theme`. Le responsive utilise Grid, Flexbox, container queries et `:has()` pour les états du composeur. `field-sizing: content` améliore le textarea ; une taille redimensionnable manuellement sert de repli. Les mouvements respectent `prefers-reduced-motion`.

Le tiroir mobile est un dialogue natif. Le menu desktop se replie à 60 px et conserve séparément sa largeur personnalisée. La séparation est utilisable au pointeur et au clavier. Les sources ouvrent seulement le document cité ; PDF.js est chargé localement à la demande et annulé à la fermeture.

Cible : navigateurs modernes disposant des dialogues et popovers natifs (Chromium 114+, Firefox 125+, Safari 17+). Les versions dépourvues de `closedby` gardent la fermeture par Échap et par bouton ; un repli au pointeur permet aussi de fermer le tiroir par clic extérieur ; `field-sizing` reste une amélioration progressive.
