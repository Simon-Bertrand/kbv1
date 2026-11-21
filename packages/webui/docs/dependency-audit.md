# Vérification des versions — 3 novembre 2026

Relevé historique du 3 novembre 2026, conservé sans nouvelle consultation du
registre ni envoi de la liste des dépendances. Les versions actuellement
déclarées sont dans [package.json](../package.json) et
[package-lock.json](../package-lock.json) ; ce relevé ne constitue pas un audit
de sécurité à jour.

Le projet n’utilise pas les dernières versions stables partout. Les versions déclarées des dépendances directes correspondent à celles du package-lock.json. Les versions publiques ci-dessous proviennent du registre npm, des pages officielles npm ou des releases des mainteneurs ; les pages indexées peuvent avoir un retard de publication.

| Dépendance | Projet | Dernière stable observée | Résultat |
| --- | --- | --- | --- |
| next | 16.4.3 | 16.4.5 | Mise à jour disponible |
| react / react-dom | 19.2.8 | 19.3.1 | Mise à jour disponible, garder les deux versions alignées |
| typescript | 6.0.3 | 7.0.2 | Migration majeure |
| marked | 15.0.12 | 18.1.3 | Migration majeure |
| pdfjs-dist | 5.7.284 | 6.5.438 | Migration majeure |
| @playwright/test | 1.63.0 | 1.64.1 | Mise à jour disponible |
| @types/react | 19.2.18 | 19.3.0 | À aligner avec React |
| @types/react-dom | 19.2.4 | 19.3.0 | À aligner avec ReactDOM |
| @types/node | 25.9.8 | 26.6.6 | Choisir la branche correspondant au runtime Node ciblé |
| dompurify | 3.4.17 | 3.4.17 | Aucun écart détecté sur la page officielle consultée |
| rolldown | 1.2.16 | 1.2.16 | Aucun écart détecté sur la page officielle consultée |
| lightningcss | 1.33.0 | 1.33.0 | Aucun écart détecté sur la page officielle consultée |
| postcss (override) | 8.5.28 | 8.5.32 | Mise à jour de l’override disponible |

L’environnement utilise Node 24.15.0 et npm 11.12.1. Le registre publie npm 12.2.1. Le site officiel Node.js présente 26.13.0 comme dernière LTS et 24.22.2 comme version LTS de la branche 24. Le champ `engines.node` du projet exprime un minimum, pas une version installée. Ne pas choisir automatiquement la dernière majeure des types Node si le runtime reste sur une autre branche.

## Points de compatibilité

- TypeScript 7 ne fournit plus l’ancienne API du compilateur. Retirer `experimental.useTypeScriptCli: false` dans next.config.ts et vérifier les scripts de génération des déclarations, notamment le chemin du binaire tsc. La documentation livrée avec Next.js décrit cette transition dans `node_modules/next/dist/docs/01-app/03-api-reference/05-config/01-next-config-js/useTypeScriptCli.md`.
- PDF.js 6 demande de vérifier le lecteur, les API de rendu, les nouveaux assets/éventuels workers et les ressources synchronisées dans public/pdfjs/. Le moteur et ses workers doivent rester alignés et locaux.
- Marked 18 demande de vérifier les signatures TypeScript et le rendu Markdown assaini avec DOMPurify.
- Après mise à jour de Playwright, préparer aussi la version de Chromium attendue pour les tests.
- Après modification des versions, régénérer le lockfile et préparer le cache avec `make deps`, puis vérifier `make deps-offline`, le typage, les tests du modèle, les builds Next.js/React, le SSR et les parcours navigateur.

## Limite de cette vérification

`npm outdated --json --cache .deps/npm --fetch-retries=0 --fetch-timeout=10000` échoue ici avec `EAI_AGAIN registry.npmjs.org`. Le contrôle de tous les paquets transitifs et de leurs tags actuels n’a donc pas pu être réalisé. Aucune version du manifeste ou du lockfile n’a été modifiée : les nouvelles archives ne peuvent pas être installées et testées dans cet environnement actuellement.

## Sources officielles

- [Next.js : registre latest](https://registry.npmjs.org/next/latest)
- [React : registre latest](https://registry.npmjs.org/react/latest), [ReactDOM](https://www.npmjs.com/package/react-dom?activeTab=versions)
- [TypeScript : registre latest](https://registry.npmjs.org/typescript/latest)
- [Marked](https://www.npmjs.com/package/marked?activeTab=versions)
- [PDF.js : release v6.5.438](https://github.com/mozilla/pdf.js/releases/tag/v6.5.438)
- [Playwright](https://www.npmjs.com/package/%40playwright/test?activeTab=versions)
- [Types React](https://www.npmjs.com/package/%40types/react), [ReactDOM](https://www.npmjs.com/package/%40types/react-dom?activeTab=versions), [Node](https://www.npmjs.com/package/%40types/node?activeTab=versions)
- [DOMPurify](https://www.npmjs.com/package/dompurify), [Rolldown](https://www.npmjs.com/package/rolldown), [Lightning CSS](https://www.npmjs.com/package/lightningcss?activeTab=versions)
- [PostCSS](https://www.npmjs.com/package/postcss?activeTab=versions)
- [Node.js : versions disponibles](https://nodejs.org/en/download)
- [npm : registre latest](https://registry.npmjs.org/npm/latest)
