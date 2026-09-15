from __future__ import annotations

import shutil
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

from app.config import REPO_ROOT, Settings, get_settings
from app.main import app
from db.migrate import migrate
from pipeline.lineage import backfill, record_lineage, views_using

DATA = REPO_ROOT / "data"
KEYS = {"k-viewer": "viewer", "k-analyst": "analyst", "k-owner": "owner"}
CONSUMERS = ["gold.consumer_legacy_otif_monthly_plant", "gold.consumer_ops_fill_rate_monthly_plant",
             "gold.consumer_ops_otif_monthly_plant", "gold.consumer_sales_fill_rate_daily_customer",
             "gold.consumer_sales_otif_daily_customer"]
ALL_VIEWS = [*CONSUMERS, "gold.fill_rate_v1_lines", "gold.otif_case_tolerance_v1_lines",
             "gold.otif_date_basis_v1_lines", "gold.otif_v2_lines", "gold.otif_v3_lines"]
# weight-based: every view except the count-based legacy v2, its consumer, and the count-tolerance reference
WEIGHT_VIEWS = [*CONSUMERS[1:], "gold.fill_rate_v1_lines", "gold.otif_date_basis_v1_lines",
                "gold.otif_v3_lines"]


# --- views derived from metrics/*.yaml ---------------------------------------------------


@pytest.mark.parametrize(
    ("gold_col", "views"),
    [
        ("customer_no", ALL_VIEWS),  # a dimension of every metric
        ("invoiced_weight_lb", WEIGHT_VIEWS),  # v2 is count-based
        ("invoiced_qty", ALL_VIEWS),
        ("requested_date", ["gold.otif_date_basis_v1_lines"]),  # only the reference definition
        ("lot_no", []),  # no metric reads it yet
    ],
)
def test_views_using_follows_the_metric_definitions(gold_col: str, views: list[str]) -> None:
    assert views_using("gold.fact_delivery", gold_col) == views


# --- API over a real rename -------------------------------------------------------------


@pytest.fixture
def client(pg_url: str, tmp_path: Path) -> Iterator[tuple[TestClient, str]]:
    """Two PLT-02 months either side of the cust_no → customer_number rename, both headers confirmed."""
    if not (DATA / "sources" / "plt02").is_dir():
        pytest.skip("data/ not generated (run `make synth`)")
    root = tmp_path / "data"
    shutil.copytree(DATA / "master", root / "master")
    (root / "sources" / "plt02").mkdir(parents=True)
    for month in ("2026-02", "2026-03"):
        name = f"plt-02_orderlines_{month}.csv"
        shutil.copy(DATA / "sources" / "plt02" / name, root / "sources" / "plt02" / name)
    migrate(pg_url)
    settings = Settings(database_url=pg_url, llm_provider="none", api_keys=KEYS, data_dir=root)
    app.dependency_overrides[get_settings] = lambda: settings
    test_client = TestClient(app)
    proposed = test_client.post("/sources/plt02/mappings/propose", headers=_as("analyst")).json()
    for version in proposed["versions"]:
        test_client.post(f"/mappings/{version['mapping_version_id']}/confirm", headers=_as("owner"))
    try:
        yield test_client, pg_url
    finally:
        app.dependency_overrides.clear()


def _as(role: str) -> dict[str, str]:
    return {"X-API-Key": f"k-{role}"}


def _get(client: TestClient, path: str, **params: Any) -> Any:  # noqa: ANN401 - JSON
    return client.get(path, params=params, headers=_as("viewer"))


@pytest.mark.postgres
def test_confirm_records_lineage_with_transforms(client: tuple[TestClient, str]) -> None:
    test_client, url = client
    engine = create_engine(url)
    with engine.connect() as conn:
        rows = conn.execute(text(
            "SELECT m.version, l.source_col, l.gold_col, l.transforms FROM ops.lineage l "
            "JOIN ops.mapping_versions m USING (mapping_version_id) ORDER BY 1, 2")).all()
    engine.dispose()
    assert sum(1 for r in rows if r.version == 1) == 15 and sum(1 for r in rows if r.version == 2) == 15
    weights = {(r.version, r.source_col): r.transforms for r in rows if r.gold_col == "ordered_weight_lb"}
    assert weights == {(1, "WT_ORD_KG"): ["kg_to_lb"], (2, "WT_ORD_KG"): ["kg_to_lb"]}


