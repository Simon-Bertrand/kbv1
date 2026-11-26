# Recherche documentaire

La WebUI Next.js interroge l’index PostgreSQL existant (`rag.documents`, `rag.chunks` et `rag.text_embeddings`) via `rag.rag_search_hybrid`. Le mode BM25 est disponible dès que l’index contient du texte. Les modes **Vectoriel** et **Hybride** utilisent la distance cosinus, avec fusion RRF en mode hybride.

## Construire et publier les images avec GitLab CI

L’étape `package` de [`.gitlab-ci.yml`](.gitlab-ci.yml) construit et publie les huit images de la stack avec Buildah. Configure les variables dans les paramètres CI/CD du projet GitLab.

| Variable | Configuration |
| --- | --- |
| `REGISTRY_PATH` | Hôte Gitea, sans protocole ni chemin, par exemple `gitea.example.com`. |
| `PIP_INDEX_URL` | Définie en haut de `.gitlab-ci.yml` à partir de `REGISTRY_PATH` et `GITEA_USER` (compte Gitea qui héberge les wheels). |
| `PIP_TRUSTED_HOST` | Hôte Gitea facultatif. Laisser vide pour vérifier le certificat TLS. |
| `GITEA_BASE_REGISTRY` | Préfixe des images applicatives, de base et de CI, défini par défaut à `${REGISTRY_PATH}/${GITEA_USER}`. |
| `GITEA_USER`, `GITEA_PAT` | Identifiants pour lire les images de base et les wheels, et publier les images applicatives dans Gitea. La CI les insère dans l’URL pip transmise à l’étape d’installation. Définir le PAT comme variable masquée. Configurer aussi l’authentification du runner pour tirer les images de job privées avant leur démarrage. |
| `VERSION` | Tag des images publiées et déployées. Par défaut, le SHA complet du commit GitLab. |

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
Le premier script charge les archives vérifiées, pousse les images de base,
construit l’image de job `ci-buildah-helm:latest` et publie l’image `aws-cli:2` utilisée par PdfParse pour la copie S3. Le runner installe directement les wheels de Gitea avec pip, sans cache ni
compilation de dépendances. Il publie les images applicatives sous `GITEA_BASE_REGISTRY`.
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
Docker locales sous `knowledgebase/*:latest` et compile la wheel CUDA de PdfParse
sur la machine de dev. Les builds Compose installent cette wheel et téléchargent
les autres dépendances depuis PyPI et les index spécialisés publics. Aucun
artefact Gitea n’est requis en développement.

Compose monte directement `./data` sur `/data` et `/mnt/e/models` sur `/models` en lecture seule. Tu peux changer les chemins hôte dans `.env` avec `DATA_DIR` et `MODELS_DIR`. Le service PdfParse fixe `KM_SKIP_MOUNTSYNC=1` et démarre sur ces montages locaux. Le conteneur d’initialisation S3 est propre au déploiement Helm.

Les fichiers dans `docker/python/` fixent toutes les versions installées dans
les deux environnements, y compris les dépendances transitives. Après avoir
modifié le verrou, les régénérer sur Linux x86-64 avec Python 3.12 et uv.

```sh
python3.12 -m scripts.docker.pins
```

En préproduction, les jobs de build passent un à la fois. Buildah utilise un
stockage overlay standard du runner. Le job supprime le tag local de
l’image applicative après publication. Le runner doit prendre en charge overlay. Vérifier le pic disque pendant le premier build
PdfParse sur le runner de 30 Go.

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
  existingSecret: km-runtime-secrets

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

Une fois les huit images publiées, lance le job manuel `deploy:preprod` depuis le pipeline de la branche par défaut. Il valide le chart puis installe ou met à jour la release `kbv1` avec le `GITEA_BASE_REGISTRY` et la `VERSION` du pipeline.

