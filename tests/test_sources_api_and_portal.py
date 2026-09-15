from __future__ import annotations

import shutil
from collections.abc import Iterator
from pathlib import Path
from typing import cast

import httpx
import pytest
from fastapi.testclient import TestClient
from streamlit.testing.v1 import AppTest

from app.config import REPO_ROOT, Settings, get_settings
from app.main import app
from db.migrate import migrate
from portal.api import Api

DATA = REPO_ROOT / "data"
KEYS = {"k-viewer": "viewer", "k-analyst": "analyst", "k-owner": "owner"}
PORTAL = str(REPO_ROOT / "portal" / "app.py")


@pytest.fixture
def small_data(tmp_path: Path) -> Path:
    """Two PLT-01 months and the masters, so uploads never touch the real data/ tree."""
    if not (DATA / "sources" / "plt01").is_dir():
        pytest.skip("data/ not generated (run `make synth`)")
    root = tmp_path / "data"
    shutil.copytree(DATA / "master", root / "master")
    (root / "sources" / "plt01").mkdir(parents=True)
    for f in sorted((DATA / "sources" / "plt01").glob("*.csv"))[:2]:
        shutil.copy(f, root / "sources" / "plt01" / f.name)
    return root


@pytest.fixture
def client(pg_url: str, small_data: Path) -> Iterator[TestClient]:
    migrate(pg_url)
    settings = Settings(database_url=pg_url, llm_provider="none", api_keys=KEYS, data_dir=small_data)
    app.dependency_overrides[get_settings] = lambda: settings
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def _as(role: str) -> dict[str, str]:
    return {"X-API-Key": f"k-{role}"}


@pytest.mark.postgres
def test_upload_creates_a_new_source_that_can_be_profiled_mapped_and_ingested(
        client: TestClient, small_data: Path) -> None:
    body = (small_data / "sources" / "plt01").glob("*.csv").__next__().read_bytes()
    up = client.post("/sources/plt09/files", params={"name": "plt09_orderlines_2025-03.csv"}, content=body,
                     headers={**_as("analyst"), "Content-Type": "text/csv"})
    assert up.status_code == 201, up.text
    assert up.json()["columns"][:2] == ["ord_no", "ln"] and not up.json()["already_present"]
    assert (small_data / "uploads" / "plt09" / "plt09_orderlines_2025-03.csv").read_bytes() == body

    listed = {s["source"]: s for s in client.get("/sources", headers=_as("viewer")).json()}
    assert listed["plt09"]["uploaded_files"] == 1 and listed["plt01"]["files"] == 2
    assert client.get("/sources/plt09/profile", headers=_as("viewer")).json()["rows"] > 0

    proposal = client.post("/sources/plt09/mappings/propose", headers=_as("analyst")).json()["versions"][0]
    confirmed = client.post(f"/mappings/{proposal['mapping_version_id']}/confirm", headers=_as("owner"))
    assert confirmed.status_code == 200
    ingested = client.post("/ingest/plt09", headers={**_as("analyst"), "Idempotency-Key": "test-upload-0001"})
    batches = ingested.json()["files"]
    assert [b["status"] for b in batches] == ["validated"] and batches[0]["silver_rows"] == batches[0]["rows"]
    assert client.get("/sources/plt09/batches", headers=_as("viewer")).json()[0]["status"] == "validated"


@pytest.mark.postgres
def test_upload_rules(client: TestClient) -> None:
    csv_headers = {**_as("analyst"), "Content-Type": "text/csv"}
    ok = b"a,b\n1,2\n"
    assert client.post("/sources/plt09/files", params={"name": "x.csv"}, content=ok, headers=csv_headers)\
        .status_code == 201
    again = client.post("/sources/plt09/files", params={"name": "x.csv"}, content=ok, headers=csv_headers)
    assert again.status_code == 201 and again.json()["already_present"]
    assert client.post("/sources/plt09/files", params={"name": "x.csv"}, content=b"a,b\n3,4\n",
                       headers=csv_headers).status_code == 409
    bad_uploads = [("../escape.csv", ok), ("x.txt", ok), ("y.csv", b"a,a\n1,2\n"), ("z.csv", b"\xff\xfe")]
    for name, content in bad_uploads:
        assert client.post("/sources/plt09/files", params={"name": name}, content=content,
                           headers=csv_headers).status_code == 422, name
    assert client.post("/sources/Bad Name/files", params={"name": "x.csv"}, content=ok,
                       headers=csv_headers).status_code in (404, 422)
    assert client.post("/sources/plt09/files", params={"name": "v.csv"}, content=ok,
                       headers={**_as("viewer"), "Content-Type": "text/csv"}).status_code == 403


@pytest.mark.postgres
def test_portal_client_surfaces_server_side_permission_errors(client: TestClient) -> None:
    http = cast(httpx.Client, client)  # TestClient is an httpx client bound to the ASGI app
    analyst = Api("http://testserver", "k-analyst", client=http)
    owner = Api("http://testserver", "k-owner", client=http)
    version = analyst.propose("plt01").data["versions"][0]
    refused = analyst.decide(version["mapping_version_id"], "confirm")
    assert not refused.ok and refused.status == 403 and "requires owner" in (refused.error or "")
    assert owner.decide(version["mapping_version_id"], "confirm").ok
    assert not Api("http://127.0.0.1:9", "k").sources().ok  # unreachable API → error reply, not an exception


def test_portal_asks_for_a_key_before_calling_the_api() -> None:
    at = AppTest.from_file(PORTAL, default_timeout=60).run()
    assert not at.exception
    assert any("Enter an API key" in i.value for i in at.info)


def test_portal_reports_an_unreachable_api() -> None:
    at = AppTest.from_file(PORTAL, default_timeout=60)
    at.run()
    at.sidebar.text_input[0].set_value("http://127.0.0.1:9")
    at.sidebar.text_input[1].set_value("k-owner")
    at.run()
    assert not at.exception
    assert any("API unreachable" in e.value for e in at.error)
