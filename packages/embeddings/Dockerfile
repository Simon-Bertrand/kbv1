FROM knowledgebase/build-km-encoder:local
ENV PATH=/opt/venv/bin:$PATH PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_INDEX=1 \
    MODELS_DIR=/models HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 HF_HUB_DISABLE_TELEMETRY=1 HF_HOME=/tmp/huggingface
WORKDIR /build
COPY pyproject.toml uv.lock ./
COPY packages/km-api/pyproject.toml packages/km-api/README.md ./packages/km-api/
COPY packages/embeddings/pyproject.toml packages/embeddings/README.md ./packages/embeddings/
RUN sha256sum -c /opt/offline/km-encoder.sha256
COPY packages/km-api/src/ ./packages/km-api/src/
COPY packages/embeddings/src/ ./packages/embeddings/src/
RUN mkdir /build/tmp \
    && TMPDIR=/build/tmp python -m pip install --no-cache-dir --no-index --no-deps --no-build-isolation ./packages/km-api ./packages/embeddings \
    && cd / && rm -rf /build
WORKDIR /app
RUN python -m pip check && python -c "import km; import km_embeddings; print('km-encoder validated')"
RUN groupadd --gid 10001 km && useradd --uid 10001 --gid km --no-create-home km
USER 10001:10001
EXPOSE 8001
HEALTHCHECK --interval=15s --timeout=5s --start-period=180s CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8001/health', timeout=3)"]
CMD ["python", "-m", "uvicorn", "km_embeddings.encoder:app", "--host", "0.0.0.0", "--port", "8001", "--workers", "1", "--no-proxy-headers"]
