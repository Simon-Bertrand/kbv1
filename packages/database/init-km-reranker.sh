#!/usr/bin/env bash
set -euo pipefail
if [ "$(id -u)" = 0 ]; then exec gosu postgres "$0" "$@"; fi
if [ -z "${POSTGRES_KM_RERANKER_PASSWORD:-}" ]; then
    if [ "${1:-}" = "--if-configured" ]; then exit 0; fi
    echo 'Configurer POSTGRES_KM_RERANKER_PASSWORD.' >&2
    exit 1
fi
if [ "${#POSTGRES_KM_RERANKER_PASSWORD}" -lt 16 ]; then
    echo 'Le mot de passe SQL du reranker doit contenir au moins 16 caractères.' >&2
    exit 1
fi
psql -X -v ON_ERROR_STOP=1 --host /var/run/postgresql --username "${POSTGRES_USER:?}" --dbname "${POSTGRES_DB:?}" \
    --file /usr/local/share/km-reranker-role.sql --file - <<'SQL'
\getenv reranker_password POSTGRES_KM_RERANKER_PASSWORD
SET log_statement='none';
SET log_min_error_statement='panic';
SET password_encryption='scram-sha-256';
ALTER ROLE km_reranker PASSWORD :'reranker_password' LOGIN;
SQL
