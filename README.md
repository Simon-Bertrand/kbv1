# Recherche documentaire

La WebUI Next.js interroge l’index PostgreSQL existant (`rag.documents`, `rag.chunks` et `rag.text_embeddings`) via `rag.rag_search_hybrid`. Le mode BM25 est disponible dès que l’index contient du texte. Les modes **Vectoriel** et **Hybride** utilisent la distance cosinus, avec fusion RRF en mode hybride.

## Construire et publier les images avec GitLab CI

L’étape `package` de [`.gitlab-ci.yml`](.gitlab-ci.yml) construit et publie les huit images de la stack avec Buildah. Configure les variables dans les paramètres CI/CD du projet GitLab.

| Variable | Configuration |
| --- | --- |
| `REGISTRY_PATH` | Chemin du registre et de son namespace, par exemple `registry.example.org/team/kbv1`. Par défaut, `CI_REGISTRY_IMAGE`. |
| `REGISTRY_USER`, `REGISTRY_PASSWORD` | Identifiants à fournir pour un registre externe. Le registre GitLab du projet utilise par défaut les identifiants prédéfinis. |
| `PIP_INDEX_URL` | URL de l’index PyPI Gitea, avec `/simple`. Définir cette variable GitLab comme masquée si l’URL contient des identifiants. |
| `PIP_CERT` | Variable GitLab de type fichier pour une autorité de certification interne. |
| `GITEA_BASE_REGISTRY` | Préfixe des images de base et de CI, par exemple `gitea.exemple/organisation`. |
| `GITEA_USER`, `GITEA_TOKEN` | Identifiants de lecture des images de base privées. Configurer aussi l’authentification du runner pour tirer les images de job privées avant leur démarrage. |
| `VERSION` | Tag des images publiées, `1.0.0` par défaut. |

### Préproduction sans accès à Internet

Depuis une machine Linux x86-64 connectée, avec Docker, Python 3.12 et Twine disponibles,
préparer les artefacts du commit à construire puis les publier sur Gitea.

```sh
make prepare-build
docker login gitea.exemple
GITEA_BASE_REGISTRY=gitea.exemple/organisation ./scripts/upload-gitea-images.sh
GITEA_PYPI_UPLOAD_URL=https://gitea.exemple/api/packages/organisation/pypi ./scripts/upload-gitea-wheels.sh
```

Configurer `TWINE_USERNAME` et `TWINE_PASSWORD` avant la publication des wheels.
Le premier script charge les archives vérifiées et pousse les images ainsi que
les images de job Buildah et Helm. Le runner récupère les wheels et leurs
manifestes depuis Gitea et publie les images applicatives vers `REGISTRY_PATH`.
Ce registre doit être accessible au runner et au cluster. Si le runner utilise une image auxiliaire, configurer aussi sa copie sur un registre interne. Pour le profil PdfParse
complet, utiliser `PDFPARSE_WHEEL_PROFILE=pdfparse-full` à la préparation et
dans GitLab CI/CD.

### Développement avec Docker Compose

```sh
make prepare-bases
docker compose up -d --build
```

Renseigner le fichier `.env` à partir de `.env.example`, et préparer les
répertoires de données et de modèles. `make prepare-bases` construit les bases
Docker locales sous `knowledgebase/*:local`. Les builds Compose téléchargent
les artefacts publics verrouillés (PyPI et les sources spécialisées déclarées
dans `uv.lock`) et compilent le kernel CUDA requis par PdfParse. Aucun artefact
Gitea n’est requis en développement.

La synchronisation avec Knowledgebase porte sur `packages/`. Chaque dépôt
maintient son verrou, son workspace et sa configuration de build.



