# syntax=docker/dockerfile:1
# Build context is the repository root: docker build -f backend/Dockerfile .
FROM python:3.12-slim AS build
ENV PIP_NO_CACHE_DIR=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /build
COPY backend/pyproject.toml ./
COPY backend/schemashift ./schemashift
RUN pip install --prefix=/install .

FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    SAMPLES_DIR=/app/samples METRICS_FILE=/app/docs/metrics.json \
    DB_AUTO_CREATE=false ENVIRONMENT=production
WORKDIR /app
COPY --from=build /install /usr/local
COPY backend/alembic.ini ./
COPY backend/alembic ./alembic
COPY samples ./samples
COPY docs/metrics.json ./docs/metrics.json
RUN useradd --create-home --uid 10001 app && chown -R app /app
USER app
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/v1/health').status==200 else 1)"
# Migrate, then serve. SIGTERM triggers uvicorn's graceful shutdown (the lifespan drains runs).
CMD ["sh", "-c", "alembic upgrade head && exec uvicorn schemashift.api.main:app --host 0.0.0.0 --port ${PORT:-8000} --workers 2"]
