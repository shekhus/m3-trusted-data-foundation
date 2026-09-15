from __future__ import annotations

import json
import os
import shutil
import time
from collections.abc import Iterator
from pathlib import Path

import pytest
from conftest import Loaded
from fastapi.testclient import TestClient
from sqlalchemy import Connection, Engine, text

from app.config import REPO_ROOT, Settings, get_settings
from app.main import app
from db.migrate import migrate
from metrics.compiler import aggregate_sql, apply, compile_all
from pipeline.publish import publish_batch, publish_source
from pipeline.validate import load_masters, validate_batch

DATA = REPO_ROOT / "data"
MASTER = DATA / "master"
pytestmark = pytest.mark.postgres

ELIGIBLE = ("SELECT count(*) FROM silver.order_lines s JOIN ops.batches b USING (batch_id) "
            "WHERE b.status = 'published' AND NOT EXISTS (SELECT 1 FROM ops.exceptions e "
            "WHERE e.batch_id = s.batch_id AND e.source_row = s.source_row "
            "AND ((e.status <> 'resolved' AND e.severity = 'block') "
            "OR e.resolution_kind IN ('exclude', 'fixed_at_source')))")


def _scalar(conn: Connection, sql: str, **params: object) -> int:
    return int(conn.execute(text(sql), params).scalar_one())


@pytest.fixture(scope="module")
def first_publish(silver_db: Engine, loaded: Loaded) -> dict[str, list]:
    return {s: publish_source(silver_db, s, "owner", MASTER) for s in ("plt01", "plt02", "plt03")}


def test_open_drift_alerts_refuse_their_batches_and_nothing_else(first_publish: dict[str, list]) -> None:
    refused = {s: sorted(r.file_name[-11:-4] for r in results if r.status == "refused")
               for s, results in first_publish.items()}
    assert refused == {"plt01": ["2026-06", "2026-07", "2026-08"],
                       "plt02": ["2026-03", "2026-04", "2026-05", "2026-06", "2026-07", "2026-08"],
                       "plt03": []}
    reasons = {reason for results in first_publish.values() for r in results for reason in r.reasons}
    assert all(reason.startswith("open drift alert") for reason in reasons)


def test_published_rows_are_exactly_the_eligible_silver_rows(silver_db: Engine,
                                                             first_publish: dict[str, list]) -> None:
    published = sum(r.rows_published for results in first_publish.values() for r in results)
    with silver_db.connect() as conn:
        gold = _scalar(conn, "SELECT count(*) FROM gold.fact_delivery")
        assert published == _scalar(conn, ELIGIBLE) == gold
        # nothing held for a blank required field: every blank already has its V001 exception
        held = [r.rows_held for results in first_publish.values() for r in results]
        assert all("required field blank" not in h for h in held)
        enriched = conn.execute(text(
            "SELECT count(*) FILTER (WHERE catch_weight_flag IS NULL) AS no_flag, "
            "count(*) FILTER (WHERE lot_no IS NOT NULL AND expiry_date IS NULL) AS no_expiry, "
            "count(*) FILTER (WHERE batch_id IS NULL OR source_row IS NULL) AS no_provenance "
            "FROM gold.fact_delivery")).one()
    assert tuple(enriched) == (0, 0, 0)


def test_resolving_drift_then_republishing_publishes_everything_once(silver_db: Engine,
                                                                     first_publish: dict[str, list]) -> None:
    with silver_db.begin() as conn:
        conn.execute(text("UPDATE ops.drift_alerts SET status = 'resolved', resolved_by = 'owner', "
                          "resolved_at = now(), resolution = 'remapped after review'"))
    results = [r for s in ("plt01", "plt02", "plt03") for r in publish_source(silver_db, s, "owner", MASTER)]
    assert len(results) == 54 and {r.status for r in results} == {"published"}
    with silver_db.connect() as conn:
        gold = _scalar(conn, "SELECT count(*) FROM gold.fact_delivery")
        silver = _scalar(conn, "SELECT count(*) FROM silver.order_lines")
        blocked_rows = _scalar(conn, "SELECT count(DISTINCT (batch_id, source_row)) FROM ops.exceptions "
                                     "WHERE severity = 'block' AND status <> 'resolved'")
        assert gold == silver - blocked_rows == _scalar(conn, ELIGIBLE)
        keys = _scalar(conn, "SELECT count(DISTINCT (order_no, line_no)) FROM gold.fact_delivery")
    assert keys == gold

    again = [r for s in ("plt01", "plt02", "plt03") for r in publish_source(silver_db, s, "owner", MASTER)]
    assert sum(r.rows_published for r in again) == gold  # re-publishing changes nothing


def test_governed_otif_on_published_gold_stays_close_to_the_answer_key(
        silver_db: Engine, first_publish: dict[str, list]) -> None:
    """Our gold holds back blocked rows and keeps PLT-02's missing weights, so it is not the generator's clean
    gold; the governed OTIF per month must still land within a point of reconciliation.json."""
    truth = {m["month"]: m for m in json.loads((DATA / "ground_truth" / "reconciliation.json")
                                                .read_text(encoding="utf-8"))["monthly"]}
    otif = next(c for c in compile_all() if (c.definition.metric, c.definition.version) == ("otif", 3))
    with silver_db.begin() as conn:  # every batch published, whatever order the tests ran in
        conn.execute(text("UPDATE ops.drift_alerts SET status = 'resolved', resolved_by = 'owner', "
                          "resolved_at = now(), resolution = 'remapped after review' WHERE status = 'open'"))
    for source in ("plt01", "plt02", "plt03"):
        publish_source(silver_db, source, "owner", MASTER)
    with silver_db.begin() as conn:
        apply(conn, compile_all())
        rows = conn.execute(text(aggregate_sql(otif, ["month"]))).mappings().all()
    deltas = {r["month"]: abs(float(r["otif_rate"]) - truth[r["month"]]["governed_otif"]) for r in rows}
    assert set(deltas) == set(truth) and max(deltas.values()) < 0.01, deltas


