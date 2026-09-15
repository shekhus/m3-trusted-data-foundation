"""Exception queue (docs/plan.md A6): list, inspect, assign (analyst/owner), resolve (owner only), re-run.

Resolving records a decision with a kind publish can act on; it never changes bronze or silver. Every assign
and resolve appends to ops.exception_events. Re-validation keeps a person's resolution and closes, as
`no_longer_violated`, anything whose rule now passes.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import Connection, Engine, text

from app.auth import Principal, require
from app.config import Settings, get_settings
from app.db import get_engine
from pipeline.validate import load_masters, validate_batch

router = APIRouter(tags=["exceptions"])
AnyRole = Depends(require("viewer", "analyst", "owner"))
Worker = Depends(require("analyst", "owner"))
Owner = Depends(require("owner"))

Status = Literal["open", "assigned", "resolved"]
ResolutionKind = Literal["accept", "exclude", "fixed_at_source"]

COLUMNS = ("e.exception_id, e.batch_id, b.file_name, e.source, e.source_row, e.rule_id, e.severity, "
           "e.row_key, e.reason, e.suggested_fix, e.owner, e.status, e.details, e.resolution_kind, "
           "e.resolution, e.resolved_by, e.resolved_at, e.assigned_by, e.assigned_at, e.created_at")


class ExceptionOut(BaseModel):
    exception_id: int
    batch_id: str
    file_name: str | None
    source: str
    source_row: int
    rule_id: str
    severity: str
    row_key: str
    reason: str
    suggested_fix: str | None
    owner: str | None
    status: str
    details: dict[str, Any]
    resolution_kind: str | None
    resolution: str | None
    resolved_by: str | None
    resolved_at: datetime | None
    assigned_by: str | None
    assigned_at: datetime | None
    created_at: datetime


class EventOut(BaseModel):
    action: str
    actor: str
    details: dict[str, Any]
    created_at: datetime


class ExceptionDetail(ExceptionOut):
    raw_record: dict[str, Any]
    silver_row: dict[str, Any] | None
    events: list[EventOut]


class ExceptionPage(BaseModel):
    total: int
    items: list[ExceptionOut]


class Summary(BaseModel):
    by_status: dict[str, int]
    open_by_rule: dict[str, int]
    open_by_owner: dict[str, int]
    open_blocking: int


class Assign(BaseModel):
    owner: str = Field(min_length=2, max_length=100)


class Resolve(BaseModel):
    kind: ResolutionKind
    resolution: str = Field(min_length=10, max_length=2000, description="what was decided and why")


class RevalidateResult(BaseModel):
    batch_id: str
    violations: dict[str, int]
    open_exceptions: int
    auto_resolved: int


def _out(row: Any) -> ExceptionOut:
    return ExceptionOut(**{**dict(row._mapping), "batch_id": str(row.batch_id)})


def _load(conn: Connection, exception_id: int, lock: bool = False) -> Any:
    row = conn.execute(text(f"SELECT {COLUMNS} FROM ops.exceptions e JOIN ops.batches b USING (batch_id) "
                            f"WHERE e.exception_id = :id{' FOR UPDATE OF e' if lock else ''}"),
                       {"id": exception_id}).first()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "exception not found")
    return row


def _event(conn: Connection, exception_id: int, action: str, actor: str, details: dict) -> None:
    conn.execute(text("INSERT INTO ops.exception_events (exception_id, action, actor, details) "
                      "VALUES (:id, :action, :actor, CAST(:details AS jsonb))"),
                 {"id": exception_id, "action": action, "actor": actor, "details": json.dumps(details)})


@router.get("/exceptions", response_model=ExceptionPage)
def list_exceptions(_: Annotated[Principal, AnyRole], engine: Annotated[Engine, Depends(get_engine)],
                    source: str | None = None,
                    status_: Annotated[Status | None, Query(alias="status")] = None,
                    severity: Literal["block", "warn"] | None = None, rule_id: str | None = None,
                    owner: str | None = None, batch_id: str | None = None,
                    limit: Annotated[int, Query(ge=1, le=500)] = 100,
                    offset: Annotated[int, Query(ge=0)] = 0) -> ExceptionPage:
    if batch_id is not None:
        try:
            batch_id = str(uuid.UUID(batch_id))
        except ValueError as exc:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "batch_id must be a UUID") from exc
    filters = {"source": source, "status": status_, "severity": severity, "rule_id": rule_id, "owner": owner,
               "batch_id": batch_id}
    where = " AND ".join(f"e.{k} = :{k}" for k, v in filters.items() if v is not None) or "TRUE"
    params = {k: v for k, v in filters.items() if v is not None}
    with engine.connect() as conn:
        total = conn.execute(text(f"SELECT count(*) FROM ops.exceptions e WHERE {where}"),
                             params).scalar_one()
        rows = conn.execute(text(
            f"SELECT {COLUMNS} FROM ops.exceptions e JOIN ops.batches b USING (batch_id) WHERE {where} "
            "ORDER BY e.severity, e.status, e.rule_id, e.exception_id LIMIT :limit OFFSET :offset"),
            {**params, "limit": limit, "offset": offset}).all()
    return ExceptionPage(total=total, items=[_out(r) for r in rows])


@router.get("/exceptions/summary", response_model=Summary)
def summary(_: Annotated[Principal, AnyRole], engine: Annotated[Engine, Depends(get_engine)],
            source: str | None = None) -> Summary:
    where, params = ("source = :source", {"source": source}) if source else ("TRUE", {})
    with engine.connect() as conn:
        def counts(sql: str) -> dict[str, int]:
            return {str(k): int(v) for k, v in conn.execute(text(sql), params).tuples()}

        return Summary(
            by_status=counts(f"SELECT status, count(*) FROM ops.exceptions WHERE {where} GROUP BY 1"),
            open_by_rule=counts(f"SELECT rule_id, count(*) FROM ops.exceptions WHERE {where} "
                                "AND status <> 'resolved' GROUP BY 1 ORDER BY 1"),
            open_by_owner=counts(f"SELECT coalesce(owner, '-'), count(*) FROM ops.exceptions WHERE {where} "
                                 "AND status <> 'resolved' GROUP BY 1 ORDER BY 1"),
            open_blocking=conn.execute(text(f"SELECT count(*) FROM ops.exceptions WHERE {where} "
                                            "AND status <> 'resolved' AND severity = 'block'"),
                                       params).scalar_one())


@router.get("/exceptions/{exception_id}", response_model=ExceptionDetail)
def detail(exception_id: int, _: Annotated[Principal, AnyRole],
           engine: Annotated[Engine, Depends(get_engine)]) -> ExceptionDetail:
    with engine.connect() as conn:
        row = _load(conn, exception_id)
        where_row = "WHERE batch_id = :b AND source_row = :r"
        key = {"b": row.batch_id, "r": row.source_row}
        raw = conn.execute(text(f"SELECT record FROM bronze.raw_order_lines {where_row}"), key).scalar_one()
        silver = conn.execute(text(f"SELECT * FROM silver.order_lines {where_row}"), key).mappings().first()
        events = conn.execute(text("SELECT action, actor, details, created_at FROM ops.exception_events "
                                   "WHERE exception_id = :id ORDER BY event_id"), {"id": exception_id}).all()
    silver_row = None if silver is None else {
        k: str(v) if v is not None and not isinstance(v, (int, float, dict)) else v
        for k, v in silver.items()}
    return ExceptionDetail(**_out(row).model_dump(), raw_record=raw, silver_row=silver_row,
                           events=[EventOut(**dict(e._mapping)) for e in events])


@router.post("/exceptions/{exception_id}/assign", response_model=ExceptionOut)
def assign(exception_id: int, body: Assign, principal: Annotated[Principal, Worker],
           engine: Annotated[Engine, Depends(get_engine)]) -> ExceptionOut:
    with engine.begin() as conn:
        row = _load(conn, exception_id, lock=True)
        if row.status == "resolved":
            raise HTTPException(status.HTTP_409_CONFLICT, "exception is already resolved")
        conn.execute(text("UPDATE ops.exceptions SET status = 'assigned', owner = :owner, "
                          "assigned_by = :who, assigned_at = now() WHERE exception_id = :id"),
                     {"owner": body.owner, "who": principal.role, "id": exception_id})
        _event(conn, exception_id, "assigned", principal.role, {"from": row.owner, "to": body.owner})
        return _out(_load(conn, exception_id))


@router.post("/exceptions/{exception_id}/resolve", response_model=ExceptionOut)
def resolve(exception_id: int, body: Resolve, principal: Annotated[Principal, Owner],
            engine: Annotated[Engine, Depends(get_engine)]) -> ExceptionOut:
    with engine.begin() as conn:
        row = _load(conn, exception_id, lock=True)
        if row.status == "resolved":
            raise HTTPException(status.HTTP_409_CONFLICT,
                                f"exception was already resolved by {row.resolved_by}")
        conn.execute(text("UPDATE ops.exceptions SET status = 'resolved', resolution_kind = :kind, "
                          "resolution = :resolution, resolved_by = :who, resolved_at = now() "
                          "WHERE exception_id = :id"),
                     {"kind": body.kind, "resolution": body.resolution, "who": principal.role,
                      "id": exception_id})
        _event(conn, exception_id, "resolved", principal.role,
               {"kind": body.kind, "resolution": body.resolution})
        return _out(_load(conn, exception_id))


@router.post("/batches/{batch_id}/revalidate", response_model=RevalidateResult)
def revalidate(batch_id: str, _: Annotated[Principal, Worker],
               settings: Annotated[Settings, Depends(get_settings)],
               engine: Annotated[Engine, Depends(get_engine)]) -> RevalidateResult:
    """Re-run every rule on the batch's silver rows (e.g. after master data was corrected)."""
    try:
        batch_id = str(uuid.UUID(batch_id))
    except ValueError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "batch not found") from exc
    with engine.begin() as conn:
        found = conn.execute(text("SELECT status FROM ops.batches WHERE batch_id = CAST(:b AS uuid)"),
                             {"b": batch_id}).first()
        if found is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "batch not found")
        if found.status not in ("mapped", "validated", "published"):
            raise HTTPException(status.HTTP_409_CONFLICT,
                                f"batch is {found.status}; only mapped batches validate")
        before = conn.execute(text("SELECT count(*) FROM ops.exception_events v JOIN ops.exceptions e "
                                   "USING (exception_id) WHERE e.batch_id = :b "
                                   "AND v.action = 'auto_resolved'"),
                              {"b": batch_id}).scalar_one()
        violations = validate_batch(conn, batch_id, load_masters(settings.data_dir / "master"))
        open_count, after = conn.execute(text(
            "SELECT count(*) FILTER (WHERE e.status <> 'resolved'), "
            "(SELECT count(*) FROM ops.exception_events v JOIN ops.exceptions x USING (exception_id) "
            " WHERE x.batch_id = :b AND v.action = 'auto_resolved') "
            "FROM ops.exceptions e WHERE e.batch_id = :b"), {"b": batch_id}).one()
    return RevalidateResult(batch_id=batch_id, violations=violations, open_exceptions=open_count,
                            auto_resolved=after - before)
