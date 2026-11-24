ARG BUILD_IMAGE_PREFIX=knowledgebase
FROM ${BUILD_IMAGE_PREFIX}/base-python:local
ENV PATH=/opt/venv/bin:$PATH PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_INDEX=1
WORKDIR /build
COPY --from=workspace pyproject.toml uv.lock ./
COPY pyproject.toml README.md ./packages/database/clients/python/
RUN --mount=type=bind,from=wheelhouse,source=/,target=/wheelhouse \
    python /opt/wheelhouse.py install dbclient /wheelhouse /opt/venv
COPY src/ ./packages/database/clients/python/src/
RUN python -m pip install --no-index --no-deps --no-build-isolation ./packages/database/clients/python && rm -rf /build
RUN python -m pip check && python -c "import dbclient; print('dbclient validated')"
WORKDIR /app
CMD ["python", "-c", "import dbclient; print('dbclient library ready')"]
