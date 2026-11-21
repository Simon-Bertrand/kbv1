#!/usr/bin/env bash
# Run the corpus CLI in the existing PdfParse container and follow its log.
set -euo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../../.." && pwd)"
COMPOSE=(docker compose --project-directory "$ROOT" -f "$ROOT/compose.dev.yaml")
LOG=/tmp/pdfparse_run.log

# Create and reset the log before launching, so tail can open it immediately.
"${COMPOSE[@]}" exec -T pdfparse sh -c ': > /tmp/pdfparse_run.log'
"${COMPOSE[@]}" exec -T -d pdfparse sh -c \
    'exec pdfparse-run "$@" >> /tmp/pdfparse_run.log 2>&1' sh "$@"
echo "pdfparse-run lancé ; suivi de $LOG (Ctrl+C quitte le suivi)."
"${COMPOSE[@]}" exec -T pdfparse tail -n +1 -f "$LOG"
