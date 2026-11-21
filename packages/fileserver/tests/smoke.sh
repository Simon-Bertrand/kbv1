#!/usr/bin/env bash
# Full Docker integration test; the same HTTP checks can run against local NGINX.
set -Eeuo pipefail
cd "$(dirname "$0")/.."
for cmd in docker curl python3; do command -v "$cmd" >/dev/null || { echo "Missing $cmd" >&2; exit 1; }; done
DATA_DIR="$(mktemp -d)"
FILESERVER_PORT="${FILESERVER_PORT:-18080}"
PROJECT="staticzip-smoke-$$"
SOURCE_VERSIONS_DIR="$(mktemp -d)"
export DATA_DIR FILESERVER_PORT SOURCE_VERSIONS_DIR
cleanup() {
    status=$?
    if (( status != 0 )); then docker compose -p "$PROJECT" logs --no-color >&2 || true; fi
    docker compose -p "$PROJECT" down -v --remove-orphans >/dev/null 2>&1 || true
    rm -rf "$DATA_DIR" "$SOURCE_VERSIONS_DIR"
}
trap cleanup EXIT
python3 tests/integration.py --data-dir "$DATA_DIR" --prepare

docker compose -p "$PROJECT" up -d --build
BASE="http://127.0.0.1:${FILESERVER_PORT}"
for i in $(seq 1 50); do
    if curl -fsS "$BASE/" -o /dev/null; then break; fi
    sleep 1
done
python3 tests/integration.py --data-dir "$DATA_DIR" --url "$BASE"
# A respawned worker could hide a crash from a later successful request.
if docker compose -p "$PROJECT" logs --no-color 2>&1 | grep -E 'exited on signal|\[alert\]|\[emerg\]'; then
    echo 'ERROR: NGINX worker failure' >&2
    exit 1
fi
printf '\nPASS Docker smoke\n'
