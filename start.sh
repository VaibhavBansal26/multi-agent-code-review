#!/usr/bin/env bash
# Render start script: run DB migrations, then start the Celery worker and API server together.
set -e

echo "==> Running Alembic migrations..."
alembic upgrade head

echo "==> Starting Celery worker..."
celery -A app.queue.celery_app worker --loglevel=info -Q reviews,security --concurrency=1 &
CELERY_PID=$!

echo "==> Starting MACRV API (PID of worker: $CELERY_PID)..."
exec uvicorn app.main:app \
  --host 0.0.0.0 \
  --port "${PORT:-8000}" \
  --workers 1 \
  --log-level info
