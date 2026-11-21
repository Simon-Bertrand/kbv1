# All FROM images are local and exported by make prepare-build.
FROM knowledgebase/build-frontend:local AS frontend
WORKDIR /front
COPY packages/pdfparse/src/pdfparse/front/package*.json ./
RUN sha256sum -c /opt/offline/frontend.sha256
COPY packages/pdfparse/src/pdfparse/front/ ./
RUN npm --offline run check && ./node_modules/.bin/vite build

FROM knowledgebase/build-pdfparse:local
ENV PYTHONDONTWRITEBYTECODE=1 \
    DATA_DIR=/data \
    MODELS_DIR=/models \
    PYTHONUNBUFFERED=1 \
    PATH=/opt/venv/bin:$PATH \
    HF_HOME=/home/pdfparse/.cache/huggingface \
    TORCH_HOME=/home/pdfparse/.cache/torch \
    XDG_CACHE_HOME=/home/pdfparse/.cache \
    DOCLING_CACHE_DIR=/home/pdfparse/.cache/docling \
    HF_HUB_OFFLINE=1 \
    TRANSFORMERS_OFFLINE=1 \
    TOKENIZERS_PARALLELISM=false
# Triton needs a C compiler for its launcher stubs; no nvcc is required.
ENV PIP_NO_INDEX=1 HF_HUB_DISABLE_TELEMETRY=1 DO_NOT_TRACK=1 \
    PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK=True
WORKDIR /build
COPY pyproject.toml uv.lock ./
COPY packages/pdfparse/pyproject.toml packages/pdfparse/README.md ./packages/pdfparse/
RUN sha256sum -c /opt/offline/pdfparse.sha256
COPY packages/pdfparse/src/ ./packages/pdfparse/src/
COPY --from=frontend /front/dist/ ./packages/pdfparse/src/pdfparse/front/dist/
RUN python -m pip install --no-index --no-deps --no-build-isolation ./packages/pdfparse && rm -rf /build
RUN groupadd --gid 1000 pdfparse \
    && useradd --uid 1000 --gid pdfparse --create-home pdfparse \
    && mkdir -p /data /app /assets /pdfparse-cache "$HF_HOME" "$TORCH_HOME" "$DOCLING_CACHE_DIR" \
    && chown pdfparse:pdfparse /data /app /assets /pdfparse-cache "$XDG_CACHE_HOME" \
        "$HF_HOME" "$TORCH_HOME" "$DOCLING_CACHE_DIR"
WORKDIR /app
USER pdfparse
# Mount the model tree provisioned by models/download.sh at MODELS_DIR.
VOLUME ["/data", "/assets"]
EXPOSE 8000
ENTRYPOINT ["python", "-m", "pdfparse.backend"]
CMD ["--host", "0.0.0.0", "--root", "/data", "--assets-dir", "/assets"]
