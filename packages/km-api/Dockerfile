FROM knowledgebase/build-km-api:local
ENV PATH=/opt/venv/bin:$PATH PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_INDEX=1
WORKDIR /build
COPY pyproject.toml uv.lock ./
COPY packages/km-api/pyproject.toml packages/km-api/README.md ./packages/km-api/
RUN sha256sum -c /opt/offline/km-api.sha256
COPY packages/km-api/src/ ./packages/km-api/src/
RUN mkdir /build/tmp \
    && TMPDIR=/build/tmp python -m pip install --no-cache-dir --no-index --no-deps --no-build-isolation ./packages/km-api \
    && cd / && rm -rf /build
WORKDIR /app
RUN python -m pip check && python -c "import km; print('km-api validated')"
RUN groupadd --gid 10001 km && useradd --uid 10001 --gid km --no-create-home km
USER 10001:10001
EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=5s CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3)"]
CMD ["python", "-m", "uvicorn", "km.api:app", "--host", "0.0.0.0", "--port", "8000", "--no-proxy-headers"]
