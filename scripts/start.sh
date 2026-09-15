#!/bin/sh
# Container entrypoint (docs/RUNBOOK.md, D-032). Migrations run first and must succeed; the knowledge-base index
# sync is best effort (an embedding outage must not keep the API down; retrieval reports a stale index instead).
set -e
# One image, two services: APP_ROLE=portal runs the Streamlit portal instead of the API (Railway, D-032).
if [ "${APP_ROLE:-api}" = "portal" ]; then
  exec sh scripts/start_portal.sh
fi
python scripts/migrate.py
python scripts/build_index.py || echo "start: knowledge-base index sync failed; the API starts without it"
# HOST defaults to IPv4 (docker port mapping); set HOST=:: where the private network is IPv6 (Railway).
exec uvicorn app.main:app --host "${HOST:-0.0.0.0}" --port "${PORT:-8000}"
