"""CLAUDE.md principle 7 on the exception queue: only an owner resolves; viewers cannot change anything.

Mirrors plan §2.6 "Analyst tries to confirm a mapping → 403" for exceptions.
"""

from __future__ import annotations

import shutil
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import REPO_ROOT, Settings, get_settings
from app.main import app
from db.migrate import migrate

DATA = REPO_ROOT / "data"
KEYS = {"k-viewer": "viewer", "k-analyst": "analyst", "k-owner": "owner"}


@pytest.fixture
def client(pg_url: str, tmp_path: Path) -> Iterator[TestClient]:
    if not (DATA / "sources" / "plt01").is_dir():
        pytest.skip("data/ not generated (run `make synth`)")
    root = tmp_path / "data"
    shutil.copytree(DATA / "master", root / "master")
    (root / "sources" / "plt01").mkdir(parents=True)
    first = sorted((DATA / "sources" / "plt01").glob("*.csv"))[0]
    shutil.copy(first, root / "sources" / "plt01" / first.name)
    migrate(pg_url)
    app.dependency_overrides[get_settings] = lambda: Settings(database_url=pg_url, llm_provider="none",
                                                              api_keys=KEYS, data_dir=root)
    test_client = TestClient(app)
    proposal = test_client.post("/sources/plt01/mappings/propose", headers={"X-API-Key": "k-analyst"}).json()
    test_client.post(f"/mappings/{proposal['versions'][0]['mapping_version_id']}/confirm",
                     headers={"X-API-Key": "k-owner"})
    test_client.post("/ingest/plt01", headers={"X-API-Key": "k-analyst", "Idempotency-Key": "perm-test-0001"})
    try:
        yield test_client
    finally:
        app.dependency_overrides.clear()


@pytest.mark.postgres
@pytest.mark.parametrize("key", ["k-analyst", "k-viewer"])
def test_only_owner_resolves(client: TestClient, key: str) -> None:
    exception = client.get("/exceptions", params={"limit": 1}, headers={"X-API-Key": key}).json()["items"][0]
    refused = client.post(f"/exceptions/{exception['exception_id']}/resolve", headers={"X-API-Key": key},
                          json={"kind": "accept", "resolution": "trying to close it without authority"})
    assert refused.status_code == 403
    after = client.get(f"/exceptions/{exception['exception_id']}", headers={"X-API-Key": key}).json()
    assert after["status"] == exception["status"] and after["events"] == []


@pytest.mark.postgres
def test_viewer_cannot_assign(client: TestClient) -> None:
    exception_id = client.get("/exceptions", params={"limit": 1},
                              headers={"X-API-Key": "k-viewer"}).json()["items"][0]["exception_id"]
    assert client.post(f"/exceptions/{exception_id}/assign", headers={"X-API-Key": "k-viewer"},
                       json={"owner": "me"}).status_code == 403
