ARG BUILD_IMAGE_PREFIX=knowledgebase
FROM ${BUILD_IMAGE_PREFIX}/base-python:local
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    MODELS_DIR=/models HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 HF_HUB_DISABLE_TELEMETRY=1 HF_HOME=/tmp/huggingface
WORKDIR /build
ARG PYTHON_INDEX_SOURCE=public
ARG PIP_INDEX_URL
COPY --from=workspace docker/python/install.sh /opt/install-python.sh
COPY --from=workspace docker/python/km-encoder*.txt /opt/pins/
ENV PATH=/opt/venv/bin:$PATH PIP_NO_INDEX=1
COPY --from=workspace packages/km-api/pyproject.toml packages/km-api/README.md ./packages/km-api/
COPY --from=workspace packages/embeddings/pyproject.toml packages/embeddings/README.md ./packages/embeddings/
COPY --from=workspace packages/km-api/src/ ./packages/km-api/src/
COPY --from=workspace packages/embeddings/src/ ./packages/embeddings/src/
RUN --mount=type=secret,id=gitea_user,env=GITEA_USER \
    --mount=type=secret,id=gitea_pat,env=GITEA_PAT \
    --mount=type=secret,id=pip_cert \
    sh /opt/install-python.sh km-encoder ./packages/km-api ./packages/embeddings && rm -rf /build
WORKDIR /app
RUN python -m pip check && python -c "import km; import km_embeddings; print('km-encoder validated')"
RUN groupadd --gid 10001 km && useradd --uid 10001 --gid km --no-create-home km
USER 10001:10001
EXPOSE 8001
HEALTHCHECK --interval=15s --timeout=5s --start-period=180s CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8001/health', timeout=3)"]
CMD ["python", "-m", "uvicorn", "km_embeddings.encoder:app", "--host", "0.0.0.0", "--port", "8001", "--workers", "1", "--no-proxy-headers"]
