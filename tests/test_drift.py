from __future__ import annotations

import shutil
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from conftest import Loaded
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text

from app.config import REPO_ROOT, Settings, get_settings
from app.main import app
from db.migrate import migrate
from evals.drift_eval import score
from pipeline import drift
from pipeline.drift import diff, null_bucket
from pipeline.profile import load_masters

DATA = REPO_ROOT / "data"


# --- unit -------------------------------------------------------------------------------


@pytest.mark.parametrize(("pct", "bucket"), [(0.0, "sparse"), (1.07, "sparse"), (5.0, "sparse"),
                                              (12.0, "partial"), (60.0, "mostly")])
def test_null_bucket_is_coarse_enough_to_ignore_monthly_noise(pct: float, bucket: str) -> None:
    assert null_bucket(pct) == bucket


def test_diff_finds_renames_additions_and_type_changes() -> None:
    before = [["a", "string", "sparse", "shape:A9"], ["cust_no", "integer", "sparse", "integer"],
              ["d", "date", "sparse", "date:iso"]]
    after = [["a", "string", "sparse", "shape:A9"], ["customer_number", "integer", "sparse", "integer"],
             ["d", "date", "sparse", "date:us"], ["lot", "string", "sparse", "shape:A9"]]
    d = diff(before, after)
    assert (d.removed, d.added) == (["cust_no"], ["customer_number", "lot"])
    assert d.renames == [{"from": "cust_no", "to": "customer_number"}]
    assert d.changed == [{"column": "d", "from": ["date", "sparse", "date:iso"],
                          "to": ["date", "sparse", "date:us"]}]
    assert diff(before, before).empty


# --- full load of all three plants ------------------------------------------------------


@pytest.mark.postgres
def test_drift_eval_two_of_two_named_and_no_false_alerts(silver_db: Engine, loaded: Loaded) -> None:
    with silver_db.connect() as conn:
        result = score(conn, DATA)
    assert (result.detected, result.named) == (2, 2), [e.__dict__ for e in result.events]
    assert result.false_alerts == [] and len(result.scored) == 2
    d1, d2 = sorted(result.events, key=lambda e: e.drift_id)
    assert d1.file_name == "plt-02_orderlines_2026-03.csv" and d2.file_name == "plt-01_orderlines_2026-06.csv"
    assert "'cust_no' appears renamed to 'customer_number'" in (d1.message or "")
    broken = "Mapping for plt02 source column 'cust_no' is broken; gold.fact_delivery.customer_no"
    assert broken in (d1.message or "")
    added = "New source column 'lot_no' detected on plt01 with no mapping (likely canonical column lot_no)"
    assert added in (d2.message or "")


@pytest.mark.postgres
def test_backfill_rebuilds_fingerprints_from_bronze_without_new_alerts(
        silver_db: Engine, loaded: Loaded) -> None:
    with silver_db.begin() as conn:
        before = conn.execute(text("SELECT batch_id::text, fingerprint FROM ops.fingerprints")).all()
        alerts = conn.execute(text("SELECT count(*) FROM ops.drift_alerts")).scalar_one()
        conn.execute(text("DELETE FROM ops.fingerprints WHERE source = 'plt03'"))
        assert drift.backfill(conn, load_masters(DATA / "master")) == 18
        after = conn.execute(text("SELECT batch_id::text, fingerprint FROM ops.fingerprints")).all()
        assert sorted(map(tuple, after)) == sorted(map(tuple, before))  # same digests rebuilt from bronze
        assert conn.execute(text("SELECT count(*) FROM ops.drift_alerts")).scalar_one() == alerts
        conn.rollback()


@pytest.mark.postgres
def test_every_file_is_fingerprinted_once(silver_db: Engine, loaded: Loaded) -> None:
    with silver_db.connect() as conn:
        counts = conn.execute(text("SELECT count(*), count(DISTINCT batch_id), count(DISTINCT fingerprint) "
                                   "FROM ops.fingerprints")).one()
    assert tuple(counts) == (54, 54, 5)  # five distinct shapes across 54 files: 2 + 2 + 1


# --- API: resolving needs a confirmed mapping for the new shape, and an owner ------------


@pytest.fixture
def api(pg_url: str, tmp_path: Path) -> Iterator[TestClient]:
    if not (DATA / "sources" / "plt02").is_dir():
        pytest.skip("data/ not generated (run `make synth`)")
    root = tmp_path / "data"
    shutil.copytree(DATA / "master", root / "master")
    (root / "sources" / "plt02").mkdir(parents=True)
    for month in ("2026-02", "2026-03"):
        name = f"plt-02_orderlines_{month}.csv"
        shutil.copy(DATA / "sources" / "plt02" / name, root / "sources" / "plt02" / name)
    migrate(pg_url)
    settings = Settings(database_url=pg_url, llm_provider="none", data_dir=root,
                        api_keys={"k-viewer": "viewer", "k-analyst": "analyst", "k-owner": "owner"})
    app.dependency_overrides[get_settings] = lambda: settings
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def _as(role: str) -> dict[str, str]:
    return {"X-API-Key": f"k-{role}"}


@pytest.mark.postgres
def test_rename_raises_one_alert_and_resolution_waits_for_a_confirmed_mapping(api: TestClient) -> None:
    proposed = api.post("/sources/plt02/mappings/propose", headers=_as("analyst")).json()
    versions: list[dict[str, Any]] = proposed["versions"]
    old_header, new_header = versions
    api.post(f"/mappings/{old_header['mapping_version_id']}/confirm", headers=_as("owner"))

    ingested = api.post("/ingest/plt02",
                        headers={**_as("analyst"), "Idempotency-Key": "drift-api-0001"}).json()
    assert [f["status"] for f in ingested["files"]] == ["validated", "blocked"]
    (alert_id,) = ingested["drift_alerts"]
    alerts = api.get("/drift/alerts", params={"source": "plt02", "status": "open"}, headers=_as("viewer"))
    alert = alerts.json()[0]
    assert alert["alert_id"] == alert_id and alert["file_name"] == "plt-02_orderlines_2026-03.csv"
    assert alert["diff"]["affected_canonical"] == ["customer_no"]
    assert not alert["header_has_confirmed_mapping"]

    body = {"resolution": "PLT-02 renamed the column; remapped customer_number to customer_no"}
    early = api.post(f"/drift/alerts/{alert_id}/resolve", headers=_as("owner"), json=body)
    assert early.status_code == 409 and "confirm a mapping" in early.text

    api.post(f"/mappings/{new_header['mapping_version_id']}/confirm", headers=_as("owner"))
    assert api.post(f"/drift/alerts/{alert_id}/resolve", headers=_as("analyst"), json=body).status_code == 403
    resolved = api.post(f"/drift/alerts/{alert_id}/resolve", headers=_as("owner"), json=body)
    assert resolved.status_code == 200 and resolved.json()["status"] == "resolved"
    assert api.post(f"/drift/alerts/{alert_id}/resolve", headers=_as("owner"), json=body).status_code == 409

    again = api.post("/ingest/plt02", headers={**_as("analyst"), "Idempotency-Key": "drift-api-0002"}).json()
    assert [f["status"] for f in again["files"]] == ["validated", "validated"] and again["drift_alerts"] == []
