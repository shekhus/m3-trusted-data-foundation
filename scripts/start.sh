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
# An empty host binds every interface, IPv4 and IPv6: docker's IPv4 port mapping, Railway's IPv4 public edge
# and its IPv6 private network all reach the API ("::" alone is IPv6-only, "0.0.0.0" IPv4-only). Set HOST to
# narrow it.
exec uvicorn app.main:app --host "${HOST:-}" --port "${PORT:-8000}"
