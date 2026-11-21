#!/usr/bin/env bash
# Initialisation du compte webui-user pour le schéma webui.
set -euo pipefail
if [ "$(id -u)" = 0 ]; then
    exec gosu postgres "$0" "$@"
fi
: "${POSTGRES_USER:?POSTGRES_USER requis}"
: "${POSTGRES_DB:?POSTGRES_DB requis}"
: "${PGDATA:?PGDATA requis}"
: "${POSTGRES_WEBUI_PASSWORD:?Configurer le mot de passe WebUI}"

if [[ ! "$POSTGRES_DB" =~ ^[a-zA-Z0-9_][a-zA-Z0-9_.-]*$ ]]; then
    echo 'POSTGRES_DB : lettres ASCII, chiffres, _, . et - uniquement.' >&2
    exit 1
fi

psql=(psql -X --set=ON_ERROR_STOP=1 --host /var/run/postgresql --username "$POSTGRES_USER" --dbname "$POSTGRES_DB")

# Règles HBA spécifiques pour webui-user (authentification SCRAM-SHA-256)
hba=$("${psql[@]}" -At --command='SHOW hba_file;')
temporary=$(mktemp "$(dirname "$hba")/.webui-hba.XXXXXX")
trap 'rm -f "$temporary"' EXIT
{
    echo '# BEGIN WEBUI USER'
    printf 'local "%s" "webui-user" scram-sha-256\n' "$POSTGRES_DB"
    printf 'host "%s" "webui-user" 0.0.0.0/0 scram-sha-256\n' "$POSTGRES_DB"
    printf 'host "%s" "webui-user" ::/0 scram-sha-256\n' "$POSTGRES_DB"
    echo 'local all "webui-user" reject'
    echo 'host all "webui-user" 0.0.0.0/0 reject'
    echo 'host all "webui-user" ::/0 reject'
    echo '# END WEBUI USER'
    sed '/^# BEGIN WEBUI USER$/,/^# END WEBUI USER$/d' "$hba"
} > "$temporary"
chmod 600 "$temporary"
mv "$temporary" "$hba"

"${psql[@]}" <<'SQL'
DO $$
BEGIN
    IF EXISTS (SELECT FROM pg_catalog.pg_hba_file_rules WHERE error IS NOT NULL) THEN
        RAISE EXCEPTION 'Règles HBA invalides : webui-user reste NOLOGIN';
    END IF;
    IF NOT pg_catalog.pg_reload_conf() THEN
        RAISE EXCEPTION 'Rechargement HBA refusé : webui-user reste NOLOGIN';
    END IF;
END;
$$;
SQL

# Affectation sécurisée du mot de passe
"${psql[@]}" <<'SQL'
\getenv webui_password POSTGRES_WEBUI_PASSWORD
SET log_statement = 'none';
SET log_min_error_statement = 'panic';
SET password_encryption = 'scram-sha-256';
ALTER ROLE "webui-user" PASSWORD :'webui_password' LOGIN;
SQL
