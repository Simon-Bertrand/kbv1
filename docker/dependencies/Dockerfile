# Online preparation only. Service Dockerfiles consume the exported local images.
FROM knowledgebase/upstream-node:latest AS webui-runtime

FROM webui-runtime AS webui
WORKDIR /app
COPY packages/webui/package.json packages/webui/package-lock.json ./
RUN --mount=type=cache,target=/root/.npm npm ci --ignore-scripts \
    && mkdir -p /opt/offline \
    && sha256sum package.json package-lock.json > /opt/offline/webui.sha256

FROM webui-runtime AS km-frontend
WORKDIR /front
COPY packages/km-frontend/package.json packages/km-frontend/package-lock.json ./
RUN --mount=type=cache,target=/root/.npm npm ci --no-audit --no-fund \
    && mkdir -p /opt/offline \
    && sha256sum package.json package-lock.json > /opt/offline/km-frontend.sha256

FROM knowledgebase/upstream-node:latest AS frontend
WORKDIR /front
COPY packages/pdfparse/src/pdfparse/front/package*.json ./
RUN --mount=type=cache,target=/root/.npm npm ci \
    && mkdir -p /opt/offline \
    && sha256sum package.json package-lock.json > /opt/offline/frontend.sha256

FROM knowledgebase/upstream-python:latest AS python-system
ENV PATH=/opt/venv/bin:$PATH
RUN apt-get update && apt-get install -y --no-install-recommends libgomp1     && rm -rf /var/lib/apt/lists/*

FROM python-system AS python-runtime
COPY scripts/docker/wheelhouse.py /opt/wheelhouse.py

FROM python-system AS python-pdf-system
# Triton generates launcher stubs at runtime.
RUN apt-get update && apt-get install -y --no-install-recommends libgl1 libglib2.0-0 gcc libc6-dev     && rm -rf /var/lib/apt/lists/*

FROM python-pdf-system AS python-pdf-runtime
COPY scripts/docker/wheelhouse.py /opt/wheelhouse.py

# Used only on the connected machine to produce the wheelhouse.
FROM python-pdf-system AS wheelhouse-preparer
RUN apt-get update && apt-get install -y --no-install-recommends g++ \
    && rm -rf /var/lib/apt/lists/*
COPY --from=ghcr.io/astral-sh/uv:0.11.8 /uv /usr/local/bin/uv
ENV UV_CACHE_DIR=/tmp/uv-cache UV_PYTHON_DOWNLOADS=never
COPY scripts/docker/wheelhouse.py /opt/wheelhouse.py

FROM knowledgebase/upstream-alpine:latest AS fileserver-base
RUN apk add --no-cache nginx nginx-mod-http-js ca-certificates

FROM fileserver-base AS fileserver-build
RUN apk add --no-cache build-base linux-headers pcre2-dev zlib-dev curl \
    && mkdir -p /build \
    && version="$(nginx -v 2>&1 | sed 's|nginx version: nginx/||')" \
    && curl -fsSL "https://nginx.org/download/nginx-${version}.tar.gz" -o /build/nginx.tar.gz \
    && curl -fsSL https://codeload.github.com/evanmiller/mod_zip/tar.gz/refs/tags/1.3.0 -o /build/mod_zip.tar.gz \
    && echo '5e7f24b6b4efb8258d92ec921ade9df69e4bad36f833a39b47ee947b3d637f22  /build/mod_zip.tar.gz' | sha256sum -c - \
    && sha256sum /build/*.tar.gz > /build/SHA256SUMS
