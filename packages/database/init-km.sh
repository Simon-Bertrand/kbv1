#!/usr/bin/env bash
set -euo pipefail
if [ "$(id -u)" = 0 ]; then exec gosu postgres "$0" "$@"; fi
: "${POSTGRES_USER:?}"
: "${POSTGRES_DB:?}"
: "${POSTGRES_KM_READER_PASSWORD:?Configurer le mot de passe KM API}"
: "${POSTGRES_KM_WRITER_PASSWORD:?Configurer le mot de passe KM indexeur}"
if [ "${#POSTGRES_KM_READER_PASSWORD}" -lt 16 ] || [ "${#POSTGRES_KM_WRITER_PASSWORD}" -lt 16 ]; then
 echo 'Les mots de passe KM doivent contenir au moins 16 caractères.' >&2; exit 1
fi
psql -X -v ON_ERROR_STOP=1 --host /var/run/postgresql --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<'SQL'
\getenv reader_password POSTGRES_KM_READER_PASSWORD
\getenv writer_password POSTGRES_KM_WRITER_PASSWORD
SET log_statement='none';
SET log_min_error_statement='panic';
SET password_encryption='scram-sha-256';
ALTER ROLE km_reader PASSWORD :'reader_password' LOGIN;
ALTER ROLE km_writer PASSWORD :'writer_password' LOGIN;
SQL
