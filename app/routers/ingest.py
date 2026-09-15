"""POST /ingest/{source} — idempotent ingest (docs/plan.md A7, failure cases §2.6).

Same Idempotency-Key + same request → the original response, header `Idempotent-Replayed: true`, nothing runs.
Same key + different request → 422. Same key while the first call runs → 409 with Retry-After. A source that
cannot be reached → 503 with Retry-After, a `failed` batch naming the reason, and the request marked failed so
the same key may retry. Each file's batch is keyed by its content hash, so no retry or new key can duplicate
rows. Stale extracts are accepted and flagged.
"""

from __future__ import annotations

import re
from typing import Annotated

from fastapi import APIRouter, Body, Depends, Header, HTTPException, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import Engine

from app.auth import Principal, require
from app.config import Settings, get_settings
from app.db import get_engine
from pipeline import idempotency
from pipeline.ingest import BatchResult, ingest_extract, record_failed_batch
from sources import FileSystemAdapter, SourceAdapter, SourceUnavailable

router = APIRouter(tags=["ingest"])
Proposer = Depends(require("analyst", "owner"))
KEY_RE = re.compile(r"^[A-Za-z0-9_.:-]{8,200}$")


class IngestBody(BaseModel):
    files: list[str] | None = Field(None, description="file names to ingest; omit for all the source offers")


class IngestResult(BaseModel):
    idempotency_key: str
    source: str
    attempts: int
    files: list[BatchResult]
    validated: int
    blocked: int
    stale: list[str]
    drift_alerts: list[int]
    blocking_exceptions: int
    warning_exceptions: int


def get_adapter(settings: Annotated[Settings, Depends(get_settings)]) -> SourceAdapter:
    return FileSystemAdapter([settings.sources_dir, settings.data_dir / "uploads"])


@router.post("/ingest/{source}", response_model=IngestResult)
def ingest(source: str, response: Response, principal: Annotated[Principal, Proposer],
           settings: Annotated[Settings, Depends(get_settings)],
           engine: Annotated[Engine, Depends(get_engine)],
           adapter: Annotated[SourceAdapter, Depends(get_adapter)],
           idempotency_key: Annotated[str, Header(alias="Idempotency-Key")],
           body: Annotated[IngestBody | None, Body()] = None) -> IngestResult | Response:
    if not KEY_RE.match(idempotency_key):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT,
                            "Idempotency-Key must be 8-200 characters of letters, digits, _ . : -")
    if source not in adapter.sources():
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"unknown source '{source}'")
    requested = sorted(set(body.files)) if body and body.files else None
    hash_ = idempotency.request_hash({"source": source, "files": requested})

    decision = idempotency.claim(engine, idempotency_key, source, hash_, principal.role)
    if decision.decision == "mismatch":
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT,
                            "Idempotency-Key was already used for a different request")
    if decision.decision == "in_progress":
        raise HTTPException(status.HTTP_409_CONFLICT, "a request with this Idempotency-Key is still running",
                            headers={"Retry-After": "5"})
    if decision.decision == "replay":
        response.headers["Idempotent-Replayed"] = "true"
        return IngestResult.model_validate(decision.response)

    try:
        try:
            refs = adapter.discover(source)
        except SourceUnavailable as exc:
            record_failed_batch(engine, source, None, str(exc))
            raise
        if requested is not None:
            unknown = sorted(set(requested) - {r.name for r in refs})
            if unknown:
                idempotency.fail(engine, idempotency_key, f"unknown files {unknown}")
                raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"{source} has no files {unknown}")
            refs = [r for r in refs if r.name in requested]
        results: list[BatchResult] = []
        for ref in refs:
            try:
                extract = adapter.fetch(ref)
            except SourceUnavailable as exc:
                record_failed_batch(engine, source, ref.name, str(exc))
                raise
            master_dir = settings.data_dir / "master"
            results.append(ingest_extract(engine, extract, master_dir, settings.source_sla_hours))
    except SourceUnavailable as exc:
        idempotency.fail(engine, idempotency_key, f"source unavailable: {exc}")
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, f"source '{source}' is unavailable: {exc}",
                            headers={"Retry-After": str(exc.retry_after_seconds)}) from exc
    except HTTPException:
        raise
    except Exception as exc:
        idempotency.fail(engine, idempotency_key, f"{exc.__class__.__name__}: {exc}")
        raise

    result = IngestResult(
        idempotency_key=idempotency_key, source=source, attempts=decision.attempts, files=results,
        validated=sum(r.status == "validated" for r in results),
        blocked=sum(r.status == "blocked" for r in results),
        stale=[r.file_name for r in results if r.stale],
        drift_alerts=sorted({r.drift_alert_id for r in results if r.drift_alert_id is not None}),
        blocking_exceptions=sum(r.blocking_exceptions for r in results),
        warning_exceptions=sum(r.warning_exceptions for r in results))
    idempotency.complete(engine, idempotency_key, result.model_dump(mode="json"))
    return result
