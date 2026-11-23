# Helm Chart : kbv1 (Environnement de Développement)

Ce chart Helm est l'**équivalent Kubernetes strict de `compose.dev.yaml`** pour l'ensemble du projet **KBv1**. Il orchestre l'intégralité des microservices et interfaces de la plateforme.

---

## 📦 Correspondance Compose <-> Kubernetes

| Service Compose | Rôle & Technologies | Port Conteneur | Port Service Dev | Accès Ingress Dev |
|---|---|---|---|---|
| `postgres` | Base pgvector & RLS (schémas `rag`, `webui`) | `5432` | `5432` / NodePort `35432` | Interne |
| `fileserver` | Serveur de documents et assets Nginx sécurisé | `8080` (health: `8081`) | `8080` / NodePort `38080` | `fileserver.kb.local` |
| `pdfparse` | Moteur d'extraction PDF + **Front Studio** | `8000` | `8000` / NodePort `38000` | `studio.kb.local` |
| `encoder` | Modèle d'embedding `multilingual-e5-large` (FastAPI) | `8001` | `8001` / NodePort `38001` | Interne |
| `km-api` | Recherche documentaire (BM25, sémantique, hybride) + init | `8000` | `8000` / NodePort `38002` | Interne |
| `indexer` | Service d'ingestion et synchronisation vectorielle | `8011` | `8011` / NodePort `38011` | Interne |
| `km-frontend` | **Console d'administration** & visualisation RAG | `8088` | `8088` / NodePort `38088` | `km.kb.local` |
| `webui` | **Interface utilisateur Chat RAG** (Next.js) | `4173` | `3000` / NodePort `33000` | `webui.kb.local` |

---

## 🗄️ Volumes & Persistance

La stack Compose utilisait des volumes Docker et des bind-mounts hôte. Le chart les transpose de manière configurable dans `sharedStorage` :

1. **`corpus` (`./data`)** : Monté dans `fileserver` (`/srv/data`), `pdfparse` (`/data`), `km-api` (`/data`) et `indexer` (`/data`).
2. **`models` (`./models`)** : Monté en lecture seule dans `encoder` (`/models`) et `pdfparse` (`/models`).
3. **`sourceVersions` (`source-versions`)** : Volume partagé avec initialisation des permissions (`source-versions-init`) exécutée en `initContainer`.
4. **`indexerState` (`indexer-state`)** : État d'ingestion de l'indexeur avec alignement UID (`initContainer state-init`).
5. **`pdfparseAssets` (`pdfparse-assets`)** : Stockage temporaire des aperçus PDF.
6. **`postgresData` (`postgres-data`)** : Données persistantes PostgreSQL.

---

## 🔐 Identifiants Basic Auth (`basicAuth`)

Les utilisateurs et mots de passe des Basic Auth pour **PdfParse**, **Fileserver** et **KM Frontend** sont centralisés au tout début de `values.yaml` :

```yaml
basicAuth:
  # 1. Studio d'extraction PDF (FastAPI)
  # NOTE : Le mot de passe doit comporter >= 16 caractères (exigence de sécurité)
  pdfparse:
    user: "studio"
    password: "dev-studio-password"

  # 2. Explorateur web et API du Fileserver (Nginx)
  fileserver:
    user: "dev"
    password: "dev"

  # 3. Console d'administration KM-Frontend (FastAPI)
  # NOTE : En dev, laisser vide "" désactive le Basic Auth.
  # Renseigner un mot de passe pour l'activer (ex: "dev-console-password").
  kmFrontend:
    user: "admin"
    password: ""
```

---

## 🌐 Configuration du domaine et des sous-domaines Ingress

Dans `values.yaml`, vous pouvez configurer le nom de domaine racine `domain_name` ainsi que le nom de sous-domaine de chaque interface :

```yaml
# Nom de domaine racine (ex: kb.local ou 127.0.0.1.nip.io)
domain_name: "kb.local"

ingress:
  enabled: true
  subdomains:
    webui: "webui"            # -> webui.kb.local
    kmFrontend: "km"          # -> km.kb.local
    pdfparseStudio: "studio"  # -> studio.kb.local
    fileserver: "fileserver"  # -> fileserver.kb.local
```

