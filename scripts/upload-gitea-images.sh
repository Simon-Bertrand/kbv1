#!/bin/sh
set -eu

: "${GITEA_BASE_REGISTRY:?Préfixe du registre Gitea, sans https://}"
cd "$(dirname "$0")/.."
exec "${PYTHON:-python3.12}" -m scripts.docker.build_bundle publish \
    --directory "${TARBALL_DIR:-.tarball}/build" --registry "${GITEA_BASE_REGISTRY%/}"
