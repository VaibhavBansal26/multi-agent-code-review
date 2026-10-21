#!/usr/bin/env bash
# Render start script: run DB migrations then start the API server.
set -e

echo "==> Running Alembic migrations..."
alembic upgrade head

echo "==> Starting MACRV API..."
exec uvicorn app.main:app \
  --host 0.0.0.0 \
  --port "${PORT:-8000}" \
  --workers 2 \
  --log-level info
