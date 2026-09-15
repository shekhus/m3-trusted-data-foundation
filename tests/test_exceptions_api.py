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

DATA = REPO_ROOT / "data"
KEYS = {"k-viewer": "viewer", "k-analyst": "analyst", "k-owner": "owner"}
pytestmark = pytest.mark.postgres


@pytest.fixture
def api(pg_url: str, tmp_path: Path) -> Iterator[tuple[TestClient, str]]:
    """Two PLT-01 months ingested and validated: a real queue with blocking and warning exceptions."""
    if not (DATA / "sources" / "plt01").is_dir():
        pytest.skip("data/ not generated (run `make synth`)")
    root = tmp_path / "data"
    shutil.copytree(DATA / "master", root / "master")
    (root / "sources" / "plt01").mkdir(parents=True)
    for f in sorted((DATA / "sources" / "plt01").glob("*.csv"))[:2]:
        shutil.copy(f, root / "sources" / "plt01" / f.name)
    migrate(pg_url)
    settings = Settings(database_url=pg_url, llm_provider="none", api_keys=KEYS, data_dir=root)
    app.dependency_overrides[get_settings] = lambda: settings
    client = TestClient(app)
    proposal = client.post("/sources/plt01/mappings/propose", headers=_as("analyst")).json()
    client.post(f"/mappings/{proposal['versions'][0]['mapping_version_id']}/confirm", headers=_as("owner"))
    ingested = client.post("/ingest/plt01", headers={**_as("analyst"), "Idempotency-Key": "queue-test-0001"})
    assert ingested.status_code == 200 and ingested.json()["blocking_exceptions"] > 0
    try:
        yield client, pg_url
    finally:
        app.dependency_overrides.clear()


def _as(role: str) -> dict[str, str]:
    return {"X-API-Key": f"k-{role}"}


def _get(client: TestClient, path: str, **params: Any) -> Any:  # noqa: ANN401 - JSON
    response = client.get(path, params=params, headers=_as("viewer"))
    assert response.status_code == 200, response.text
    return response.json()


def _first(client: TestClient, **filters: Any) -> dict:  # noqa: ANN401
    return _get(client, "/exceptions", limit=1, **filters)["items"][0]


def test_list_filters_and_pages(api: tuple[TestClient, str]) -> None:
    client, _ = api
    everything = _get(client, "/exceptions", source="plt01", limit=500)
    assert len(everything["items"]) == min(everything["total"], 500) > 0
    blocking = _get(client, "/exceptions", severity="block", limit=500)
    assert blocking["total"] > 0 and {e["severity"] for e in blocking["items"]} == {"block"}
    v004 = _get(client, "/exceptions", rule_id="V004")
    assert {e["owner"] for e in v004["items"]} == {"master_data:customers"}
    page_two = _get(client, "/exceptions", limit=2, offset=2)
    assert page_two["total"] == everything["total"] and len(page_two["items"]) == 2
    bad = client.get("/exceptions", params={"batch_id": "not-a-uuid"}, headers=_as("viewer"))
    assert bad.status_code == 422


def test_detail_carries_the_raw_record_and_silver_row(api: tuple[TestClient, str]) -> None:
    client, _ = api
    exception = _first(client, rule_id="V004")
    detail = _get(client, f"/exceptions/{exception['exception_id']}")
    assert detail["raw_record"]["ord_no"] == exception["row_key"].split("|")[0]
    assert detail["silver_row"]["customer_no"] == detail["details"]["customer_no"]
    assert detail["events"] == []


