ARG BUILD_IMAGE_PREFIX=knowledgebase
FROM ${BUILD_IMAGE_PREFIX}/wheelhouse-preparer:local AS dependencies
ARG WHEEL_SOURCE=upstream
WORKDIR /workspace
COPY --from=workspace pyproject.toml uv.lock ./
COPY --from=workspace packages/km-api/pyproject.toml ./packages/km-api/
COPY --from=workspace packages/embeddings/pyproject.toml ./packages/embeddings/
COPY --from=workspace packages/km-frontend/pyproject.toml ./packages/km-frontend/
COPY --from=workspace packages/pdfparse/pyproject.toml ./packages/pdfparse/
COPY --from=workspace scripts/docker/wheelhouse.py /opt/resolve-wheels.py
RUN --mount=type=secret,id=pip_index_url \
    --mount=type=secret,id=pip_cert \
    python /opt/resolve-wheels.py resolve "km-api" /resolved

FROM ${BUILD_IMAGE_PREFIX}/base-python:local
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /build
COPY --from=workspace pyproject.toml uv.lock ./
COPY --from=workspace packages/km-api/pyproject.toml packages/km-api/README.md ./packages/km-api/
RUN --mount=type=bind,from=dependencies,source=/resolved,target=/wheelhouse \
    python /opt/wheelhouse.py install km-api /wheelhouse /opt/venv
ENV PATH=/opt/venv/bin:$PATH PIP_NO_INDEX=1
COPY src/ ./packages/km-api/src/
RUN python -m pip install --no-index --no-deps --no-build-isolation ./packages/km-api \
    && cd / && rm -rf /build
WORKDIR /app
RUN python -m pip check && python -c "import km; print('km-api validated')"
RUN groupadd --gid 10001 km && useradd --uid 10001 --gid km --no-create-home km
USER 10001:10001
EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=5s CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3)"]
CMD ["python", "-m", "uvicorn", "km.api:app", "--host", "0.0.0.0", "--port", "8000", "--no-proxy-headers"]
