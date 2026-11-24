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
    python /opt/resolve-wheels.py resolve "km-frontend-runtime" /resolved

FROM ${BUILD_IMAGE_PREFIX}/build-km-frontend:local AS build
WORKDIR /front
COPY package.json package-lock.json ./
RUN sha256sum -c /opt/offline/km-frontend.sha256
COPY index.html vite.config.js ./
COPY src/ ./src/
COPY scripts/ ./scripts/
COPY tests/ ./tests/
ENV npm_config_offline=true npm_config_audit=false npm_config_fund=false
RUN npm run check && npm test && npm run build

FROM ${BUILD_IMAGE_PREFIX}/base-python:local
WORKDIR /build
COPY --from=workspace pyproject.toml uv.lock ./
COPY --from=workspace packages/km-frontend/pyproject.toml ./packages/km-frontend/
RUN --mount=type=bind,from=dependencies,source=/resolved,target=/wheelhouse \
    python /opt/wheelhouse.py install km-frontend-runtime /wheelhouse /opt/venv
ENV PATH=/opt/venv/bin:$PATH PIP_NO_INDEX=1
RUN rm -rf /build
WORKDIR /app
COPY server.py ./
COPY src/__init__.py src/logging.py ./src/
COPY --from=build /front/dist ./dist
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_INDEX=1
USER 10001:10001
EXPOSE 8088
HEALTHCHECK --interval=30s --timeout=5s CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8088/health',timeout=3)"]
CMD ["python", "-m", "uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8088", "--no-proxy-headers"]