def test_assign_then_owner_resolves_with_a_kind_and_history(api: tuple[TestClient, str]) -> None:
    client, _ = api
    exception_id = _first(client, rule_id="V005")["exception_id"]
    assigned = client.post(f"/exceptions/{exception_id}/assign", headers=_as("analyst"),
                           json={"owner": "master_data:items:jo"})
    assert assigned.status_code == 200
    assert (assigned.json()["status"], assigned.json()["owner"], assigned.json()["assigned_by"]) == (
        "assigned", "master_data:items:jo", "analyst")

    resolved = client.post(f"/exceptions/{exception_id}/resolve", headers=_as("owner"),
                           json={"kind": "exclude", "resolution": "item retired; line must not be reported"})
    assert resolved.status_code == 200
    body = resolved.json()
    assert (body["status"], body["resolution_kind"], body["resolved_by"]) == ("resolved", "exclude", "owner")
    history = _get(client, f"/exceptions/{exception_id}")["events"]
    assert [(e["action"], e["actor"]) for e in history] == [("assigned", "analyst"), ("resolved", "owner")]
    assert history[1]["details"]["kind"] == "exclude"

    assert client.post(f"/exceptions/{exception_id}/resolve", headers=_as("owner"),
                       json={"kind": "accept", "resolution": "changing my mind later"}).status_code == 409
    assert client.post(f"/exceptions/{exception_id}/assign", headers=_as("analyst"),
                       json={"owner": "someone"}).status_code == 409


def test_resolution_needs_a_real_reason_and_a_known_kind(api: tuple[TestClient, str]) -> None:
    client, _ = api
    exception_id = _first(client, status="open")["exception_id"]
    for body in ({"kind": "accept", "resolution": "ok"},
                 {"kind": "delete_row", "resolution": "long enough reason"},
                 {"kind": "no_longer_violated", "resolution": "only the system may use this kind"}):
        assert client.post(f"/exceptions/{exception_id}/resolve", headers=_as("owner"),
                           json=body).status_code == 422, body


def test_summary_counts_the_queue(api: tuple[TestClient, str]) -> None:
    client, _ = api
    summary = _get(client, "/exceptions/summary", source="plt01")
    total = _get(client, "/exceptions", source="plt01", limit=1)["total"]
    assert sum(summary["by_status"].values()) == total
    blocking = _get(client, "/exceptions", source="plt01", severity="block", limit=1)["total"]
    assert summary["open_blocking"] == blocking
    assert "master_data:customers" in summary["open_by_owner"]


def test_revalidate_keeps_decisions_and_closes_what_now_passes(api: tuple[TestClient, str]) -> None:
    client, url = api
    decided = _first(client, rule_id="V004")
    client.post(f"/exceptions/{decided['exception_id']}/resolve", headers=_as("owner"),
                json={"kind": "accept", "resolution": "customer onboarding in progress; line is genuine"})
    fixable = _first(client, rule_id="V007", status="open")

    engine = create_engine(url)
    with engine.begin() as conn:  # the plant resends a corrected quantity (simulated directly in silver)
        conn.execute(text("UPDATE silver.order_lines SET ordered_qty = abs(ordered_qty), invoiced_qty = "
                          "abs(invoiced_qty) WHERE batch_id = :b AND source_row = :r"),
                     {"b": fixable["batch_id"], "r": fixable["source_row"]})
    engine.dispose()

    same_batch = decided["batch_id"] == fixable["batch_id"]
    result = client.post(f"/batches/{fixable['batch_id']}/revalidate", headers=_as("analyst"))
    assert result.status_code == 200 and result.json()["auto_resolved"] == 1
    closed = _get(client, f"/exceptions/{fixable['exception_id']}")
    assert (closed["status"], closed["resolution_kind"], closed["resolved_by"]) == (
        "resolved", "no_longer_violated", "system:revalidation")
    assert [e["action"] for e in closed["events"]] == ["auto_resolved"]
    if same_batch:
        assert _get(client, f"/exceptions/{decided['exception_id']}")["resolution_kind"] == "accept"
    again = client.post(f"/batches/{fixable['batch_id']}/revalidate", headers=_as("analyst")).json()
    assert again["auto_resolved"] == 0

    assert client.post("/batches/not-a-uuid/revalidate", headers=_as("analyst")).status_code == 404
    assert client.post(f"/batches/{fixable['batch_id']}/revalidate", headers=_as("viewer")).status_code == 403
