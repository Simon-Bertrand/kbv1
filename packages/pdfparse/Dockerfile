ARG BUILD_IMAGE_PREFIX=knowledgebase
# Compose overrides this context with the locally prepared CUDA wheel.
FROM ${BUILD_IMAGE_PREFIX}/base-python-pdf:latest AS pdfparse_native

# All FROM images are local and exported by make prepare-build.
FROM ${BUILD_IMAGE_PREFIX}/build-frontend:latest AS frontend
WORKDIR /front
COPY src/pdfparse/front/package*.json ./
RUN sha256sum -c /opt/offline/frontend.sha256
COPY src/pdfparse/front/ ./
RUN npm --offline run check && ./node_modules/.bin/vite build

FROM ${BUILD_IMAGE_PREFIX}/base-python-pdf:latest
WORKDIR /build
ARG PIP_INDEX_URL
ARG PIP_TRUSTED_HOST
ARG PDFPARSE_WHEEL_PROFILE=pdfparse
COPY --from=workspace docker/python/pdfparse*.txt /opt/pins/
COPY --from=workspace packages/pdfparse/pyproject.toml packages/pdfparse/README.md ./packages/pdfparse/
COPY src/ ./packages/pdfparse/src/
COPY --from=frontend /front/dist/ ./packages/pdfparse/src/pdfparse/front/dist/
RUN --mount=type=bind,from=pdfparse_native,source=/,target=/opt/native \
    set -eu; \
    export PIP_CONFIG_FILE=/dev/null PIP_DISABLE_PIP_VERSION_CHECK=1 PYTHONDONTWRITEBYTECODE=1; \
    unset PIP_EXTRA_INDEX_URL PIP_FIND_LINKS PIP_NO_INDEX; \
    requirements="/opt/pins/${PDFPARSE_WHEEL_PROFILE}.txt"; \
    if [ -z "${PIP_INDEX_URL:-}" ]; then \
        export PIP_EXTRA_INDEX_URL=https://download.pytorch.org/whl/cu130; \
        if [ "$PDFPARSE_WHEEL_PROFILE" = pdfparse-full ]; then \
            export PIP_EXTRA_INDEX_URL="$PIP_EXTRA_INDEX_URL https://www.paddlepaddle.org.cn/packages/stable/cu118/"; \
        fi; \
        test -s /opt/native/pdfparse-native.whl || { echo 'Wheel CUDA locale manquante. Lancer make prepare-bases.' >&2; exit 1; }; \
        version=$(sed -n 's/^causal-conv1d==//p' "$requirements"); \
        mkdir /build/wheels; \
        ln -s /opt/native/pdfparse-native.whl "/build/wheels/causal_conv1d-$version-cp312-cp312-linux_x86_64.whl"; \
        export PIP_FIND_LINKS=/build/wheels; \
    fi; \
    export PIP_INDEX_URL="${PIP_INDEX_URL:-https://pypi.org/simple}"; \
    python -m venv /opt/venv; \
    /opt/venv/bin/pip install --no-cache-dir --no-compile --only-binary=:all: --no-deps -r "$requirements"; \
    if [ "$PDFPARSE_WHEEL_PROFILE" = pdfparse-full ]; then \
        site=$(/opt/venv/bin/python -c 'import sysconfig; print(sysconfig.get_path("purelib"))'); \
        while IFS= read -r pin; do \
            case "$pin" in '#'*|'') continue ;; esac; \
            name=${pin%%==*}; \
            /opt/venv/bin/pip install --no-cache-dir --no-compile --only-binary=:all: --no-deps --target "$site/private-$name" "$pin"; \
            printf 'private-%s\n' "$name" > "$site/$name.pth"; \
        done < /opt/pins/pdfparse-full-private.txt; \
    fi; \
    /opt/venv/bin/pip install --no-cache-dir --no-compile --no-index --no-deps --no-build-isolation ./packages/pdfparse; \
    /opt/venv/bin/pip check; \
    rm -rf /build /opt/pins

ENV PATH=/opt/venv/bin:$PATH PIP_NO_INDEX=1
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
