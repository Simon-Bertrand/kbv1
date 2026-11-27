ARG BUILD_IMAGE_PREFIX=knowledgebase
FROM ${BUILD_IMAGE_PREFIX}/build-km-frontend:latest AS build
WORKDIR /front
COPY package.json package-lock.json ./
RUN sha256sum -c /opt/offline/km-frontend.sha256
COPY index.html vite.config.js ./
COPY src/ ./src/
COPY scripts/ ./scripts/
COPY tests/ ./tests/
ENV npm_config_offline=true npm_config_audit=false npm_config_fund=false
RUN npm run check && npm test && npm run build

FROM ${BUILD_IMAGE_PREFIX}/base-python:latest
ARG PIP_INDEX_URL
ARG PIP_TRUSTED_HOST
COPY --from=workspace docker/python/km-frontend-runtime.txt /tmp/requirements.txt
RUN set -eu; \
    export PIP_CONFIG_FILE=/dev/null PIP_DISABLE_PIP_VERSION_CHECK=1 PYTHONDONTWRITEBYTECODE=1; \
    unset PIP_EXTRA_INDEX_URL PIP_FIND_LINKS PIP_NO_INDEX; \
    export PIP_INDEX_URL="${PIP_INDEX_URL:-https://pypi.org/simple}"; \
    python -m venv /opt/venv; \
    /opt/venv/bin/pip install --no-cache-dir --no-compile --only-binary=:all: --no-deps -r /tmp/requirements.txt; \
    /opt/venv/bin/pip check; \
    rm -f /tmp/requirements.txt

ENV PATH=/opt/venv/bin:$PATH PIP_NO_INDEX=1
WORKDIR /app
COPY server.py ./
COPY src/__init__.py src/logging.py ./src/
COPY --from=build /front/dist ./dist
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
USER 10001:10001
EXPOSE 8088
HEALTHCHECK --interval=30s --timeout=5s CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8088/health',timeout=3)"]
CMD ["python", "-m", "uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8088", "--no-proxy-headers"]