def test_resolution_kinds_decide_what_republish_includes(silver_db: Engine,
                                                          first_publish: dict[str, list]) -> None:
    with silver_db.begin() as conn:
        accept = conn.execute(text("SELECT exception_id, batch_id::text, source_row FROM ops.exceptions "
                                   "WHERE rule_id = 'V004' AND status = 'open' LIMIT 1")).one()
        warn = conn.execute(text("SELECT exception_id, batch_id::text, source_row FROM ops.exceptions "
                                 "WHERE rule_id = 'V009' AND status = 'open' LIMIT 1")).one()
        for exception_id, kind in ((accept.exception_id, "accept"), (warn.exception_id, "exclude")):
            conn.execute(text("UPDATE ops.exceptions SET status = 'resolved', resolution_kind = :k, "
                              "resolution = 'decided in test', resolved_by = 'owner', resolved_at = now() "
                              "WHERE exception_id = :id"), {"k": kind, "id": exception_id})
        for batch_id in {accept.batch_id, warn.batch_id}:
            publish_batch(conn, batch_id, "owner", MASTER)
        in_gold = "SELECT count(*) FROM gold.fact_delivery WHERE batch_id = :b AND source_row = :r"
        # the accepted unknown customer is published; the excluded warning row is not
        assert _scalar(conn, in_gold, b=accept.batch_id, r=accept.source_row) == 1
        assert _scalar(conn, in_gold, b=warn.batch_id, r=warn.source_row) == 0
        conn.rollback()


def test_revalidating_a_published_batch_withdraws_it_until_republished(
        silver_db: Engine, first_publish: dict[str, list]) -> None:
    with silver_db.begin() as conn:
        batch_id = conn.execute(text("SELECT batch_id::text FROM ops.batches WHERE status = 'published' "
                                     "AND source = 'plt03' LIMIT 1")).scalar_one()
        before = _scalar(conn, "SELECT count(*) FROM gold.fact_delivery WHERE batch_id = :b", b=batch_id)
        assert before > 0
        validate_batch(conn, batch_id, load_masters(MASTER))
        assert _scalar(conn, "SELECT count(*) FROM gold.fact_delivery WHERE batch_id = :b", b=batch_id) == 0
        last = conn.execute(text("SELECT status FROM ops.publishes WHERE batch_id = :b "
                                 "ORDER BY publish_id DESC LIMIT 1"), {"b": batch_id}).scalar_one()
        assert last == "withdrawn"
        assert publish_batch(conn, batch_id, "owner", MASTER).rows_published == before
        conn.rollback()


# --- API: owner only, stale refusal, history ---------------------------------------------


@pytest.fixture
def api(pg_url: str, tmp_path: Path) -> Iterator[tuple[TestClient, Path]]:
    if not (DATA / "sources" / "plt03").is_dir():
        pytest.skip("data/ not generated (run `make synth`)")
    root = tmp_path / "data"
    shutil.copytree(MASTER, root / "master")
    (root / "sources" / "plt03").mkdir(parents=True)
    for f in sorted((DATA / "sources" / "plt03").glob("*.csv"))[:2]:
        shutil.copy(f, root / "sources" / "plt03" / f.name)
    stale = sorted((root / "sources" / "plt03").glob("*.csv"))[0]
    week_ago = time.time() - 7 * 24 * 3600
    os.utime(stale, (week_ago, week_ago))
    migrate(pg_url)
    settings = Settings(database_url=pg_url, llm_provider="none", data_dir=root, source_sla_hours=72,
                        api_keys={"k-viewer": "viewer", "k-analyst": "analyst", "k-owner": "owner"})
    app.dependency_overrides[get_settings] = lambda: settings
    client = TestClient(app)
    version = client.post("/sources/plt03/mappings/propose", headers={"X-API-Key": "k-analyst"}).json()
    client.post(f"/mappings/{version['versions'][0]['mapping_version_id']}/confirm",
                headers={"X-API-Key": "k-owner"})
    client.post("/ingest/plt03", headers={"X-API-Key": "k-analyst", "Idempotency-Key": "publish-api-0001"})
    try:
        yield client, stale
    finally:
        app.dependency_overrides.clear()


def test_publish_api_is_owner_only_refuses_stale_and_keeps_history(api: tuple[TestClient, Path]) -> None:
    client, stale = api
    assert client.post("/publish/plt03", headers={"X-API-Key": "k-analyst"}).status_code == 403
    body = client.post("/publish/plt03", headers={"X-API-Key": "k-owner"}).json()
    assert (body["published"], body["refused"]) == (1, 1)
    refused = next(b for b in body["batches"] if b["status"] == "refused")
    assert refused["file_name"] == stale.name and refused["reasons"] == [
        "extract is stale (older than the source SLA)"]
    assert body["gold_rows_for_source"] == body["rows_published"] > 0
    assert body["rows_held"]["open blocking exception"] > 0
    history = client.get("/publish/history", params={"source": "plt03"},
                         headers={"X-API-Key": "k-viewer"}).json()
    assert sorted(h["status"] for h in history) == ["published", "refused"]
