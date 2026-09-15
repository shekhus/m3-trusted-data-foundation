from __future__ import annotations

from collections.abc import Iterator

import pytest
from conftest import Loaded
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text

from app.config import REPO_ROOT, Settings, get_settings
from app.main import app
from app.routers.ops import alerts_for

KEYS = {"k-viewer": "viewer"}


def test_alerts_name_the_condition_and_the_runbook_section() -> None:
    pipeline = [{"source": "plt02", "failed": 1, "blocked": 2, "stale": 0}]
    backlog = [
        {"severity": "block", "oldest_age_days": 4.5, "exceptions": 7},
        {"severity": "warn", "oldest_age_days": 30.0, "exceptions": 100},
    ]
    usage = [{"rate_limited": 3}]
    agent = [{"status": "awaiting_approval", "runs": 2}, {"status": "completed", "runs": 9}]
    alerts = alerts_for(pipeline, backlog, usage, agent, {"status": "red"})
    assert alerts == [
        "plt02: 1 failed batch(es) — re-run ingest (RUNBOOK: failed ingest)",
        "plt02: 2 batch(es) blocked on an unconfirmed mapping (RUNBOOK: mappings)",
        "7 blocking exception(s) older than 3 days, past SOP-DQ-001-v2's window (RUNBOOK: backlog)",
        "3 model call(s) rate limited in the window (RUNBOOK: provider limits)",
        "2 agent run(s) awaiting an owner's decision (RUNBOOK: agent)",
        "latest reconciliation is red: consumer views disagree (RUNBOOK: reconciliation)",
    ]
    assert alerts_for([], [], [], [], {"status": "green"}) == []


@pytest.fixture
def client(silver_db: Engine, loaded: Loaded) -> Iterator[TestClient]:
    settings = Settings(
        database_url=silver_db.url.render_as_string(hide_password=False),
        llm_provider="none",
        data_dir=REPO_ROOT / "data",
        api_keys=KEYS,
    )
    app.dependency_overrides[get_settings] = lambda: settings
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


@pytest.mark.postgres
def test_ops_summary_reads_the_views_for_any_role(client: TestClient, silver_db: Engine) -> None:
    assert client.get("/ops/summary").status_code == 401
    body = client.get("/ops/summary", params={"days": 30}, headers={"X-API-Key": "k-viewer"}).json()
    by_source = {p["source"]: p for p in body["pipeline"]}
    with silver_db.connect() as conn:
        rows = conn.execute(text("SELECT source, count(*) FROM ops.batches GROUP BY 1")).all()
        batches = {r[0]: r[1] for r in rows}
        open_exceptions = conn.execute(
            text("SELECT count(*) FROM ops.exceptions WHERE status <> 'resolved'")
        ).scalar_one()
    assert {s: p["batches"] for s, p in by_source.items()} == batches
    assert sum(b["exceptions"] for b in body["exception_backlog"]) <= open_exceptions
    assert set(body) >= {"pipeline", "exception_backlog", "model_usage", "agent_runs", "tool_usage", "alerts"}
    assert (
        client.get("/ops/summary", params={"days": 0}, headers={"X-API-Key": "k-viewer"}).status_code == 422
    )
