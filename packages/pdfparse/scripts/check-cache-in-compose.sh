#!/usr/bin/env bash
# Stream CSV from the running PdfParse container; diagnostics stay on stderr.
set -euo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../../.." && pwd)"
exec docker compose --project-directory "$ROOT" -f "$ROOT/compose.dev.yaml" \
    exec -T pdfparse pdfparse-cachecheck "$@"
