# NGINX StaticZIP — audited package

Construction Docker : préparer les dépendances avec `make prepare-build` depuis
la racine sur une machine connectée, ou charger leur bundle avec `make load-build`
hors ligne. Voir le [guide hors réseau](../../README.md#utilisation-hors-réseau).

A read-only file explorer built from **NGINX**, **njs**, and the third-party
**mod_zip** module. NGINX serves raw files (sendfile, ETag, Range); the UI is
static HTML/CSS/JS. njs filters NGINX's **internal** native JSON autoindex and
produces manifests; mod_zip streams ZIP archives without writing them to disk.

## Quick start

```bash
# From the repository root:
cd packages/fileserver
# The shared configuration can be passed with --env-file ../../.env.
export DATA_DIR=/srv/storage   # MUST exist on the Docker host
export FILESERVER_PORT=8080
docker compose up -d --build
curl -i http://localhost:8080/
curl -sS 'http://localhost:8080/api/list?path=' | python3 -m json.tool
```

Open `http://localhost:8080/` on the Docker host, or `http://SERVER_IP:8080/`
from another machine. NGINX binds port **8080 inside the container**.

If the host directory is different, change `DATA_DIR`; Compose will fail rather
than silently create a missing host bind directory.

The root dev and prod Compose files include this service with environment-dependent behavior.
In development (`KB_ENV=dev`), the web UI and `/api/*` endpoints are accessible for exploration.
In production (`KB_ENV=production`), the file explorer UI and listing/ZIP APIs are strictly disabled;
Fileserver serves exclusively immutable versioned PDF files (`/files/_versions/<sha256>.pdf`) directly
to the browser under short-lived JWT authorization from WebUI. See [PROD_SECURITY.md](PROD_SECURITY.md) and [MICROSERVICES.md](../../MICROSERVICES.md).

## Security & Production Architecture

Pour la spécification complète de la sécurité en production, les schémas décisionnels et la défense en profondeur (validation JWT, contrôle de provenance WebUI `Origin`/`Referer`/`Sec-Fetch-Site`), consulter :
👉 **[PROD_SECURITY.md](PROD_SECURITY.md)**

* **En Développement (`KB_ENV=dev`)** : L'accès à l'interface web (`GET /`) est protégé par une authentification HTTP Basic Auth configurable (`FILESERVER_DEV_USER` / `FILESERVER_DEV_PASSWORD`, défaut `dev` / `dev`). Une fois authentifié, un cookie de session permissive `fs_ui_session` autorise les API d'exploration (`/api/list`, `/api/zip`) et le téléchargement des fichiers bruts (`/files/...`).
* **En Production (`KB_ENV=production`)** : L'UI d'exploration (`GET /`) et les endpoints de listing sont strictement désactivés (403 Forbidden). Les variables de Basic Auth sont ignorées et déclenchent un warning au boot si présentes. Seule la route unitaire `/files/...` est servie sous réserve d'un jeton JWT valide émis par WebUI (`iss: "webui"`) et d’une origine WebUI explicitement autorisée, y compris si ses fichiers sont servis depuis une autre origine.

## Routes

| Route | Description |
| --- | --- |
| `/` | Static web interface |
| `/api/list?path=subfolder` | Filtered JSON listing from NGINX autoindex |
| `/files/subfolder/report.pdf` | Direct NGINX static download, HTTP Range/HEAD/ETag |
| `/api/zip?path=subfolder` | Stream a ZIP including `subfolder/` as top-level |

Examples:

```bash
curl -v -H 'Range: bytes=0-1023' 'http://localhost:8080/files/report.pdf' -o /tmp/first1024
curl -fsS 'http://localhost:8080/api/zip?path=some-folder' -o some-folder.zip
unzip -t some-folder.zip
```

## Filtering & security model

Any path *component* containing `.pdfparsecache` is excluded. Dotfiles and
dot-directories are also blocked (for example `.env`, `.git`), as are symbolic
links. Files whose names contain `.pdfparsecache` are also excluded. The rule
applies to listings, raw URLs, and ZIP members. Files with control characters
or backslashes in their names are excluded from JS listings/archives to avoid
ambiguous ZIP names. NGINX regex checks operate on normalized request URIs.

`/_index/` and `/_zipfiles/` are internal-only NGINX locations and cannot be
accessed by ordinary clients. Data is mounted read-only. This is **not** an
access-control system for different users. Bind to LAN/VPN only or add auth/TLS
before exposing publicly; do not publish this service on the open Internet
without access control.

## Limits and caveats

- ZIP generation synchronously walks metadata in an NGINX worker, up to 5,000
  ZIP entries, 32 directory levels, and a 2 MiB manifest. A single extremely
  wide directory can still make `readdirSync` expensive. For very large trees,
  use pre-generated archives or an asynchronous archiving backend instead.
- Streaming ZIP entries use **STORE** (no compression), which avoids CPU for
  PDF/ZIP-like data. ZIP requests do **not** support HTTP Range resume, because
  the CRC32 of each file was not precomputed; direct `/files/` downloads do.
- A ZIP is not a stable snapshot; concurrent host-side modifications while a
  download is underway can invalidate the result. Read-only bind mounts alone
  cannot prevent changes made by other host processes.
- Directory filenames containing ZIP-illegal control chars or Windows-ambiguous
  backslashes are skipped; directories with more than 32 levels return HTTP 413.
- The module is built from mod_zip 1.3.0 (source archive verified by SHA-256),
  against the same Alpine NGINX package used at runtime. `mod_zip-njs.patch`
  guards the absent upstream for njs-generated manifests and orders the ZIP
  filter before the copy filter in the module list (filters execute in reverse
  order). Without these fixes, ZIP requests crash workers or return bad CRC32s.
  The compiler and build dependencies stay in the build stage.
- HTTPS/auth, global recursive search, checksum API, ZIP caching, and archive
  resume are deliberately **not** included.
- NGINX's `open_file_cache` caches file metadata/handles (not ZIP files).
- Compose mounts `/var/lib/nginx/tmp` as tmpfs for Alpine NGINX temporary
  files, alongside `/var/run` and `/var/cache/nginx`, with a read-only root.

## Validation

```bash
node --check web/app.js
node --check --input-type=module < njs/explorer.js
node tests/logic.mjs
bash tests/smoke.sh  # Docker Compose + curl + python3 required
```

The Dockerfile runs `nginx -t` during the build. If a module is ABI-incompatible
with the selected NGINX image, the build fails early. Restart/recreate the
container after rebuilding: an NGINX configuration reload does not reliably
replace an already loaded dynamic module.

The HTTP suite checks actual ZIP signatures, CRC32, complete member lists and
file bytes, including empty files/directories, nested Unicode names with spaces,
`#?%+`, a 2 MiB binary file, 100 files, the storage root, HEAD, Range and errors.
It also checks listings, raw file ranges and filtering. The fixture directory
is traversable by the NGINX worker; a plain `mktemp -d` directory is not.

These checks also run against a local NGINX instance without Docker, using a
**disposable** directory already configured as its data root:

```bash
python3 tests/integration.py --data-dir /tmp/staticzip-test-data --prepare
python3 tests/integration.py --data-dir /tmp/staticzip-test-data --url http://127.0.0.1:18080
```

Validated with Alpine NGINX 1.30.6, njs 0.9.9 and the patched mod_zip 1.3.0
through real HTTP requests. Docker image/Compose execution requires Docker;
the local HTTP run does not validate container mounts or the Docker build.

## Sources

- https://github.com/evanmiller/mod_zip
- https://nginx.org/en/docs/njs/reference.html
- https://nginx.org/en/docs/http/ngx_http_autoindex_module.html
- https://nginx.org/en/docs/http/ngx_http_core_module.html

Le lecteur WebUI reçoit un JWT limité au chemin exact, valable cinq minutes.
Fileserver exige HS256, `iss=webui`, `aud=fileserver`, `sub`, `path`, `iat`, `exp`
et une durée maximale d’une heure. `WEBUI_PUBLIC_ORIGIN` configure l’unique
origine CORS ; les réponses 401 exposent leur statut au lecteur pour renouveler.
La probe locale est `http://127.0.0.1:8081/health` et ne dépend pas d’un User-Agent.
Les logs d’accès utilisent `$uri` sans les paramètres contenant le JWT.
