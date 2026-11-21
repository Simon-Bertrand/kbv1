FROM knowledgebase/base-postgres:local

# Extensions PostgreSQL
COPY --from=knowledgebase/base-pgvector:local / /
COPY --from=knowledgebase/base-pgvectorscale:local / /
COPY --from=knowledgebase/base-pg-textsearch:local / /
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
COPY --chmod=0755 init-km-reranker.sh /usr/local/bin/init-km-reranker
COPY --chmod=0755 init-km.sh /usr/local/bin/init-km
COPY --chmod=0755 init-agent.sh /usr/local/bin/init-rag-agent
COPY sql/webui/00_schema.sql /tmp/sql/00-webui-schema/00_schema.sql
COPY sql/webui/10_tables.sql /tmp/sql/10-webui-tables/10_tables.sql
COPY sql/webui/35_security.sql /tmp/sql/35-webui-security/35_security.sql
COPY sql/webui/36_isolation.sql /tmp/sql/36-webui-isolation/36_isolation.sql
COPY --chmod=0755 init-webui.sh /usr/local/bin/init-webui

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

RUN ln -s /usr/local/bin/init-km /docker-entrypoint-initdb.d/56-km.sh && \
    ln -s /usr/local/bin/init-rag-agent /docker-entrypoint-initdb.d/50-agent.sh && \
    ln -s /usr/local/bin/init-webui /docker-entrypoint-initdb.d/55-webui.sh

RUN printf '#!/bin/sh\nexec /usr/local/bin/init-km-reranker --if-configured\n' \
    > /docker-entrypoint-initdb.d/57-km-reranker.sh \
    && chmod 0755 /docker-entrypoint-initdb.d/57-km-reranker.sh

EXPOSE 5432

# Entrypoint PostgreSQL officiel
ENTRYPOINT ["docker-entrypoint.sh"]

# Démarrage avec preload BM25
CMD ["postgres", "-c", "shared_preload_libraries=pg_textsearch"]
