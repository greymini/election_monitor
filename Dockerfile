# API image built from the repository root, for hosts that build the root by
# default (Railway). Same image as backend/docker/Dockerfile.api, which builds
# from backend/; keep the two in step.
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
        libpq5 curl \
    && rm -rf /var/lib/apt/lists/*

COPY backend/requirements-api.txt .
RUN pip install --no-cache-dir -r requirements-api.txt

COPY backend/common ./common
COPY backend/api ./api
COPY backend/chatbot ./chatbot
COPY backend/analytics ./analytics
# api.main bootstraps the single login (APP_USERNAME / APP_PASSWORD) at startup.
COPY backend/scripts ./scripts
COPY backend/ingest ./ingest
# /boundaries serves source warnings and unseeded blocks from the built file.
COPY backend/db/seed/geo ./db/seed/geo

RUN useradd --create-home --uid 10001 appuser && chown -R appuser /app
USER appuser

EXPOSE 8000
# PORT is set by Railway; 8000 otherwise.
CMD ["sh", "-c", "uvicorn api.main:app --host 0.0.0.0 --port ${PORT:-8000} --workers 2"]
