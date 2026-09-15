"""Sources: list, profile (findings), upload an extract, build bronze/silver, list batches.

Uploads go to data/uploads/<source>/, never into the generated data/sources/ tree (its files are locked by
tests/ground_truth.lock). The body is the raw CSV (`Content-Type: text/csv`), so no multipart dependency.
"""

from __future__ import annotations

import csv
import io
import re
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel
from sqlalchemy import Engine, text

from app.auth import Principal, require
from app.config import Settings, get_settings
from app.db import get_engine
from pipeline.ingest import BatchResult, ingest_file
from pipeline.profile import SourceProfile, discover_sources, profile_source_cached

router = APIRouter(tags=["sources"])

AnyRole = Depends(require("viewer", "analyst", "owner"))
Proposer = Depends(require("analyst", "owner"))
SOURCE_RE = re.compile(r"^[a-z][a-z0-9_]{1,31}$")
FILE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,120}\.csv$")
MAX_UPLOAD_BYTES = 20 * 1024 * 1024


class SourceSummary(BaseModel):
    source: str
    files: int
    uploaded_files: int
    batches: dict[str, int]


class Batch(BaseModel):
    batch_id: str
    file_name: str | None
    status: str
    rows: int | None
    header_hash: str | None
    error: str | None
    received_at: str


class Uploaded(BaseModel):
    source: str
    file_name: str
    bytes: int
    columns: list[str]
    already_present: bool


def all_sources(settings: Settings) -> dict[str, list[Path]]:
    """Generated sources plus uploaded ones; a source may have files in both places."""
    found: dict[str, list[Path]] = {}
    for root in (settings.sources_dir, settings.data_dir / "uploads"):
        if root.is_dir():
            for name, files in discover_sources(root).items():
                found.setdefault(name, []).extend(files)
    return {name: sorted(files, key=lambda f: f.name) for name, files in sorted(found.items()) if files}


def _files(settings: Settings, source: str) -> list[Path]:
    files = all_sources(settings).get(source)
    if not files:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"unknown source '{source}'")
    return files


@router.get("/sources", response_model=list[SourceSummary])
def list_sources(_: Annotated[Principal, AnyRole], settings: Annotated[Settings, Depends(get_settings)],
                 engine: Annotated[Engine, Depends(get_engine)]) -> list[SourceSummary]:
    with engine.connect() as conn:
        rows = conn.execute(text("SELECT source, status, count(*) AS n FROM ops.batches GROUP BY 1, 2")).all()
    batches: dict[str, dict[str, int]] = {}
    for r in rows:
        batches.setdefault(r.source, {})[r.status] = r.n
    uploads = settings.data_dir / "uploads"
    return [SourceSummary(source=name, files=len(files),
                          uploaded_files=sum(1 for f in files if uploads in f.parents),
                          batches=batches.get(name, {}))
            for name, files in all_sources(settings).items()]


@router.get("/sources/{source}/profile", response_model=SourceProfile)
def profile(source: str, _: Annotated[Principal, AnyRole],
            settings: Annotated[Settings, Depends(get_settings)]) -> SourceProfile:
    return profile_source_cached(source, _files(settings, source), settings.data_dir / "master")


@router.post("/sources/{source}/files", response_model=Uploaded, status_code=status.HTTP_201_CREATED)
async def upload(source: str, request: Request, _: Annotated[Principal, Proposer],
                 settings: Annotated[Settings, Depends(get_settings)],
                 name: Annotated[str, Query(description="file name, e.g. plt04_orderlines_2026-09.csv")],
                 ) -> Uploaded:
    unprocessable = status.HTTP_422_UNPROCESSABLE_CONTENT
    if not SOURCE_RE.match(source):
        raise HTTPException(unprocessable, "source must be lowercase letters, digits, _")
    if not FILE_RE.match(name):
        raise HTTPException(unprocessable, "file name must be a plain name ending .csv")
    body = await request.body()
    if not body or len(body) > MAX_UPLOAD_BYTES:
        raise HTTPException(unprocessable, f"body must be 1 byte to {MAX_UPLOAD_BYTES}")
    try:
        header = next(csv.reader(io.StringIO(body.decode("utf-8-sig"))))
    except (UnicodeDecodeError, StopIteration, csv.Error) as exc:
        raise HTTPException(unprocessable, f"not a UTF-8 CSV with a header: {exc}") from exc
    if len(header) < 2 or len(set(header)) != len(header) or any(not h.strip() for h in header):
        raise HTTPException(unprocessable, "header needs 2+ distinct, non-blank columns")

    target = settings.data_dir / "uploads" / source / name
    if target.exists():
        if target.read_bytes() != body:
            raise HTTPException(status.HTTP_409_CONFLICT,
                                f"{name} already exists with different content; upload under a new name")
        return Uploaded(source=source, file_name=name, bytes=len(body), columns=header, already_present=True)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(body)
    return Uploaded(source=source, file_name=name, bytes=len(body), columns=header, already_present=False)


@router.post("/sources/{source}/silver", response_model=list[BatchResult])
def build(source: str, _: Annotated[Principal, Proposer],
          settings: Annotated[Settings, Depends(get_settings)],
          engine: Annotated[Engine, Depends(get_engine)]) -> list[BatchResult]:
    """Ingest every file of the source: bronze always, silver where the header has a confirmed mapping."""
    return [ingest_file(engine, source, f) for f in _files(settings, source)]


@router.get("/sources/{source}/batches", response_model=list[Batch])
def batches(source: str, _: Annotated[Principal, AnyRole],
            engine: Annotated[Engine, Depends(get_engine)]) -> list[Batch]:
    with engine.connect() as conn:
        rows = conn.execute(text(
            "SELECT batch_id, file_name, status, row_count, header_hash, error, received_at FROM ops.batches "
            "WHERE source = :s ORDER BY file_name, received_at"), {"s": source}).all()
    return [Batch(batch_id=str(r.batch_id), file_name=r.file_name, status=r.status, rows=r.row_count,
                  header_hash=r.header_hash.strip() if r.header_hash else None, error=r.error,
                  received_at=r.received_at.isoformat()) for r in rows]
