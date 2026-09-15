"""Reconciliation: run the job (analyst/owner) and read the latest result (any role)."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import Engine, text

from app.auth import Principal, require
from app.config import Settings, get_settings
from app.db import get_engine
from pipeline import reconcile

router = APIRouter(tags=["reconciliation"])
AnyRole = Depends(require("viewer", "analyst", "owner"))
Worker = Depends(require("analyst", "owner"))


class RunOut(BaseModel):
    run_id: int
    status: str
    actor: str
    created_at: datetime
    summary: dict[str, Any]
    consumers: list[dict[str, Any]]
    tools: list[dict[str, Any]]


@router.post("/reconcile", response_model=RunOut)
def run(
    principal: Annotated[Principal, Worker],
    settings: Annotated[Settings, Depends(get_settings)],
    engine: Annotated[Engine, Depends(get_engine)],
) -> RunOut:
    result = reconcile.run(engine, settings.data_dir, principal.role)
    return latest(principal, engine, result.run_id)


@router.get("/reconcile/latest", response_model=RunOut)
def latest(
    _: Annotated[Principal, AnyRole],
    engine: Annotated[Engine, Depends(get_engine)],
    run_id: int | None = None,
) -> RunOut:
    with engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT run_id, status, actor, created_at, summary FROM ops.reconciliation_runs "
                + ("WHERE run_id = :r" if run_id else "ORDER BY run_id DESC LIMIT 1")
            ),
            {"r": run_id} if run_id else {},
        ).first()
        if row is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "no reconciliation run yet")
        consumers = (
            conn.execute(
                text(
                    "SELECT metric, period, grain_key, consumer_a, value_a, consumer_b, value_b, delta, "
                    "legacy, value_legacy, legacy_delta, status FROM ops.reconciliation WHERE run_id = :r "
                    "ORDER BY status DESC, metric, period, grain_key"
                ),
                {"r": row.run_id},
            )
            .mappings()
            .all()
        )
        tools = (
            conn.execute(
                text(
                    "SELECT tool, period, lines_governed, lines_tool, governed, measured, gap, gap_flagged, "
                    "contributions, dominant_cause FROM ops.reconciliation_tools "
                    "WHERE run_id = :r ORDER BY tool, period"
                ),
                {"r": row.run_id},
            )
            .mappings()
            .all()
        )
    return RunOut(
        run_id=row.run_id,
        status=row.status,
        actor=row.actor,
        created_at=row.created_at,
        summary=row.summary,
        consumers=[dict(c) for c in consumers],
        tools=[dict(t) for t in tools],
    )
