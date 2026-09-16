"""FastAPI entry point: every router, `/healthz`, and `/` redirecting to the interactive API docs."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.responses import RedirectResponse

from app.routers import (
    agent,
    drift,
    exceptions,
    ingest,
    knowledge,
    lineage,
    mappings,
    ops,
    publish,
    reconcile,
    sources,
)

app = FastAPI(title="m3-trusted-data-foundation", version="0.1.0")
app.include_router(sources.router)
app.include_router(mappings.router)
app.include_router(ingest.router)
app.include_router(lineage.router)
app.include_router(exceptions.router)
app.include_router(drift.router)
app.include_router(publish.router)
app.include_router(reconcile.router)
app.include_router(agent.router)
app.include_router(ops.router)
app.include_router(knowledge.router)


@app.get("/", include_in_schema=False)
def root() -> RedirectResponse:
    """The bare URL is not an endpoint: send people to the interactive docs instead of a 404."""
    return RedirectResponse("/docs")


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}
