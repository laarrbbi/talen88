#!/bin/bash
set -e

echo "[startup] Generating demo database..."
python -m data.generate

echo "[startup] Starting all services..."
exec supervisord -c /app/deploy/supervisord.conf
