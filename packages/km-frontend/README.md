# Console d’administration KM (km-frontend)

Ce package contient :
- Une interface Web React/Vite (`src/`, `package.json`).
- Une passerelle HTTP FastAPI (`server.py`).

La passerelle relaie les requêtes `/api/{target}/{path}` vers les services KM
(`athena`, `rag`, `knowledge`, `files`, `index`).

## Utilisation et Packaging

### Serveur Python de passerelle

Développement local :
```bash
uv run uvicorn server:app --host 0.0.0.0 --port 8088 --no-proxy-headers
```

Distribution Wheel (`km-frontend`) :
Le fichier `pyproject.toml` déclare la distribution wheel contenant le serveur FastAPI (`server.py`).
Lorsque le wheel est installé hors sources, le dossier des assets statiques peut être configuré via la variable d'environnement `FRONTEND_DIST_DIR` (ou `KM_FRONTEND_DIST`), pointant vers le dossier `dist/` produit par la compilation web.

### Interface Web (React / Vite)

Installation et construction des assets de production :
```bash
npm ci
npm run check
npm test
npm run build
```
Les fichiers statiques sont générés dans `dist/`.

### Dépendances Runtime et Verrouillage

Le projet définit ses dépendances dans `pyproject.toml`. Le fichier `requirements.lock` exporte les versions et empreintes des wheels utilisées par Docker depuis le `uv.lock` du workspace. Depuis la racine du dépôt, cet export se régénère avec la commande suivante.
```bash
python -m scripts.docker.frontend_runtime_requirements
```
Vérification de synchronisation :
```bash
python -m scripts.docker.frontend_runtime_requirements --check
```