Après la publication, suis la section [Déployer depuis GitLab CI](#déployer-depuis-gitlab-ci).

## Configurer les Deployments, Services et Ingress Kubernetes

Copie le [fichier de valeurs du site](helm/kbv1/values-site.example.yaml) et adapte-le à ton cluster. Helm fusionne ce fichier avec les [valeurs par défaut du chart](helm/kbv1/values.yaml).

```sh
cp helm/kbv1/values-site.example.yaml values-site.yaml
```

Voici un exemple de clés à modifier dans `values-site.yaml` :

```yaml
domain_name: kb.example.org

webui:
  env:
    authMode: admin
    searchMode: hybrid
    chatSeconds: "180"
  service:
    port: 3000

kmFrontend:
  env:
    consoleUser: "admin"

pdfparse:
  env:
    adminUser: "studio"

secrets:
  existingSecret: kbv1-runtime

ingress:
  enabled: true
  className: nginx
  tlsSecretName: kbv1-tls
  subdomains:
    webui: webui
    kmFrontend: km
    pdfparseStudio: studio
    fileserver: fileserver
  fileserverUi:
    enabled: true
    authSecretName: kbv1-fileserver-basic-auth
```

- **Deployments**. Les clés `<composant>.env` configurent les variables prévues par le template de chaque conteneur. Par exemple, `webui.env.searchMode` devient `WEBUI_SEARCH_MODE` et `pdfparse.env.adminUser` devient `PDFPARSE_ADMIN_USER`. Pour ajouter une variable que le chart ne prévoit pas encore, ajoute son entrée `env` dans le [template du Deployment](helm/kbv1/templates/). Utilise `valueFrom.secretKeyRef` si elle contient un secret.
- **Secrets**. Crée le Secret runtime désigné par `secrets.existingSecret` dans le namespace de la release. Les mots de passe, jetons et identifiants WebUI sont lus depuis ce Secret. La [documentation du chart](helm/kbv1/README.md#secrets-et-variables) liste les clés exactes pour chaque composant. Après une rotation du Secret, redémarre les Deployments concernés pour recharger leurs variables d’environnement.
- **Services**. `<composant>.service.port` règle le port interne `ClusterIP`. Par exemple, le Service WebUI reçoit les connexions sur `3000` et les transmet au conteneur sur `4173`.
- **Ingress**. Les hôtes sont construits avec `ingress.subdomains` et `domain_name`, par exemple `webui.kb.example.org`. Configure les entrées DNS et un certificat couvrant les hôtes dans `ingress.tlsSecretName`. Si tu changes de contrôleur, adapte aussi `networkPolicy.ingressController` à son namespace et aux labels de ses pods. Le Secret Basic Auth de l’explorateur Fileserver est référencé par `ingress.fileserverUi.authSecretName`.

Vérifie les manifests avant d’appliquer les valeurs :

```sh
helm lint --strict helm/kbv1 -f values-site.yaml
helm template kbv1 helm/kbv1 --namespace kbv1 -f values-site.yaml
helm upgrade --install kbv1 helm/kbv1 --namespace kbv1 -f values-site.yaml
```

### Déployer depuis GitLab CI

Une fois les huit images publiées, lance le job manuel `deploy:preprod` depuis le pipeline de la branche par défaut. Il valide le chart puis installe ou met à jour la release `kbv1` avec le `REGISTRY_PATH` et la `VERSION` du pipeline.

| Variable GitLab | Valeur à fournir |
| --- | --- |
| `KUBE_PREPROD_CONTEXT` | Nom du contexte Kubernetes à utiliser, par exemple celui fourni par l’agent GitLab |
| `KUBE_PREPROD_VALUES` | Variable de type **Fichier** contenant les valeurs du site adaptées au cluster |
| `KUBE_NAMESPACE` | Namespace existant de la release. Valeur par défaut `kbv1` |

Le runner doit disposer d’un kubeconfig avec le contexte indiqué. Prépare les Secrets et les PVC référencés par les valeurs du site, avec les poids E5 déjà présents pour que l’encodeur démarre. Incrémente `VERSION` pour chaque nouvelle livraison d’images afin que Kubernetes détecte le changement et remplace les pods.

## Remplir les volumes du corpus et des modèles avec Helm

### Depuis S3 et Gitea

Le script [`provision-kbv1-volumes.sh`](scripts/provision-kbv1-volumes.sh) s’exécute dans un Job Kubernetes. Il télécharge les modèles depuis le registre Generic de Gitea et synchronise les documents depuis un préfixe S3. La synchronisation S3 conserve les fichiers présents uniquement sur le PVC.

| Configuration du conteneur | Valeur attendue |
| --- | --- |
| Image | Bash, AWS CLI v2, curl, GNU tar, gzip et les outils GNU de base |
| PVC en écriture | Corpus sur `/data`, modèles sur `/models` |
| `DATA_S3_URI` | Préfixe source, par exemple `s3://kbv1-corpus/documents/` |
| Identité AWS | Rôle du Job ou identifiants injectés par Kubernetes, avec accès en lecture au préfixe |
| `GITEA_GENERIC_URL` | URL HTTPS jusqu’à `/api/packages/<organisation>/generic` |
| `/config/models.tsv` | Manifeste de confiance des archives à télécharger |
| `/secrets/gitea.netrc` | Fichier monté depuis un Secret Kubernetes avec une entrée `machine <hôte> login <compte> password <jeton>` et un jeton en lecture |
| `TMPDIR` | Espace temporaire pouvant accueillir la plus grosse archive, séparé du PVC des modèles |

Le Job doit pouvoir joindre les deux sources. Monte le script et le manifeste depuis un ConfigMap, puis utilise `bash /config/provision-kbv1-volumes.sh` comme commande. Garde les identifiants AWS et Gitea dans des Secrets ou dans l’identité du Job.

Les clés S3 sous le préfixe configuré deviennent des chemins relatifs à `/data`. La synchronisation compare la taille et la date exacte de modification, vérifie les sommes de contrôle S3 lorsqu’elles sont disponibles et autorise jusqu’à huit tentatives par requête. Après un échec, relancer le Job reprend la synchronisation en sautant les fichiers déjà à jour. Pour afficher le plan S3 sans écrire sur les PVC ni contacter Gitea, lance `bash /config/provision-kbv1-volumes.sh --plan-data`.

Chaque ligne de `models.tsv` contient cinq champs séparés par des espaces ou des tabulations. Les archives contiennent directement les fichiers du checkpoint. Le chemin de destination est relatif à `/models`.

```text
# sha256  paquet-gitea  version  fichier  destination
<sha256>  pdfparse-qwen3-5-4b  <revision>  qwen3-5-4b.tar.gz  vlm/qwen3_5_4b
```

Le PVC reprend l’arborescence de `/mnt/e/models`. Par exemple, archive le contenu de `/mnt/e/models/vlm/qwen3_5_4b/` à la racine de l’archive et utilise `vlm/qwen3_5_4b` comme destination. Le checkpoint se retrouvera sous `/models/vlm/qwen3_5_4b/`, sans dossier intermédiaire. La même règle vaut pour les autres familles.

Le script vérifie le SHA-256 avant l’extraction. À la relance, il saute les modèles déjà installés avec la même somme et s’arrête si un dossier cible existe avec une autre somme ou sans marqueur. Pour remplacer un checkpoint, prépare le nouveau volume hors traitement avant de le monter dans les applications.

### Depuis les répertoires locaux

Place les documents dans `./data/` et les modèles dans `./models/` à la racine du dépôt. Une fois le [chart Helm](helm/kbv1/README.md) installé et ses deux PVC disponibles, lance une seule commande pour transférer les deux dossiers.

```sh
bash scripts/upload-kbv1-volumes.sh
```

Le script utilise le contexte `kubectl` actif, le namespace `kbv1` et la release `kbv1` par défaut. Pour d’autres noms, utilise `bash scripts/upload-kbv1-volumes.sh MON_NAMESPACE MA_RELEASE`. Il reprend l’image, les PVC et les Secrets de lecture du registre depuis le Deployment PdfParse. Ton compte Kubernetes doit pouvoir lister et lire les Deployments, créer, surveiller et supprimer le pod temporaire, puis y exécuter des commandes.

Le pod temporaire monte les deux PVC en écriture. Le script vérifie les montages, envoie les dossiers vers `/data` et `/models`, affiche leur contenu, puis supprime le pod. L’arborescence est conservée et les fichiers portant le même chemin sont remplacés. Pour mettre à jour des modèles déjà utilisés, effectue la copie hors traitement et redémarre les consommateurs concernés après le transfert.

### Modèles du profil PdfParse par défaut

| Chemin sous `./models/` | Modèle et rôle |
| --- | --- |
| `picture_classifier/dfc_v25/` | DocumentFigureClassifier v2.5, classification des figures sur CPU |
| `ocr/mineru2_5_pro_2605/` | MinerU2.5-Pro-2605-1.2B, OCR sur `cuda:0` en FP16 |
| `vlm/qwen3_5_4b/` | Qwen3.5-4B, description des figures sur `cuda:0` avec quantification NF4 au chargement |
| `layout/heron/` | Heron, analyse de la mise en page par le pipeline natif Docling |
| `table_structure/tableformer/` | Docling TableFormer, reconstruction des tableaux |
| `embeddings/multilingual_e5_large/` | Le tokenizer sert au découpage structurel de PdfParse lorsqu’il est présent. L’encodeur E5 du même chart a besoin du checkpoint complet pour calculer les embeddings sur CPU. |

Le [profil PdfParse](packages/pdfparse/src/pdfparse/backend/defaults.py) sélectionne les trois premiers modèles. Heron et TableFormer sont utilisés par l’extracteur Docling. Le Studio charge les modèles au moment du traitement.