| Variable GitLab | Valeur à fournir |
| --- | --- |
| `KUBE_PREPROD_CONTEXT` | Nom du contexte Kubernetes à utiliser, par exemple celui fourni par l’agent GitLab |
| `KUBE_PREPROD_VALUES` | Variable de type **Fichier** contenant les valeurs du site adaptées au cluster |
| `KUBE_NAMESPACE` | Namespace existant de la release. Valeur par défaut `namespace` |
| `KUBE_DEPLOY_TIMEOUT` | Délai d’attente de Helm. Valeur par défaut `12m`. Augmenter ce délai si la première copie S3 prend plus longtemps. |

Le runner doit disposer d’un kubeconfig avec le contexte indiqué. Prépare les Secrets et les PVC référencés par les valeurs du site, avec les poids E5 déjà présents pour que l’encodeur démarre. Chaque nouveau commit fournit à Helm un tag distinct pour que Kubernetes remplace les pods.

## Remplir les volumes du corpus et des modèles avec Helm

### Synchronisation S3 au démarrage de PdfParse

Renseigne le préfixe S3 et la règle réseau dans [`value-pdfparse.yaml`](helm/kbv1/value-pdfparse.yaml). Remplace l’adresse d’exemple `192.0.2.10/32` par la plage IP de ton endpoint S3. Tu peux aussi utiliser les sélecteurs du service qui donne accès à S3 dans le cluster.

```yaml
pdfparse:
  s3Sync:
    prefix: s3://mon-bucket/pdfparse
networkPolicy:
  extraEgress:
    pdfparse:
      - to:
          - ipBlock:
              cidr: 192.0.2.10/32
        ports:
          - protocol: TCP
            port: 443
```

| Source S3 | Chemin dans le conteneur | PVC existant |
| --- | --- | --- |
| `<prefix>/documents/` | `/data` | `km-data` |
| `<prefix>/modeles/` | `/models` | `km-models` |

À la première création du pod, `s3-ingest` copie les fichiers nouveaux ou modifiés et conserve ceux présents uniquement sur les PVC. Après chaque copie réussie, il écrit une marque sur le volume concerné. Les pods suivants sautent la copie des volumes déjà synchronisés pour le même préfixe et la même révision. Si une copie échoue, Kubernetes la retente avant de démarrer le Studio. [Référence AWS CLI](https://docs.aws.amazon.com/cli/latest/reference/s3/sync.html)

Pour récupérer de nouveaux fichiers depuis le même préfixe, change `pdfparse.s3Sync.revision` dans les valeurs Helm, par exemple de `v1` à `v2`. Laisse le préfixe vide si les volumes sont alimentés autrement.

- **Image.** Depuis la machine connectée, `scripts/upload-gitea-images.sh` vérifie et publie AWS CLI sous `<global.imageRegistry>/aws-cli:2`. Pour utiliser une autre image déjà disponible, renseigne `pdfparse.s3Sync.image`. Le pod utilise `km-pull-secrets` pour la tirer.
- **Accès S3.** Pour les copies, crée le Secret `km-s3-secrets` dans le même namespace que PdfParse. Il fournit `AWS_ACCESS_KEY_ID` et `AWS_SECRET_ACCESS_KEY`, ainsi que `AWS_SESSION_TOKEN` si les identifiants sont temporaires. Le compte doit pouvoir lister les deux préfixes et lire leurs objets. Si nécessaire, ajoute `AWS_DEFAULT_REGION` au Secret. Un démarrage qui saute les deux copies n’a pas besoin d’identifiants.
- **Réseau.** Helm exige une règle de sortie lorsque le préfixe est renseigné. La règle s’applique à tout le pod, y compris à `s3-ingest`. Vérifie que l’adresse autorisée correspond à celle du service S3.
- **Volumes.** Les PVC doivent exister et être accessibles en écriture à l’UID 1000. Le modèle reste monté en lecture seule côté PdfParse. Si la première copie prend plus de 12 minutes, augmente `KUBE_DEPLOY_TIMEOUT` dans GitLab.

```sh
NS=namespace
kubectl -n "$NS" get pvc km-data km-models
kubectl -n "$NS" get secret km-pull-secrets km-s3-secrets
kubectl -n "$NS" logs -f deployment/kbv1-pdfparse -c s3-ingest
kubectl -n "$NS" rollout status deployment/kbv1-pdfparse
```

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
