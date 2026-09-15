"""FastAPI entry point. Routers (ingest, mappings, exceptions) are added as their weeks land."""

from __future__ import annotations

from fastapi import FastAPI

from app.routers import agent, drift, exceptions, ingest, lineage, mappings, ops, publish, reconcile, sources

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


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}
