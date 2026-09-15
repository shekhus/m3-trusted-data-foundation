"""Operations summary (D-031): one read-only call over the observability views, for the portal's Ops tab and
for whoever is on call (docs/RUNBOOK.md). Any role may read it; nothing here writes.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import Engine, text

from app.auth import Principal, require
from app.db import get_engine

router = APIRouter(tags=["ops"])
AnyRole = Depends(require("viewer", "analyst", "owner"))


class OpsSummary(BaseModel):
    since: date
    pipeline: list[dict[str, Any]]
    exception_backlog: list[dict[str, Any]]
    model_usage: list[dict[str, Any]]
    agent_runs: list[dict[str, Any]]
    tool_usage: list[dict[str, Any]]
    latest_reconciliation: dict[str, Any] | None
    alerts: list[str]


def _rows(engine: Engine, sql: str, params: dict | None = None) -> list[dict[str, Any]]:
    with engine.connect() as conn:
        return [dict(r) for r in conn.execute(text(sql), params or {}).mappings().all()]


def alerts_for(
    pipeline: list[dict],
    backlog: list[dict],
    usage: list[dict],
    agent: list[dict],
    reconciliation: dict | None,
) -> list[str]:
    """Plain-language conditions an operator should act on; each maps to a RUNBOOK section."""
    out = []
    for p in pipeline:
        if p["failed"]:
            out.append(
                f"{p['source']}: {p['failed']} failed batch(es) — re-run ingest (RUNBOOK: failed ingest)"
            )
        if p["blocked"]:
            out.append(
                f"{p['source']}: {p['blocked']} batch(es) blocked on an unconfirmed mapping "
                "(RUNBOOK: mappings)"
            )
        if p["stale"]:
            out.append(
                f"{p['source']}: {p['stale']} stale extract(s); publish refuses them (RUNBOOK: stale source)"
            )
    old = [b for b in backlog if b["severity"] == "block" and (b["oldest_age_days"] or 0) > 3]
    if old:
        total = sum(b["exceptions"] for b in old)
        out.append(
            f"{total} blocking exception(s) older than 3 days, past SOP-DQ-001-v2's window (RUNBOOK: backlog)"
        )
    rate_limited = sum(u["rate_limited"] for u in usage)
    if rate_limited:
        out.append(f"{rate_limited} model call(s) rate limited in the window (RUNBOOK: provider limits)")
    waiting = [a for a in agent if a["status"] == "awaiting_approval"]
    if waiting:
        out.append(
            f"{sum(a['runs'] for a in waiting)} agent run(s) awaiting an owner's decision (RUNBOOK: agent)"
        )
    if reconciliation and reconciliation["status"] == "red":
        out.append("latest reconciliation is red: consumer views disagree (RUNBOOK: reconciliation)")
    return out


@router.get("/ops/summary", response_model=OpsSummary)
def summary(
    _: Annotated[Principal, AnyRole],
    engine: Annotated[Engine, Depends(get_engine)],
    days: Annotated[int, Query(ge=1, le=90)] = 7,
) -> OpsSummary:
    since = date.today() - timedelta(days=days - 1)
    pipeline = _rows(engine, "SELECT * FROM ops.v_pipeline_health ORDER BY source")
    backlog = _rows(engine, "SELECT * FROM ops.v_exception_backlog ORDER BY exceptions DESC LIMIT 50")
    usage = _rows(
        engine,
        "SELECT * FROM ops.v_model_usage_daily WHERE day >= :since ORDER BY day DESC, calls DESC",
        {"since": since},
    )
    agent = _rows(engine, "SELECT * FROM ops.v_agent_runs ORDER BY runs DESC")
    tools = _rows(engine, "SELECT * FROM ops.v_tool_usage ORDER BY calls DESC")
    latest = _rows(
        engine,
        "SELECT run_id, status, created_at, summary FROM ops.reconciliation_runs "
        "ORDER BY run_id DESC LIMIT 1",
    )
    reconciliation = latest[0] if latest else None
    return OpsSummary(
        since=since,
        pipeline=pipeline,
        exception_backlog=backlog,
        model_usage=usage,
        agent_runs=agent,
        tool_usage=tools,
        latest_reconciliation=reconciliation,
        alerts=alerts_for(pipeline, backlog, usage, agent, reconciliation),
    )
