#!/bin/sh
set -eu

: "${GITEA_PYPI_UPLOAD_URL:?URL Gitea de publication, sans /simple}"
: "${TWINE_USERNAME:?Identifiant Gitea}"
: "${TWINE_PASSWORD:?Jeton Gitea}"

set --
for wheel in "${WHEELHOUSE_DIR:-.wheelhouse}"/wheels/*.whl; do
    [ -f "$wheel" ] || continue
    case "${wheel##*/}" in
        kbv1_wheelhouse_*) continue ;;
    esac
    set -- "$@" "$wheel"
done
if [ "$#" -eq 0 ]; then
    printf 'Aucune wheel trouvée. Lancer make prepare-build.\n' >&2
    exit 1
fi
exec "${PYTHON:-python3}" -m twine upload --non-interactive \
    --repository-url "$GITEA_PYPI_UPLOAD_URL" "$@"
