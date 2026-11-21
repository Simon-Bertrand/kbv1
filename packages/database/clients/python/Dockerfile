FROM knowledgebase/build-dbclient:local
ENV PATH=/opt/venv/bin:$PATH PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_INDEX=1
WORKDIR /build
COPY pyproject.toml uv.lock ./
COPY packages/database/clients/python/pyproject.toml packages/database/clients/python/README.md ./packages/database/clients/python/
RUN sha256sum -c /opt/offline/dbclient.sha256
COPY packages/database/clients/python/src/ ./packages/database/clients/python/src/
RUN python -m pip install --no-index --no-deps --no-build-isolation ./packages/database/clients/python && rm -rf /build
RUN python -m pip check && python -c "import dbclient; print('dbclient validated')"
WORKDIR /app
CMD ["python", "-c", "import dbclient; print('dbclient library ready')"]
