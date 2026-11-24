#!/usr/bin/env bash
set -euo pipefail

if [ "$(id -u)" = 0 ]; then
    exec gosu postgres "$0" "$@"
fi

sql_dir=/usr/local/share/database-bootstrap

require_password() {
    local variable=$1 minimum=${2:-1} password=${!1:-}
    if [ "${#password}" -lt "$minimum" ]; then
        printf 'Configurer %s (au moins %s caractère(s)).\n' "$variable" "$minimum" >&2
        exit 1
    fi
}

require_hba_database() {
    : "${PGDATA:?PGDATA requis}"
    if [[ ! "$POSTGRES_DB" =~ ^[a-zA-Z0-9_][a-zA-Z0-9_.-]*$ ]]; then
        echo 'POSTGRES_DB : lettres ASCII, chiffres, _, . et - uniquement.' >&2
        exit 1
    fi
}

configure_hba() (
    local role=$1 marker=$2 hba temporary
    hba=$("${psql[@]}" -At --command='SHOW hba_file;')
    temporary=$(mktemp "$(dirname "$hba")/.service-hba.XXXXXX")
    trap 'rm -f "$temporary"' EXIT
    {
        printf '# BEGIN %s\n' "$marker"
        printf 'local "%s" "%s" scram-sha-256\n' "$POSTGRES_DB" "$role"
        printf 'host "%s" "%s" 0.0.0.0/0 scram-sha-256\n' "$POSTGRES_DB" "$role"
        printf 'host "%s" "%s" ::/0 scram-sha-256\n' "$POSTGRES_DB" "$role"
        printf 'local all "%s" reject\n' "$role"
        printf 'host all "%s" 0.0.0.0/0 reject\n' "$role"
        printf 'host all "%s" ::/0 reject\n' "$role"
        printf '# END %s\n' "$marker"
        sed "/^# BEGIN ${marker}\$/,/^# END ${marker}\$/d" "$hba"
    } > "$temporary"
    chmod 600 "$temporary"
    mv "$temporary" "$hba"
    "${psql[@]}" --file="$sql_dir/reload-hba.sql"
)

command_name=${0##*/}
if [[ "$command_name" == init-km-reranker && "${1:-}" == --if-configured && -z "${POSTGRES_KM_RERANKER_PASSWORD:-}" ]]; then
    exit 0
fi

psql=(psql -X --set=ON_ERROR_STOP=1 --host /var/run/postgresql
      --username "${POSTGRES_USER:?POSTGRES_USER requis}" --dbname "${POSTGRES_DB:?POSTGRES_DB requis}")

case "$command_name" in
    init-rag-agent)
        require_password POSTGRES_AGENT_PASSWORD
        require_hba_database
        "${psql[@]}" --file=/usr/local/share/rag-agent.sql
        configure_hba agent 'RAG AGENT'
        "${psql[@]}" --file="$sql_dir/agent.sql"
        ;;
    init-webui)
        require_password POSTGRES_WEBUI_PASSWORD
        require_hba_database
        configure_hba webui-user 'WEBUI USER'
        "${psql[@]}" --file="$sql_dir/webui.sql"
        ;;
    init-km)
        require_password POSTGRES_KM_READER_PASSWORD 16
        require_password POSTGRES_KM_WRITER_PASSWORD 16
        "${psql[@]}" --file="$sql_dir/km.sql"
        ;;
    init-km-reranker)
        require_password POSTGRES_KM_RERANKER_PASSWORD 16
        "${psql[@]}" --file=/usr/local/share/km-reranker-role.sql --file="$sql_dir/km-reranker.sql"
        ;;
    *)
        printf 'Commande d’initialisation inconnue : %s\n' "$command_name" >&2
        exit 1
        ;;
esac
