FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY pyproject.toml ./
COPY schemashift ./schemashift
RUN pip install --no-cache-dir .
RUN useradd -m app && chown -R app /app
USER app
EXPOSE 8000
CMD ["uvicorn", "schemashift.api.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2"]
