# Déployer KBv1 avec Helm

## PdfParse seul

Le fichier `helm/kbv1/value-pdfparse.yaml` déploie uniquement PdfParse. Le Studio et son API sont accessibles sur `https://km-<nom-add>-01.<domain_name>` avec le Basic Auth de PdfParse. Remplacer `nom-add` par le nom ADD en minuscules, avec des tirets pour séparer les mots.

Dans le namespace de la release, préparer `km-runtime-secrets` avec la clé `PDFPARSE_ADMIN_PASSWORD` (au moins 16 caractères) et `km-pull-secrets` avec les identifiants de lecture du registre. Le nom d’utilisateur du Studio est `studio` par défaut. Renseigner l’Issuer dans les annotations de l’Ingress. Cert-manager génère et renouvelle `km-tls-secret`. Les commandes de gestion sont commentées dans le profil.

Le pod monte le PVC existant `km-data` sur `/data` et le PVC `km-models` sur `/models` en lecture seule. Pour synchroniser les deux volumes au démarrage, renseigner `pdfparse.s3Sync.prefix` et autoriser l’accès réseau à S3. Le conteneur d’initialisation doit pouvoir écrire sur les deux PVC. [Configuration de la synchronisation S3](../../README.md#synchronisation-s3-au-démarrage-de-pdfparse)

```sh
helm lint --strict helm/kbv1 -f helm/kbv1/value-pdfparse.yaml
IMAGE_TAG=$(git rev-parse HEAD)
helm upgrade --install kbv1 helm/kbv1 --namespace namespace \
  -f helm/kbv1/value-pdfparse.yaml --set-string "global.imageTag=$IMAGE_TAG"
```

La commande manuelle suppose que l’image du commit courant a déjà été publiée. Dans GitLab, utiliser le contenu de `helm/kbv1/value-pdfparse.yaml` pour la variable de type fichier `KUBE_PREPROD_VALUES`. La CI fournit le préfixe de registre et le tag de l’image.

## Stack complète

Copier `values-site.example.yaml` en `values-site.yaml` et adapter les valeurs au cluster.

- `global.imageRegistry` correspond à `REGISTRY_PATH` dans GitLab, par exemple `registry.example.test/team/kbv1`.
- `global.imageTag` correspond à `VERSION` dans GitLab, par défaut le SHA du commit. Pour un déploiement manuel, indiquer le tag effectivement publié.
- `global.imagePullSecrets` référence le Secret Docker du namespace lorsque le registry est privé.
- `domain_name` et `ingress.subdomains` définissent les quatre noms DNS. Le Secret TLS `ingress.tlsSecretName` doit contenir un certificat couvrant les noms exposés.
- Prévoir au moins `128Gi` pour le corpus et `64Gi` pour les modèles. Avec un `pvcName` renseigné, Helm monte le PVC existant et sa capacité se gère séparément. Pour que Helm crée un PVC, laisser `pvcName: ""` et indiquer si nécessaire une `storageClassName` compatible `ReadWriteMany`. Les modèles doivent être préchargés. Le volume partagé des versions de documents doit aussi accepter `ReadWriteMany`.
- PdfParse réserve un GPU NVIDIA. Le cluster doit annoncer la ressource `nvidia.com/gpu` sur au moins un nœud.

| Composant | Demandes Kubernetes | Limites Kubernetes |
| --- | --- | --- |
| PdfParse | 8 vCPU, 16 GiB de RAM, 1 GPU NVIDIA | 8 vCPU, 24 GiB de RAM, 1 GPU NVIDIA |
| Encodeur E5 | 12 vCPU, 8 GiB de RAM | 12 vCPU, 12 GiB de RAM |

L’encodeur charge multilingual-e5-large depuis le PVC de modèles et exécute l’inférence sur CPU. Les pools OpenMP et MKL suivent la limite CPU du conteneur. Le nombre de threads et la mémoire réservée donnent un point de départ à valider sous charge sur les nœuds cibles. [Fiche du modèle](https://huggingface.co/intfloat/multilingual-e5-large) · [Réglages des threads PyTorch](https://docs.pytorch.org/docs/stable/threading_environment_variables.html)

```sh
helm lint --strict helm/kbv1 -f values-site.yaml
helm upgrade --install kbv1 helm/kbv1 \
  --namespace kbv1 --create-namespace -f values-site.yaml
```

## Routes HTTPS

| Hôte par défaut | Chemin | Authentification |
| --- | --- | --- |
| `webui.kb.local` | `/` | Session WebUI avec compte local ou OIDC |
| `km.kb.local` | `/` | Basic Auth géré par KM Frontend |
| `studio.kb.local` | `/` | Basic Auth géré par PdfParse Studio |
| `fileserver.kb.local` | `/` | Basic Auth à l’Ingress, puis authentification de l’explorateur |
| `fileserver.kb.local` | `/files` | JWT pour les PDF depuis WebUI ou session de l’explorateur Fileserver |

Les services Kubernetes sont de type `ClusterIP`. PostgreSQL, KM API, l’encodeur et l’indexeur restent internes. La configuration d’Ingress reprend la classe `nginx` et ses annotations Ingress NGINX. Le contrôleur communautaire Ingress NGINX a été retiré en mars 2026 et ne reçoit plus de correctifs. [Annonce de Kubernetes](https://kubernetes.io/blog/2026/01/29/ingress-nginx-statement/)

L’image Fileserver active son explorateur avec `KB_ENV=dev`. Le chart limite ce mode au Fileserver et à la console KM qui l’interroge. Le chart vérifie leurs mots de passe au démarrage. Désactiver uniquement l’explorateur avec `ingress.fileserverUi.enabled=false` conserve la route PDF.

## Secrets et variables

Créer les Secrets dans le namespace de la release avant le déploiement. Les clés d’environnement ci-dessous proviennent du Secret `secrets.existingSecret`.

| Composants | Clés du Secret |
| --- | --- |
| PostgreSQL et clients SQL | `POSTGRES_PASSWORD`, `POSTGRES_AGENT_PASSWORD`, `POSTGRES_KM_READER_PASSWORD`, `POSTGRES_KM_WRITER_PASSWORD`, `POSTGRES_WEBUI_PASSWORD` |
| Échanges internes | `KM_API_TOKEN`, `KM_ENCODER_TOKEN`, `KM_INDEXER_TOKEN`, `KM_INDEXER_ADMIN_TOKEN` |
| Fileserver | `FILESERVER_JWT_SECRET`, `FILESERVER_UI_SECRET`, `FILESERVER_DEV_PASSWORD` |
| Interfaces KM et Studio | `KM_CONSOLE_PASSWORD`, `PDFPARSE_ADMIN_PASSWORD` |
| WebUI | `WEBUI_AUTH_SECRET` et `WEBUI_AUTH_ADMIN_LOGIN` / `WEBUI_AUTH_ADMIN_PASSWORD` en mode `admin`, ou `WEBUI_AUTH_OIDC_ISSUER`, `WEBUI_AUTH_OIDC_ID`, `WEBUI_AUTH_OIDC_SECRET` en mode `oidc` |
| Agent externe, si configuré | `KM_AGENT_TOKEN` pour WebUI et `KM_AGENT_ADMIN_TOKEN` pour KM Frontend |

Utiliser des secrets JWT et de session aléatoires d’au moins 32 caractères. Les mots de passe des interfaces Basic Auth et les jetons KM doivent comporter au moins 16 caractères. Les identifiants sont configurables dans `fileserver.env.devUser`, `kmFrontend.env.consoleUser` et `pdfparse.env.adminUser`.

Pour l’explorateur Fileserver, le Secret Ingress `ingress.fileserverUi.authSecretName` doit contenir une clé `auth` au format htpasswd. Utiliser le même identifiant et le même mot de passe dans htpasswd et dans la configuration Fileserver. Avec les noms par défaut et le namespace `kbv1` :

```sh
auth_file=$(mktemp)
htpasswd -cB "$auth_file" dev
kubectl -n kbv1 create secret generic kbv1-fileserver-basic-auth --from-file=auth="$auth_file"
rm -f "$auth_file"
```

Le chart calcule les URL internes et les URL publiques HTTPS. Les valeurs `webui.env.authUrl`, `webui.env.fileserverPublicUrl` et `fileserver.env.webuiPublicOrigin` permettent de fournir des URL explicites.

## Isolement réseau

Les NetworkPolicies isolent les pods de la release en entrée et en sortie. Elles exigent un CNI qui applique les NetworkPolicies. Les labels des pods DNS et du contrôleur Ingress sont configurables sous `networkPolicy`.

| Source | Destinations autorisées |
| --- | --- |
| Contrôleur Ingress | WebUI, KM Frontend, PdfParse et Fileserver sur leurs ports HTTP |
| WebUI | PostgreSQL, KM API, Fileserver |
| KM Frontend | KM API, indexeur, Fileserver |
| KM API | PostgreSQL, encodeur, Fileserver |
| Indexeur | PostgreSQL, encodeur, KM API |
| Pods KBv1 | DNS du cluster sur TCP et UDP 53 |

Les accès sortants vers un fournisseur OIDC, un agent, un reranker ou un service d’ingestion se déclarent séparément sous `networkPolicy.extraEgress.<composant>`. Utiliser les sélecteurs de pods du service ou les CIDR et ports nécessaires. En mode OIDC, WebUI doit pouvoir accéder à l’issuer HTTPS.
