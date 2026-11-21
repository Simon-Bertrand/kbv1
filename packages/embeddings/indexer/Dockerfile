FROM knowledgebase/build-km-indexer:local
ENV PATH=/opt/venv/bin:$PATH PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_INDEX=1
WORKDIR /build
COPY pyproject.toml uv.lock ./
COPY packages/km-api/pyproject.toml packages/km-api/README.md ./packages/km-api/
COPY packages/embeddings/pyproject.toml packages/embeddings/README.md ./packages/embeddings/
RUN sha256sum -c /opt/offline/km-indexer.sha256
COPY packages/km-api/src/ ./packages/km-api/src/
COPY packages/embeddings/src/ ./packages/embeddings/src/
RUN mkdir /build/tmp \
    && TMPDIR=/build/tmp python -m pip install --no-cache-dir --no-index --no-deps --no-build-isolation ./packages/km-api ./packages/embeddings \
    && cd / && rm -rf /build
WORKDIR /app
RUN python -m pip check && python -c "import km; import km_embeddings; print('km-indexer validated')"
RUN groupadd --gid 10001 km && useradd --uid 10001 --gid km --no-create-home km \
    && mkdir -p /state /corpus && chown km:km /state /corpus
USER 10001:10001
EXPOSE 8011
HEALTHCHECK --interval=15s --timeout=5s CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8011/health', timeout=3)"]
CMD ["python", "-m", "uvicorn", "km_embeddings.index_service:app", "--host", "0.0.0.0", "--port", "8011", "--workers", "1", "--no-proxy-headers"]
