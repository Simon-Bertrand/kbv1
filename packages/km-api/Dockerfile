ARG BUILD_IMAGE_PREFIX=knowledgebase
FROM ${BUILD_IMAGE_PREFIX}/base-python:latest
WORKDIR /build
ARG PIP_INDEX_URL
ARG PIP_TRUSTED_HOST
COPY --from=workspace docker/python/km-api.txt /tmp/requirements.txt
COPY --from=workspace packages/km-api/pyproject.toml packages/km-api/README.md ./packages/km-api/
COPY src/ ./packages/km-api/src/
RUN set -eu; \
    export PIP_CONFIG_FILE=/dev/null PIP_DISABLE_PIP_VERSION_CHECK=1 PYTHONDONTWRITEBYTECODE=1; \
    unset PIP_EXTRA_INDEX_URL PIP_FIND_LINKS PIP_NO_INDEX; \
    export PIP_INDEX_URL="${PIP_INDEX_URL:-https://pypi.org/simple}"; \
    python -m venv /opt/venv; \
    /opt/venv/bin/pip install --no-cache-dir --no-compile --only-binary=:all: --no-deps -r /tmp/requirements.txt; \
    /opt/venv/bin/pip install --no-cache-dir --no-compile --no-index --no-deps --no-build-isolation ./packages/km-api; \
    /opt/venv/bin/pip check; \
    rm -f /tmp/requirements.txt; \
    rm -rf /build

ENV PATH=/opt/venv/bin:$PATH PIP_NO_INDEX=1
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
RUN python -c "import km; print('km-api validated')"
RUN groupadd --gid 10001 km && useradd --uid 10001 --gid km --no-create-home km
USER 10001:10001
EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=5s CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3)"]
CMD ["python", "-m", "uvicorn", "km.api:app", "--host", "0.0.0.0", "--port", "8000", "--no-proxy-headers"]
