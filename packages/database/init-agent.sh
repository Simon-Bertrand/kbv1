#!/usr/bin/env bash
# Nouvelle base : exécuté automatiquement. Base existante : init-rag-agent.
set -euo pipefail
if [ "$(id -u)" = 0 ]; then
    exec gosu postgres "$0" "$@"
fi
: "${POSTGRES_USER:?POSTGRES_USER requis}"
: "${POSTGRES_DB:?POSTGRES_DB requis}"
: "${PGDATA:?PGDATA requis}"
: "${POSTGRES_AGENT_PASSWORD:?Définir POSTGRES_AGENT_PASSWORD dans database/compose.yaml}"
# Les règles HBA sont écrites sans interpolation d'un nom de base arbitraire.
if [[ ! "$POSTGRES_DB" =~ ^[a-zA-Z0-9_][a-zA-Z0-9_.-]*$ ]]; then
    echo 'POSTGRES_DB : lettres ASCII, chiffres, _, . et - uniquement pour les règles agent.' >&2
    exit 1
fi
psql=(psql -X --set=ON_ERROR_STOP=1 --host /var/run/postgresql --username "$POSTGRES_USER" --dbname "$POSTGRES_DB")
"${psql[@]}" --file=/usr/local/share/rag-agent.sql

# L'agent ne doit pas profiter de PUBLIC CONNECT sur postgres/template1/autres DB.
# Ces règles précèdent aussi le trust local utilisé par l'administrateur.
hba=$("${psql[@]}" -At --command='SHOW hba_file;')
temporary=$(mktemp "$(dirname "$hba")/.agent-hba.XXXXXX")
trap 'rm -f "$temporary"' EXIT
{
    echo '# BEGIN RAG AGENT'
    printf 'local "%s" agent scram-sha-256\n' "$POSTGRES_DB"
    printf 'host "%s" agent 0.0.0.0/0 scram-sha-256\n' "$POSTGRES_DB"
    printf 'host "%s" agent ::/0 scram-sha-256\n' "$POSTGRES_DB"
    echo 'local all agent reject'
    echo 'host all agent 0.0.0.0/0 reject'
    echo 'host all agent ::/0 reject'
    echo '# END RAG AGENT'
    sed '/^# BEGIN RAG AGENT$/,/^# END RAG AGENT$/d' "$hba"
} > "$temporary"
chmod 600 "$temporary"
mv "$temporary" "$hba"
"${psql[@]}" <<'SQL'
DO $$
BEGIN
    IF EXISTS (SELECT FROM pg_catalog.pg_hba_file_rules WHERE error IS NOT NULL) THEN
        RAISE EXCEPTION 'Règles HBA invalides : agent reste NOLOGIN';
    END IF;
    IF NOT pg_catalog.pg_reload_conf() THEN
        RAISE EXCEPTION 'Rechargement HBA refusé : agent reste NOLOGIN';
    END IF;
END;
$$;
SQL

# \\getenv évite de mettre le secret dans les arguments de processus ou le SQL versionné.
"${psql[@]}" <<'SQL'
\getenv agent_password POSTGRES_AGENT_PASSWORD
SET log_statement = 'none';
SET log_min_error_statement = 'panic';
SET password_encryption = 'scram-sha-256';
ALTER ROLE agent PASSWORD :'agent_password' LOGIN;
SQL
