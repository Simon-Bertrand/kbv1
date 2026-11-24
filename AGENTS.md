Le projet est censé etre une copie de ../knowledgebase pour les différents packages. Dès que tu modifies un package, demande moi l'autorisation pour refleter la modification dans ../knowledgebase/packages/*

## Environnements de build et de déploiement

Conserver les mêmes Dockerfiles, versions verrouillées et profils de dépendances pour la préproduction et le développement. Paramétrer les différences d’environnement dans GitLab CI/CD, Helm et Docker Compose.

- **Préproduction** : le runner GitLab construit et publie les images avec Buildah. Helm déploie les images publiées. Cet environnement a accès à GitLab et à Gitea, sans accès à Internet. Configurer `PIP_INDEX_URL` dans les variables GitLab CI/CD pour pointer vers l’index PyPI de Gitea. Le build doit y récupérer les wheels aux versions exactes exportées dans `docker/python/`. Les sources publiques inscrites dans `uv.lock` ne doivent déclencher aucun téléchargement public. Rendre disponibles sur Gitea les images de base, les images du runner et les outils de build nécessaires, y compris uv.
- **Développement** : utiliser Docker Compose sur une machine connectée à Internet. Télécharger les wheels depuis PyPI et les autres sources publiques verrouillées. Utiliser le préfixe `knowledgebase` pour les images de base chargées dans Docker local. Préparer les bases locales et la wheel CUDA de PdfParse avec `make prepare-bases`. La commande `docker compose up --build` doit construire et démarrer la stack directement.

Sur une machine connectée, lancer `make prepare-build` pour préparer les images de base et les wheels. Charger les archives Docker puis pousser les images sous les noms et tags attendus dans le registre de conteneurs Gitea. Utiliser `scripts/upload-gitea-images.sh` pour les images et `scripts/upload-gitea-wheels.sh` pour les wheels.

Pour chaque changement de build, garder les chemins `COPY` cohérents avec les contextes utilisés par Compose et Buildah. Valider le démarrage avec Compose en développement et le build avec Buildah sur un runner limité à GitLab et Gitea avant de déclarer les deux parcours opérationnels.

Générer les profils pip avec `python3.12 -m scripts.docker.pins` après toute modification du verrou. Le runner installe les wheels sans cache ni compilation de dépendances. Utiliser le stockage overlay du runner, supprimer le tag local de l’image applicative après le job et limiter les builds à un job à la fois sur le runner de 30 Go.
