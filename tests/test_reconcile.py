from __future__ import annotations

import shutil
from collections.abc import Iterator
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text

from app.config import REPO_ROOT, Settings, get_settings
from app.main import app
from db.migrate import migrate
from evals.reconciliation_eval import score
from metrics.compiler import CONSUMERS_FILE, CompileError, compile_all, compile_consumers
from pipeline.publish import publish_source
from pipeline.reconcile import RunResult, run

DATA = REPO_ROOT / "data"


# --- consumer catalogue compiles, and rejects what cannot reconcile ------------------------


def _catalog_with(tmp_path: Path, edit: dict) -> Path:
    catalog = yaml.safe_load(CONSUMERS_FILE.read_text(encoding="utf-8"))
    for key, value in edit.items():
        index, field, new = value
        catalog[key][index][field] = new
    path = tmp_path / "consumers.yaml"
    path.write_text(yaml.safe_dump(catalog), encoding="utf-8")
    return path


def test_repo_consumer_catalog_compiles_to_views() -> None:
    compiled = compile_consumers(compile_all())
    assert {c.view for c in compiled.catalog.consumers} >= {
        "gold.consumer_ops_otif_monthly_plant",
        "gold.consumer_sales_otif_daily_customer",
        "gold.consumer_legacy_otif_monthly_plant",
    }
    assert "CREATE VIEW gold.consumer_sales_otif_daily_customer" in compiled.ddl


@pytest.mark.parametrize(
    ("edit", "message"),
    [
        ({"consumers": (0, "version", 9)}, "no metric otif v9"),
        ({"consumers": (2, "legacy", False)}, "legacy flag must match"),
        ({"consumers": (0, "metric", "otif_date_basis")}, "legacy flag|reference definitions"),
        ({"reconcile": (0, "b", "nobody")}, "unknown consumer"),
        ({"reconcile": (0, "numerator", "missing_sum")}, "lacks aggregations"),
        ({"reconcile": (0, "grain", ["month", "customer_no"])}, "cannot roll up"),
    ],
)
def test_consumer_catalog_rejects_views_that_cannot_reconcile(
    tmp_path: Path, edit: dict, message: str
) -> None:
    metrics = compile_all()
    if "otif_date_basis" in str(edit):
        path = _catalog_with(tmp_path, {"consumers": (0, "metric", "otif_date_basis")})
        catalog = yaml.safe_load(path.read_text(encoding="utf-8"))
        catalog["consumers"][0]["version"] = 1
        path.write_text(yaml.safe_dump(catalog), encoding="utf-8")
    else:
        path = _catalog_with(tmp_path, edit)
    with pytest.raises(CompileError, match=message):
        compile_consumers(metrics, path)


# --- on the answer key's population: every criterion of reconciliation.json ---------------


@pytest.fixture(scope="module")
def generator_run(generator_gold: Engine) -> RunResult:
    return run(generator_gold, DATA, "test")


@pytest.mark.postgres
def test_reconciliation_on_generator_gold_meets_every_scoring_criterion(
    generator_gold: Engine, generator_run: RunResult
) -> None:
    assert generator_run.status == "green" and generator_run.consumer_red == 0
    assert generator_run.consumer_periods == 19 * 3 * 2  # 19 months × 3 plants × (OTIF, fill rate)
    with generator_gold.connect() as conn:
        result = score(conn, DATA, generator_run.run_id)
    by_tool = {t.tool: t for t in result.tools}
    assert result.passed, [t.__dict__ for t in result.tools]
    legacy, dashboard = by_tool["tool1_legacy_report_suite"], by_tool["tool2_dashboard_tool"]
    assert (legacy.identified, legacy.dominant) == (["weight_basis"], "weight_basis")
    assert (dashboard.identified, dashboard.dominant) == (
        ["date_basis", "scope_filter", "weight_basis"],
        "date_basis",
    )
    # a 2% case tolerance never changes a verdict in this data, so neither export can confirm or rule it out
    assert dashboard.undetermined == ["case_tolerance"] and legacy.undetermined == ["case_tolerance"]
    assert dashboard.worst_contribution_error == legacy.worst_contribution_error == 0.0
    assert dashboard.lines_dropped_exact


@pytest.mark.postgres
def test_legacy_view_gap_is_reported_not_hidden(generator_gold: Engine, generator_run: RunResult) -> None:
    with generator_gold.connect() as conn:
        gaps = conn.execute(
            text(
                "SELECT count(*) FILTER (WHERE legacy_delta <> 0), count(*) FROM ops.reconciliation "
                "WHERE run_id = :r AND metric = 'otif'"
            ),
            {"r": generator_run.run_id},
        ).one()
    assert gaps[1] == 57 and gaps[0] > 0


# --- on our own published gold ------------------------------------------------------------


@pytest.mark.postgres
def test_consumer_views_agree_on_published_gold(silver_db: Engine, loaded: object) -> None:
    with silver_db.begin() as conn:
        conn.execute(
            text(
                "UPDATE ops.drift_alerts SET status = 'resolved', resolved_by = 'owner', "
                "resolved_at = now(), resolution = 'remapped after review' WHERE status = 'open'"
            )
        )
    for source in ("plt01", "plt02", "plt03"):
        publish_source(silver_db, source, "owner", DATA / "master")
    result = run(silver_db, DATA, "test")
    assert result.status == "green" and result.consumer_red == 0 and result.consumer_periods == 114
    # the published population holds back blocked rows, so the tools' exports are reproduced almost, not
    # exactly
    for summary in result.tools.values():
        assert summary["export_match_share"] > 0.5
    assert result.tools["tool1_legacy_report_suite"]["dominant_cause"] == "weight_basis"
    assert result.tools["tool2_dashboard_tool"]["dominant_cause"] == "date_basis"


# --- API ----------------------------------------------------------------------------------


@pytest.fixture
def api(pg_url: str, tmp_path: Path) -> Iterator[TestClient]:
    root = tmp_path / "data"
    shutil.copytree(DATA / "master", root / "master")
    shutil.copytree(DATA / "report_feed", root / "report_feed")
    migrate(pg_url)
    settings = Settings(
        database_url=pg_url,
        llm_provider="none",
        data_dir=root,
        api_keys={"k-viewer": "viewer", "k-analyst": "analyst"},
    )
    app.dependency_overrides[get_settings] = lambda: settings
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


@pytest.mark.postgres
def test_reconcile_api_roles_and_no_data(api: TestClient) -> None:
    assert api.get("/reconcile/latest", headers={"X-API-Key": "k-viewer"}).status_code == 404
    assert api.post("/reconcile", headers={"X-API-Key": "k-viewer"}).status_code == 403
    body = api.post("/reconcile", headers={"X-API-Key": "k-analyst"}).json()
    assert body["status"] == "no_data" and body["consumers"] == [] and body["tools"] == []
    assert api.get("/reconcile/latest", headers={"X-API-Key": "k-viewer"}).json()["run_id"] == body["run_id"]
