#!/bin/bash
# Container entrypoint: prepare the database, then hand over to supervisord, which runs
# the scoring service, the agents service, the data API, and a one-shot score refresh.
set -euo pipefail

# Keep the database (and, when no key secret is set, its key file) on the persistent
# volume so data survives restarts and redeploys.
export PULSESCORE_DB="${PULSESCORE_DB:-/data/pulsescore.db}"
DATA_DIR="$(dirname "$PULSESCORE_DB")"
mkdir -p "$DATA_DIR"

if [ -z "${PULSESCORE_DATA_KEY:-}" ]; then
  if [ "${PULSESCORE_ENV:-}" = "production" ]; then
    echo "[startup] PULSESCORE_DATA_KEY is not set. Set it as a secret (see README: Deploy)." >&2
    exit 1
  fi
  export PULSESCORE_KEYFILE="${PULSESCORE_KEYFILE:-$DATA_DIR/.localkey}"
  echo "[startup] no PULSESCORE_DATA_KEY; using key file $PULSESCORE_KEYFILE (dev only)"
fi

if [ ! -f "$PULSESCORE_DB" ]; then
  echo "[startup] no database at $PULSESCORE_DB; generating the demo dataset..."
  python -m data.generate
else
  echo "[startup] using existing database at $PULSESCORE_DB"
  python -c "from data.db import get_connection, migrate; c = get_connection(); migrate(c); c.close()"
fi

echo "[startup] starting services (scores refresh once the scoring service is up)..."
exec supervisord -c /app/deploy/supervisord.conf