Les URLs internes (`WEBUI_AUTH_URL`, `FILESERVER_PUBLIC_URL`, `WEBUI_PUBLIC_ORIGIN`) s'adaptent automatiquement et dynamiquement à ces valeurs.

---

## Pipeline GitLab

Les branches de merge request valident le rendu Helm. La branche principale et
les tags construisent les huit images hors ligne, les publient dans le registre
GitLab avec le SHA du commit, puis publient le chart configuré pour ce même SHA.

- Le job `images:publish` attend un runner Shell tagué `kbv1-docker`. Le
  runner doit avoir accès au moteur Docker local avec les images de build
  `knowledgebase/*:local` déjà chargées et compatibles avec le `uv.lock`.
- Renseigner `KUBE_DEV_CONTEXT` et `KUBE_DEV_VALUES`, une variable GitLab de
  type fichier fondée sur [values-site.example.yaml](values-site.example.yaml).
  Le namespace, les PVC externes et les Secrets doivent être provisionnés avant
  de déclencher le job manuel. Adapter `KUBE_NAMESPACE` et `WEBUI_DEV_URL` au site.
- Le secret nommé dans `secrets.existingSecret` doit contenir les clés utilisées
  par les références `secretKeyRef` du rendu Helm. Pour la WebUI, fournir
  `WEBUI_AUTH_ADMIN_LOGIN` et `WEBUI_AUTH_ADMIN_PASSWORD`, ou sélectionner
  `webui.env.authMode=oidc` et fournir les trois variables `WEBUI_AUTH_OIDC_*`.
  `global.imagePullSecrets` référence le secret de lecture du registre, distinct
  du secret applicatif.
- Configurer les volumes qui contiennent le corpus et les modèles. Le volume
  des versions PDF doit être partagé entre les Pods. Le PVC PostgreSQL conserve
  les données et le déploiement attend un schéma compatible BM25. Les scripts
  d'initialisation ne sont exécutés que sur une base vide. Pour une base existante,
  appliquer la mise à niveau SQL et recalculer les embeddings dans une opération
  séparée avant de déployer cette version.
- Avec un Ingress TLS, prévoir les noms WebUI et Fileserver dans le certificat.
  Sans Ingress, fournir les URLs publiques réelles de la WebUI et du Fileserver
  ainsi que l'origine WebUI autorisée par le Fileserver.

Le job dev utilise `--atomic --wait` et n'efface aucun PVC. Helm ne migre pas
une instance PostgreSQL déjà initialisée.

## 🚀 Utilisation rapide

Les exemples supposent les images chargées localement, le namespace, le stockage et le Secret `kbv1-runtime` provisionnés.

### 1. Option A : Wildcard DNS dynamique (Zéro config `/etc/hosts`)
En définissant `domain_name: 127.0.0.1.nip.io` :
```bash
helm upgrade --install kbv1 ./helm/kbv1 \
  --set domain_name=127.0.0.1.nip.io \
  --namespace kbv1 --create-namespace
```
Les interfaces sont accessibles immédiatement sur :
- `http://webui.127.0.0.1.nip.io`
- `http://km.127.0.0.1.nip.io`
- `http://studio.127.0.0.1.nip.io`

### 2. Option B : Avec `/etc/hosts`
Ajoutez les entrées locales :
```text
127.0.0.1 webui.kb.local km.kb.local studio.kb.local fileserver.kb.local
```
Puis déployez normalement :
```bash
helm upgrade --install kbv1 ./helm/kbv1 --namespace kbv1 --create-namespace
```

### 2. Validation & Simulation
```bash
# Vérifier la validité syntaxique du chart
helm lint helm/kbv1

# Générer les manifests Kubernetes
helm template kbv1-dev helm/kbv1
```

### 3. Déploiement sur cluster Kubernetes local (k3d, Minikube, Kind)
```bash
helm upgrade --install kbv1 ./helm/kbv1 \
  --namespace kbv1 \
  --create-namespace
```

### 4. Accès direct sans Ingress via Port-Forward
```bash
# WebUI (Chat RAG)
kubectl port-forward svc/kbv1-webui 3000:3000 -n kbv1

# KM Frontend (Console d'administration RAG)
kubectl port-forward svc/kbv1-km-frontend 8088:8088 -n kbv1

# PdfParse Studio (Extraction PDF)
kubectl port-forward svc/kbv1-pdfparse 8000:8000 -n kbv1
```
