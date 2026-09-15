from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.config import REPO_ROOT, Settings, get_settings
from app.main import app
from db.migrate import migrate

DATA = REPO_ROOT / "data"
KEYS = {"viewer": "k-viewer", "analyst": "k-analyst", "owner": "k-owner"}

pytestmark = pytest.mark.postgres


def headers(role: str) -> dict[str, str]:
    return {"X-API-Key": KEYS[role]}


@pytest.fixture
def client(pg_url: str) -> Iterator[TestClient]:
    if not (DATA / "sources").is_dir():
        pytest.skip("data/ not generated (run `make synth`)")
    migrate(pg_url)
    settings = Settings(database_url=pg_url, api_keys={v: k for k, v in KEYS.items()}, data_dir=DATA)
    app.dependency_overrides[get_settings] = lambda: settings
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def _propose(client: TestClient, source: str = "plt02") -> list[dict]:
    response = client.post(f"/sources/{source}/mappings/propose", headers=headers("analyst"))
    assert response.status_code == 200, response.text
    return response.json()


def test_propose_stores_one_version_per_header_variant(client: TestClient) -> None:
    proposals = _propose(client)
    assert [p["version"] for p in proposals] == [1, 2]
    assert {p["status"] for p in proposals} == {"proposed"}
    assert len({p["header_hash"] for p in proposals}) == 2
    renamed = [c["source_col"] for p in proposals for c in p["columns"]
               if c["canonical_col"] == "customer_no"]
    assert renamed == ["cust_no", "customer_number"]


def test_repeat_propose_does_not_create_duplicate_versions(client: TestClient) -> None:
    first = _propose(client)
    again = _propose(client)
    assert [p["mapping_version_id"] for p in again] == [p["mapping_version_id"] for p in first]
    assert len(client.get("/sources/plt02/mappings", headers=headers("viewer")).json()) == 2


def test_owner_confirms_and_reconfirm_supersedes(client: TestClient) -> None:
    first = _propose(client)[0]
    confirmed = client.post(f"/mappings/{first['mapping_version_id']}/confirm", headers=headers("owner"))
    assert confirmed.status_code == 200 and confirmed.json()["status"] == "confirmed"
    assert confirmed.json()["confirmed_by"] == "owner"

    # a person corrects the same header (drops the lot column) → new version, confirm supersedes the old one
    columns = [c if c["canonical_col"] != "lot_no" else {**c, "canonical_col": None, "transforms": []}
               for c in first["columns"]]
    human = client.post("/sources/plt02/mappings", headers=headers("analyst"),
                        json={"header": first["header"], "columns": columns})
    assert human.status_code == 201 and human.json()["proposed_by"] == "human"
    assert client.post(f"/mappings/{human.json()['mapping_version_id']}/confirm",
                       headers=headers("owner")).status_code == 200
    statuses = {v["version"]: v["status"] for v in client.get("/sources/plt02/mappings",
                                                                headers=headers("viewer")).json()}
    assert statuses == {1: "superseded", 2: "proposed", 3: "confirmed"}


def test_deciding_twice_is_a_conflict(client: TestClient) -> None:
    version_id = _propose(client)[0]["mapping_version_id"]
    assert client.post(f"/mappings/{version_id}/reject", headers=headers("owner")).status_code == 200
    assert client.post(f"/mappings/{version_id}/confirm", headers=headers("owner")).status_code == 409


def test_mapping_missing_required_columns_cannot_be_confirmed(client: TestClient) -> None:
    first = _propose(client)[0]
    columns = [c if c["canonical_col"] != "plant" else {**c, "canonical_col": None, "transforms": []}
               for c in first["columns"]]
    human = client.post("/sources/plt02/mappings", headers=headers("analyst"),
                        json={"header": first["header"], "columns": columns}).json()
    response = client.post(f"/mappings/{human['mapping_version_id']}/confirm", headers=headers("owner"))
    assert response.status_code == 422 and "plant" in response.text


def test_unknown_source_and_missing_key(client: TestClient) -> None:
    assert client.post("/sources/nope/mappings/propose", headers=headers("owner")).status_code == 404
    assert client.get("/sources/plt02/mappings").status_code == 401
    assert client.get("/sources/plt02/mappings", headers={"X-API-Key": "wrong"}).status_code == 401
