"""Publish silver to gold (owner only) and read the publish history."""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Body, Depends
from pydantic import BaseModel
from sqlalchemy import Engine, text

from app.auth import Principal, require
from app.config import Settings, get_settings
from app.db import get_engine
from pipeline.publish import publish_source

router = APIRouter(tags=["publish"])
AnyRole = Depends(require("viewer", "analyst", "owner"))
Owner = Depends(require("owner"))


class PublishBody(BaseModel):
    batch_ids: list[str] | None = None


class BatchPublish(BaseModel):
    batch_id: str
    source: str
    file_name: str | None
    status: str
    rows_published: int
    rows_held: dict[str, int]
    reasons: list[str]


class PublishSummary(BaseModel):
    source: str
    batches: list[BatchPublish]
    published: int
    refused: int
    rows_published: int
    rows_held: dict[str, int]
    gold_rows_for_source: int


class HistoryRow(BaseModel):
    publish_id: int
    batch_id: str
    file_name: str | None
    status: str
    rows_published: int
    rows_held: dict[str, Any]
    reasons: list[str]
    actor: str
    created_at: datetime


@router.post("/publish/{source}", response_model=PublishSummary)
def publish(source: str, principal: Annotated[Principal, Owner],
            settings: Annotated[Settings, Depends(get_settings)],
            engine: Annotated[Engine, Depends(get_engine)],
            body: Annotated[PublishBody | None, Body()] = None) -> PublishSummary:
    """Publish the source's batches (or the given ones) to gold.fact_delivery; refused batches say why."""
    results = publish_source(engine, source, principal.role, settings.data_dir / "master",
                             body.batch_ids if body else None)
    held: dict[str, int] = {}
    for r in results:
        for reason, n in r.rows_held.items():
            held[reason] = held.get(reason, 0) + n
    with engine.connect() as conn:
        gold_rows = conn.execute(text("SELECT count(*) FROM gold.fact_delivery WHERE source = :s"),
                                 {"s": source}).scalar_one()
    return PublishSummary(source=source, batches=[BatchPublish(**asdict(r)) for r in results],
                          published=sum(r.status == "published" for r in results),
                          refused=sum(r.status == "refused" for r in results),
                          rows_published=sum(r.rows_published for r in results), rows_held=held,
                          gold_rows_for_source=gold_rows)


@router.get("/publish/history", response_model=list[HistoryRow])
def history(_: Annotated[Principal, AnyRole], engine: Annotated[Engine, Depends(get_engine)],
            source: str, limit: int = 200) -> list[HistoryRow]:
    with engine.connect() as conn:
        rows = conn.execute(text(
            "SELECT p.publish_id, p.batch_id, b.file_name, p.status, p.rows_published, p.rows_held, "
            "p.reasons, p.actor, p.created_at FROM ops.publishes p JOIN ops.batches b USING (batch_id) "
            "WHERE p.source = :s ORDER BY p.publish_id DESC LIMIT :n"),
            {"s": source, "n": min(limit, 1000)}).all()
    return [HistoryRow(**{**dict(r._mapping), "batch_id": str(r.batch_id)}) for r in rows]
