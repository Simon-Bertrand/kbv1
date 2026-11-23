#!/bin/sh
set -eu

case "${1:-}" in
    '') publish=yes ;;
    --build-only) publish=no ;;
    *) printf 'Usage: %s [--build-only]\n' "$0" >&2; exit 2 ;;
esac

: "${CI_REGISTRY_IMAGE:?}"
: "${CI_COMMIT_SHA:?}"
if [ "$publish" = yes ]; then
    : "${CI_REGISTRY:?}"
    : "${CI_REGISTRY_USER:?}"
    : "${CI_REGISTRY_PASSWORD:?}"
fi

# The shell runner owns the offline build images prepared for this workspace lock.
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
trap 'rm -rf "$scratch"' EXIT
for service in $services; do
    awk '$1 == "FROM" && $2 ~ /^knowledgebase\// {print $2}
         $1 == "COPY" && $2 ~ /^--from=knowledgebase\// {sub(/^--from=/, "", $2); print $2}' "$(dockerfile "$service")"
done | sort -u > "$scratch/required-images"
while IFS= read -r image; do
    if ! docker image inspect "$image" >/dev/null 2>&1; then
        printf 'Base de build hors ligne manquante sur le runner : %s\n' "$image" >&2
        exit 1
    fi
done < "$scratch/required-images"

for service in $services; do
    file=$(dockerfile "$service")
    case "$service" in
        postgres|fileserver|webui) context=${file%/*} ;;
        *) context=. ;;
    esac
    docker build --network=none --pull=false -f "$file" \
        --label "org.opencontainers.image.revision=$CI_COMMIT_SHA" \
        -t "$CI_REGISTRY_IMAGE/$service:$CI_COMMIT_SHA" "$context"
done
[ "$publish" = yes ] || exit 0
printf '%s\n' "$CI_REGISTRY_PASSWORD" | docker --config "$scratch" login --username "$CI_REGISTRY_USER" --password-stdin "$CI_REGISTRY"
for service in $services; do
    docker --config "$scratch" push "$CI_REGISTRY_IMAGE/$service:$CI_COMMIT_SHA"
done
