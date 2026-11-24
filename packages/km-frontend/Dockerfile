ARG BUILD_IMAGE_PREFIX=knowledgebase
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
ARG PYTHON_INDEX_SOURCE=public
ARG PIP_INDEX_URL
COPY --from=workspace docker/python/install.sh /opt/install-python.sh
COPY --from=workspace docker/python/km-frontend-runtime*.txt /opt/pins/
ENV PATH=/opt/venv/bin:$PATH PIP_NO_INDEX=1
RUN --mount=type=secret,id=gitea_user,env=GITEA_USER \
    --mount=type=secret,id=gitea_pat,env=GITEA_PAT \
    --mount=type=secret,id=pip_cert \
    sh /opt/install-python.sh km-frontend-runtime && rm -rf /build
WORKDIR /app
COPY server.py ./
COPY src/__init__.py src/logging.py ./src/
COPY --from=build /front/dist ./dist
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_INDEX=1
USER 10001:10001
EXPOSE 8088
HEALTHCHECK --interval=30s --timeout=5s CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8088/health',timeout=3)"]
CMD ["python", "-m", "uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8088", "--no-proxy-headers"]
