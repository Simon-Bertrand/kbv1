ARG BUILD_IMAGE_PREFIX=knowledgebase
FROM ${BUILD_IMAGE_PREFIX}/base-pg-textsearch:latest AS base-pg-textsearch
FROM ${BUILD_IMAGE_PREFIX}/base-pgvectorscale:latest AS base-pgvectorscale
FROM ${BUILD_IMAGE_PREFIX}/base-pgvector:latest AS base-pgvector
FROM ${BUILD_IMAGE_PREFIX}/base-postgres:latest

# Extensions PostgreSQL
COPY --from=base-pgvector / /
COPY --from=base-pgvectorscale / /
COPY --from=base-pg-textsearch / /
# Construire avec database/ comme contexte : docker build -t knowledgebase/postgres:local database/
COPY sql/rag/00_schema.sql /tmp/sql/00-schema/00_schema.sql
COPY sql/rag/extensions/ /tmp/sql/01-extensions/
COPY sql/rag/tables/ /tmp/sql/10-tables/
COPY sql/rag/indexes/ /tmp/sql/20-indexes/
COPY sql/rag/procedures/ /tmp/sql/30-procedures/
COPY sql/rag/security/ /tmp/sql/35-security/
COPY sql/rag/tests/ /tmp/sql/40-tests/
COPY sql/rag/security/01_agent.sql /usr/local/share/rag-agent.sql
COPY sql/rag/security/03_reranker.sql /usr/local/share/km-reranker-role.sql
COPY sql/bootstrap/ /usr/local/share/database-bootstrap/
COPY --chmod=0755 init-roles.sh /usr/local/libexec/init-database-roles
COPY sql/webui/00_schema.sql /tmp/sql/00-webui-schema/00_schema.sql
COPY sql/webui/10_tables.sql /tmp/sql/10-webui-tables/10_tables.sql
COPY sql/webui/35_security.sql /tmp/sql/35-webui-security/35_security.sql
COPY sql/webui/36_isolation.sql /tmp/sql/36-webui-isolation/36_isolation.sql

# L'entrypoint officiel exécute les scripts à plat dans l'ordre alphabétique,
# uniquement à la première initialisation (répertoire de données vide).
# Le préfixe de groupe précède le numéro du fichier : extensions, tables, index,
# fonctions, tests.
RUN set -eu; \
    mkdir -p /docker-entrypoint-initdb.d; \
    for directory in /tmp/sql/*; do \
        for file in "$directory"/*.sql; do \
            cp "$file" "/docker-entrypoint-initdb.d/$(basename "$directory")-$(basename "$file")"; \
        done; \
    done; \
    rm -r /tmp/sql

RUN set -eu; \
    for command in init-rag-agent init-webui init-km init-km-reranker; do \
        ln -s /usr/local/libexec/init-database-roles "/usr/local/bin/$command"; \
    done; \
    for entry in '50-agent:init-rag-agent' '55-webui:init-webui' '56-km:init-km' '57-km-reranker:init-km-reranker --if-configured'; do \
        file="/docker-entrypoint-initdb.d/${entry%%:*}.sh"; \
        printf '#!/bin/sh\nexec /usr/local/bin/%s\n' "${entry#*:}" > "$file"; \
        chmod 0755 "$file"; \
    done

EXPOSE 5432

# Entrypoint PostgreSQL officiel
ENTRYPOINT ["docker-entrypoint.sh"]

# Démarrage avec preload BM25
CMD ["postgres", "-c", "shared_preload_libraries=pg_textsearch"]
