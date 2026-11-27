#!/bin/sh
set -eu

case "${1:-}" in
    '') publish=yes ;;
    --build-only) publish=no ;;
    *) printf 'Usage: %s [--build-only]\n' "$0" >&2; exit 2 ;;
esac

: "${CI_REGISTRY_IMAGE:?}"
: "${CI_COMMIT_SHA:?}"
: "${PIP_INDEX_URL:?Set PIP_INDEX_URL to the Gitea PyPI index}"
: "${GITEA_USER:?}" "${GITEA_PAT:?}"
export PIP_INDEX_URL="https://${GITEA_USER}:${GITEA_PAT}@${PIP_INDEX_URL#https://}"
export PIP_TRUSTED_HOST="${PIP_TRUSTED_HOST:-}"
if [ "$publish" = yes ]; then
    : "${CI_REGISTRY:?}"
    : "${CI_REGISTRY_USER:?}"
    : "${CI_REGISTRY_PASSWORD:?}"
fi

build_image_prefix=${BUILD_IMAGE_PREFIX:-knowledgebase}
build_image_prefix=${build_image_prefix%/}
export build_image_prefix

# Resolve the same image references that Docker will use in the build.
services='postgres fileserver pdfparse encoder indexer km-api km-frontend webui'
dockerfile() {
    case "$1" in
        postgres) printf '%s\n' packages/database/Dockerfile ;;
        encoder) printf '%s\n' packages/embeddings/Dockerfile ;;
        indexer) printf '%s\n' packages/embeddings/indexer/Dockerfile ;;
        *) printf 'packages/%s/Dockerfile\n' "$1" ;;
    esac
}

scratch=$(mktemp -d)
trap 'rm -rf -- "$scratch"' EXIT
for service in $services; do
    awk '
        $1 == "FROM" && $2 ~ /^\$\{BUILD_IMAGE_PREFIX\}\// {
            sub(/^\$\{BUILD_IMAGE_PREFIX\}/, ENVIRON["build_image_prefix"], $2)
            print $2
        }
    ' "$(dockerfile "$service")"
done | sort -u > "$scratch/required-images"
while IFS= read -r image; do
    if [ "$build_image_prefix" != knowledgebase ]; then
        docker pull "$image"
    elif ! docker image inspect "$image" >/dev/null 2>&1; then
        printf 'Base de build hors ligne manquante sur le runner : %s\n' "$image" >&2
        exit 1
    fi
done < "$scratch/required-images"

for service in $services; do
    file=$(dockerfile "$service")
    context=${file%/*}
    set --
    case "$service" in
        pdfparse|encoder|indexer|km-api|km-frontend)
            set -- --build-context workspace=. --provenance=false \
                --build-arg PIP_INDEX_URL --build-arg PIP_TRUSTED_HOST
            if [ "$service" = pdfparse ]; then
                set -- "$@" --build-arg "PDFPARSE_WHEEL_PROFILE=${PDFPARSE_WHEEL_PROFILE:-pdfparse}"
            fi
            ;;
    esac
    docker build "$@" --network=default --pull=false -f "$file" \
        --build-arg "BUILD_IMAGE_PREFIX=$build_image_prefix" \
        --label "org.opencontainers.image.revision=$CI_COMMIT_SHA" \
        -t "$CI_REGISTRY_IMAGE/$service:$CI_COMMIT_SHA" "$context"
done
[ "$publish" = yes ] || exit 0
printf '%s\n' "$CI_REGISTRY_PASSWORD" | docker --config "$scratch" login --username "$CI_REGISTRY_USER" --password-stdin "$CI_REGISTRY"
for service in $services; do
    docker --config "$scratch" push "$CI_REGISTRY_IMAGE/$service:$CI_COMMIT_SHA"
done
