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

COUNTS = ("SELECT (SELECT count(*) FROM ops.batches) AS batches, "
          "(SELECT count(*) FROM bronze.raw_order_lines) AS bronze, "
          "(SELECT count(*) FROM silver.order_lines) AS silver, "
          "(SELECT count(*) FROM ops.exceptions) AS exceptions")


@pytest.fixture
def api(pg_url: str, tmp_path: Path) -> Iterator[tuple[TestClient, str, Path]]:
    if not (DATA / "sources" / "plt01").is_dir():
        pytest.skip("data/ not generated (run `make synth`)")
    root = tmp_path / "data"
    shutil.copytree(DATA / "master", root / "master")
    (root / "sources" / "plt01").mkdir(parents=True)
    for f in sorted((DATA / "sources" / "plt01").glob("*.csv"))[:2]:
        shutil.copy(f, root / "sources" / "plt01" / f.name)  # copy, not copy2: fresh mtime, not stale
    migrate(pg_url)
    settings = Settings(database_url=pg_url, llm_provider="none", api_keys=KEYS, data_dir=root,
                        source_sla_hours=72)
    app.dependency_overrides[get_settings] = lambda: settings
    client = TestClient(app)
    proposal = client.post("/sources/plt01/mappings/propose", headers={"X-API-Key": "k-analyst"}).json()
    version_id = proposal["versions"][0]["mapping_version_id"]
    client.post(f"/mappings/{version_id}/confirm", headers={"X-API-Key": "k-owner"})
    try:
        yield client, pg_url, root
    finally:
        app.dependency_overrides.clear()


def _ingest(client: TestClient, key: str, files: list[str] | None = None,
            role: str = "analyst") -> Any:  # TestClient response
    return client.post("/ingest/plt01", headers={"X-API-Key": f"k-{role}", "Idempotency-Key": key},
                       json={"files": files} if files is not None else None)


def _counts(url: str) -> tuple:
    engine = create_engine(url)
    with engine.connect() as conn:
        row = tuple(conn.execute(text(COUNTS)).one())
    engine.dispose()
    return row


def test_same_key_three_times_replays_the_original_and_writes_nothing(
        api: tuple[TestClient, str, Path]) -> None:
    """Plan §2.6 'Duplicate ingest (retry)' and the week-3 replay test: 3× one ingest, counts unchanged."""
    client, url, _ = api
    first = _ingest(client, "replay-test-0001")
    assert first.status_code == 200, first.text
    assert "Idempotent-Replayed" not in first.headers
    body = first.json()
    assert body["validated"] == 2 and body["blocked"] == 0 and body["blocking_exceptions"] > 0
    after_first = _counts(url)
    assert after_first[0] == 2 and after_first[1] == after_first[2] > 1000

    for _ in range(2):
        again = _ingest(client, "replay-test-0001")
        assert again.status_code == 200 and again.headers["Idempotent-Replayed"] == "true"
        assert again.json() == body
        assert _counts(url) == after_first


def test_a_new_key_for_the_same_files_duplicates_nothing(api: tuple[TestClient, str, Path]) -> None:
    client, url, _ = api
    _ingest(client, "first-key-0001")
    before = _counts(url)
    second = _ingest(client, "second-key-0001")
    assert second.status_code == 200 and "Idempotent-Replayed" not in second.headers
    assert all(f["replayed"] for f in second.json()["files"])
    assert _counts(url) == before


def test_key_reused_for_a_different_request_is_refused(api: tuple[TestClient, str, Path]) -> None:
    client, _, root = api
    names = sorted(p.name for p in (root / "sources" / "plt01").glob("*.csv"))
    assert _ingest(client, "reuse-key-0001", files=names[:1]).status_code == 200
    refused = _ingest(client, "reuse-key-0001", files=names)
    assert refused.status_code == 422 and "different request" in refused.text
    assert _ingest(client, "reuse-key-0001", files=names[:1]).headers["Idempotent-Replayed"] == "true"


def test_request_rules(api: tuple[TestClient, str, Path]) -> None:
    client, _, _ = api
    assert client.post("/ingest/plt01", headers={"X-API-Key": "k-analyst"}).status_code == 422  # no key
    assert _ingest(client, "short").status_code == 422
    assert _ingest(client, "bad key with spaces").status_code == 422
    assert _ingest(client, "viewer-key-0001", role="viewer").status_code == 403
    assert client.post("/ingest/nope", headers={"X-API-Key": "k-analyst",
                                                "Idempotency-Key": "unknown-src-01"}).status_code == 404
    unknown_file = _ingest(client, "unknown-file-01", files=["not-there.csv"])
    assert unknown_file.status_code == 422 and "not-there.csv" in unknown_file.text
