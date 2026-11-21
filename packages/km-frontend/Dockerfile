FROM knowledgebase/build-km-frontend:local AS build
WORKDIR /front
COPY packages/km-frontend/package.json packages/km-frontend/package-lock.json ./
RUN sha256sum -c /opt/offline/km-frontend.sha256
COPY packages/km-frontend/index.html packages/km-frontend/vite.config.js ./
COPY packages/km-frontend/src/ ./src/
COPY packages/km-frontend/scripts/ ./scripts/
COPY packages/km-frontend/tests/ ./tests/
ENV npm_config_offline=true npm_config_audit=false npm_config_fund=false
RUN npm run check && npm test && npm run build

FROM knowledgebase/base-km-frontend:local
WORKDIR /build
COPY packages/km-frontend/requirements.lock ./requirements.lock
RUN sha256sum -c /opt/offline/km-frontend-runtime.sha256 && rm -rf /build
WORKDIR /app
COPY packages/km-frontend/server.py packages/km-frontend/km_frontend_logging.py ./
COPY --from=build /front/dist ./dist
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_INDEX=1
USER 10001:10001
EXPOSE 8088
HEALTHCHECK --interval=30s --timeout=5s CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8088/health',timeout=3)"]
CMD ["python", "-m", "uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8088", "--no-proxy-headers"]
