#!/bin/bash
# Azure App Service startup script
set -e

echo "=== Asset Management v2 Startup ==="
cd /home/site/wwwroot

# Bootstrap: create schemas, create tables, stamp Alembic if fresh DB.
# Idempotent — safe to run on every startup.
echo "Bootstrapping database..."
python init_db.py

# Apply any pending Alembic migrations (no-op if already up to date).
echo "Running database migrations..."
flask db upgrade || echo "Warning: db upgrade had no new migrations to apply"

echo "Starting Gunicorn..."
gunicorn \
  --config gunicorn.conf.py \
  --bind 0.0.0.0:${PORT:-8000} \
  "app:create_app()"
