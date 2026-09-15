"""Drift alerts (docs/plan.md A9): list; resolve (owner only, once the new shape has a confirmed mapping)."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import Engine, text

from app.auth import Principal, require
from app.db import get_engine

router = APIRouter(tags=["drift"])
AnyRole = Depends(require("viewer", "analyst", "owner"))
Owner = Depends(require("owner"))

SELECT = ("SELECT a.alert_id, a.source, a.batch_id, b.file_name, a.to_fingerprint, a.diff, a.message, "
          "a.status, a.resolution, a.resolved_by, a.resolved_at, a.created_at, b.header_hash "
          "FROM ops.drift_alerts a JOIN ops.batches b USING (batch_id)")


class DriftAlert(BaseModel):
    alert_id: int
    source: str
    batch_id: str
    file_name: str | None
    message: str
    diff: dict[str, Any]
    status: str
    resolution: str | None
    resolved_by: str | None
    resolved_at: datetime | None
    created_at: datetime
    header_has_confirmed_mapping: bool


class Resolve(BaseModel):
    resolution: str = Field(min_length=10, max_length=2000)


def _out(row: Any, confirmed: bool) -> DriftAlert:
    return DriftAlert(alert_id=row.alert_id, source=row.source, batch_id=str(row.batch_id),
                      file_name=row.file_name, message=row.message, diff=row.diff, status=row.status,
                      resolution=row.resolution, resolved_by=row.resolved_by, resolved_at=row.resolved_at,
                      created_at=row.created_at, header_has_confirmed_mapping=confirmed)


def _confirmed(conn: Any, source: str, header_hash: str | None) -> bool:
    return header_hash is not None and conn.execute(text(
        "SELECT EXISTS (SELECT 1 FROM ops.mapping_versions WHERE source = :s AND header_hash = :h "
        "AND status = 'confirmed')"), {"s": source, "h": header_hash}).scalar_one()


@router.get("/drift/alerts", response_model=list[DriftAlert])
def list_alerts(_: Annotated[Principal, AnyRole], engine: Annotated[Engine, Depends(get_engine)],
                source: str | None = None,
                status_: Annotated[Literal["open", "resolved"] | None, Query(alias="status")] = None,
                ) -> list[DriftAlert]:
    clauses, params = [], {}
    if source:
        clauses.append("a.source = :source")
        params["source"] = source
    if status_:
        clauses.append("a.status = :status")
        params["status"] = status_
    where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
    with engine.connect() as conn:
        rows = conn.execute(text(f"{SELECT}{where} ORDER BY a.alert_id"), params).all()
        return [_out(r, _confirmed(conn, r.source, r.header_hash)) for r in rows]


@router.post("/drift/alerts/{alert_id}/resolve", response_model=DriftAlert)
def resolve(alert_id: int, body: Resolve, principal: Annotated[Principal, Owner],
            engine: Annotated[Engine, Depends(get_engine)]) -> DriftAlert:
    with engine.begin() as conn:
        row = conn.execute(text(f"{SELECT} WHERE a.alert_id = :id FOR UPDATE OF a"), {"id": alert_id}).first()
        if row is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "drift alert not found")
        if row.status == "resolved":
            raise HTTPException(status.HTTP_409_CONFLICT, "drift alert is already resolved")
        if not _confirmed(conn, row.source, row.header_hash):
            raise HTTPException(status.HTTP_409_CONFLICT,
                                "confirm a mapping for the new header before resolving this alert")
        conn.execute(text("UPDATE ops.drift_alerts SET status = 'resolved', resolution = :r, "
                          "resolved_by = :who, resolved_at = now() WHERE alert_id = :id"),
                     {"r": body.resolution, "who": principal.role, "id": alert_id})
        return _out(conn.execute(text(f"{SELECT} WHERE a.alert_id = :id"), {"id": alert_id}).one(), True)
