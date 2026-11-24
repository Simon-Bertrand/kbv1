ARG BUILD_IMAGE_PREFIX=knowledgebase
FROM ${BUILD_IMAGE_PREFIX}/wheelhouse-preparer:local AS dependencies
ARG WHEEL_SOURCE=upstream
ARG PDFPARSE_WHEEL_PROFILE=pdfparse
WORKDIR /workspace
COPY --from=workspace pyproject.toml uv.lock ./
COPY --from=workspace packages/km-api/pyproject.toml ./packages/km-api/
COPY --from=workspace packages/embeddings/pyproject.toml ./packages/embeddings/
COPY --from=workspace packages/km-frontend/pyproject.toml ./packages/km-frontend/
COPY --from=workspace packages/pdfparse/pyproject.toml ./packages/pdfparse/
COPY --from=workspace scripts/docker/wheelhouse.py /opt/resolve-wheels.py
RUN --mount=type=secret,id=pip_index_url \
    --mount=type=secret,id=pip_cert \
    python /opt/resolve-wheels.py resolve "${PDFPARSE_WHEEL_PROFILE}" /resolved

# All FROM images are local and exported by make prepare-build.
FROM ${BUILD_IMAGE_PREFIX}/build-frontend:local AS frontend
WORKDIR /front
COPY src/pdfparse/front/package*.json ./
RUN sha256sum -c /opt/offline/frontend.sha256
COPY src/pdfparse/front/ ./
RUN npm --offline run check && ./node_modules/.bin/vite build

FROM ${BUILD_IMAGE_PREFIX}/base-python-pdf:local
ENV PYTHONDONTWRITEBYTECODE=1 \
    DATA_DIR=/data \
    MODELS_DIR=/models \
    PYTHONUNBUFFERED=1 \
    HF_HOME=/home/pdfparse/.cache/huggingface \
    TORCH_HOME=/home/pdfparse/.cache/torch \
    XDG_CACHE_HOME=/home/pdfparse/.cache \
    DOCLING_CACHE_DIR=/home/pdfparse/.cache/docling \
    HF_HUB_OFFLINE=1 \
    TRANSFORMERS_OFFLINE=1 \
    TOKENIZERS_PARALLELISM=false
# Triton needs a C compiler for its launcher stubs; no nvcc is required.
ENV HF_HUB_DISABLE_TELEMETRY=1 DO_NOT_TRACK=1 \
    PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK=True
WORKDIR /build
COPY --from=workspace pyproject.toml uv.lock ./
COPY --from=workspace packages/pdfparse/pyproject.toml packages/pdfparse/README.md ./packages/pdfparse/
RUN --mount=type=bind,from=dependencies,source=/resolved,target=/wheelhouse \
    python /opt/wheelhouse.py install pdfparse /wheelhouse /opt/venv
ENV PATH=/opt/venv/bin:$PATH PIP_NO_INDEX=1
COPY src/ ./packages/pdfparse/src/
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