@pytest.mark.postgres
def test_impact_of_the_renamed_customer_column_names_gold_column_and_every_view(
        client: tuple[TestClient, str]) -> None:
    test_client, _ = client
    body = _get(test_client, "/lineage/impact", source="plt02", source_col="cust_no").json()
    assert body["gold_columns"] == ["gold.fact_delivery.customer_no"]
    assert body["views"] == ALL_VIEWS
    lineage = [(r["mapping_version"], r["transforms"]) for r in body["lineage"]]
    assert lineage == [(1, ["normalize_customer_no"])]
    assert "plt02 column 'cust_no' feeds gold.fact_delivery.customer_no" in body["summary"]


@pytest.mark.postgres
def test_impact_distinguishes_weight_from_count_metrics_and_unused_columns(
        client: tuple[TestClient, str]) -> None:
    test_client, _ = client
    weight = _get(test_client, "/lineage/impact", source="plt02", source_col="WT_SHIP_KG").json()
    assert weight["views"] == WEIGHT_VIEWS
    lot = _get(test_client, "/lineage/impact", source="plt02", source_col="LOTID").json()
    assert lot["gold_columns"] == ["gold.fact_delivery.lot_no"] and lot["views"] == []
    assert _get(test_client, "/lineage/impact", source="plt02", source_col="nope").status_code == 404


@pytest.mark.postgres
def test_upstream_shows_both_headers_feeding_customer_no(client: tuple[TestClient, str]) -> None:
    test_client, _ = client
    rows = _get(test_client, "/lineage/upstream", gold_table="gold.fact_delivery",
                gold_col="customer_no").json()
    assert [(r["source"], r["source_col"], r["mapping_version"]) for r in rows] == [
        ("plt02", "cust_no", 1), ("plt02", "customer_number", 2)]


@pytest.mark.postgres
def test_superseded_mapping_leaves_current_impact_but_keeps_history(client: tuple[TestClient, str]) -> None:
    test_client, _ = client
    v1 = next(v for v in test_client.get("/sources/plt02/mappings", headers=_as("viewer")).json()
              if v["version"] == 1)
    # a person corrects the old header's mapping (drops the lot column); confirming it supersedes v1
    columns = [c if c["canonical_col"] != "lot_no" else {**c, "canonical_col": None, "transforms": []}
               for c in v1["columns"]]
    human = test_client.post("/sources/plt02/mappings", headers=_as("analyst"),
                             json={"header": v1["header"], "columns": columns}).json()
    assert test_client.post(f"/mappings/{human['mapping_version_id']}/confirm",
                            headers=_as("owner")).status_code == 200
    current = _get(test_client, "/lineage/upstream", gold_table="gold.fact_delivery",
                   gold_col="lot_no").json()
    assert [(r["source_col"], r["mapping_version"]) for r in current] == [("LOTID", 2)]
    history = _get(test_client, "/lineage/upstream", gold_table="gold.fact_delivery", gold_col="lot_no",
                   include_superseded=True).json()
    assert [(r["source_col"], r["mapping_version"], r["mapping_status"]) for r in history] == [
        ("LOTID", 1, "superseded"), ("LOTID", 2, "confirmed")]


@pytest.mark.postgres
def test_backfill_restores_lineage_for_mappings_confirmed_without_it(client: tuple[TestClient, str]) -> None:
    _, url = client
    engine = create_engine(url)
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM ops.lineage"))
        assert backfill(conn) == 30
        assert backfill(conn) == 0
    engine.dispose()


@pytest.mark.postgres
def test_ingest_records_lineage_for_mappings_confirmed_without_it(client: tuple[TestClient, str]) -> None:
    test_client, url = client
    engine = create_engine(url)
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM ops.lineage"))  # as for a mapping confirmed before lineage existed
    ingested = test_client.post("/ingest/plt02",
                                headers={**_as("analyst"), "Idempotency-Key": "lineage-0001"})
    assert ingested.status_code == 200 and ingested.json()["validated"] == 2
    with engine.begin() as conn:
        assert conn.execute(text("SELECT count(*) FROM ops.lineage")).scalar_one() == 30
        version_id = conn.execute(
            text("SELECT min(mapping_version_id) FROM ops.mapping_versions")).scalar_one()
        assert record_lineage(conn, version_id) == 0  # idempotent
    engine.dispose()
