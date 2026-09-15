"""FastAPI entry point. Routers (ingest, mappings, exceptions) are added as their weeks land."""

from __future__ import annotations

from fastapi import FastAPI

from app.routers import mappings, sources

app = FastAPI(title="m3-trusted-data-foundation", version="0.1.0")
app.include_router(sources.router)
app.include_router(mappings.router)


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}
