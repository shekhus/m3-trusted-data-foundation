"""docs/plan.md §2.6 failure cases for ingest:

- Source unreachable → 503 with Retry-After; batch marked failed; nothing partial; the same key may retry.
- Stale source (file older than SLA) → batch accepted but flagged stale.
- A request still running under the same key → 409, not a second run.
"""

from __future__ import annotations

import os
import shutil
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

from app.config import REPO_ROOT, Settings, get_settings
from app.main import app
from app.routers.ingest import get_adapter
from db.migrate import migrate
from pipeline.idempotency import request_hash
from sources import Extract, ExtractRef, FileSystemAdapter, SourceUnavailable

DATA = REPO_ROOT / "data"
KEYS = {"k-analyst": "analyst", "k-owner": "owner"}
pytestmark = pytest.mark.postgres


class FlakyAdapter(FileSystemAdapter):
    """Lists the source fine, then fails to read the second file until `healthy` is set."""

    healthy = False

    def fetch(self, ref: ExtractRef) -> Extract:
        if not self.healthy and ref.name.endswith("2025-04.csv"):
            raise SourceUnavailable("connection reset by the plant file server", retry_after_seconds=120)
        return super().fetch(ref)


@pytest.fixture
def env(pg_url: str, tmp_path: Path) -> Iterator[tuple[TestClient, str, Path, FlakyAdapter]]:
    if not (DATA / "sources" / "plt01").is_dir():
        pytest.skip("data/ not generated (run `make synth`)")
    root = tmp_path / "data"
    shutil.copytree(DATA / "master", root / "master")
    (root / "sources" / "plt01").mkdir(parents=True)
    for f in sorted((DATA / "sources" / "plt01").glob("*.csv"))[:2]:
        shutil.copy(f, root / "sources" / "plt01" / f.name)
    migrate(pg_url)
    settings = Settings(database_url=pg_url, llm_provider="none", api_keys=KEYS, data_dir=root,
                        source_sla_hours=72)
    adapter = FlakyAdapter([root / "sources"])
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_adapter] = lambda: adapter
    client = TestClient(app)
    proposal = client.post("/sources/plt01/mappings/propose", headers={"X-API-Key": "k-analyst"}).json()
    version_id = proposal["versions"][0]["mapping_version_id"]
    client.post(f"/mappings/{version_id}/confirm", headers={"X-API-Key": "k-owner"})
    try:
        yield client, pg_url, root, adapter
    finally:
        app.dependency_overrides.clear()


def _ingest(client: TestClient, key: str) -> Any:  # TestClient response
    return client.post("/ingest/plt01", headers={"X-API-Key": "k-analyst", "Idempotency-Key": key})


def _query(url: str, sql: str) -> list:
    engine = create_engine(url)
    with engine.connect() as conn:
        rows = list(conn.execute(text(sql)).all())
    engine.dispose()
    return rows


def test_unreachable_source_returns_503_marks_failed_and_the_same_key_retries(
        env: tuple[TestClient, str, Path, FlakyAdapter]) -> None:
    client, url, _, adapter = env
    down = _ingest(client, "flaky-source-0001")
    assert down.status_code == 503 and down.headers["Retry-After"] == "120"
    failed = _query(url, "SELECT file_name, error FROM ops.batches WHERE status = 'failed'")
    assert [(r.file_name, "connection reset" in r.error) for r in failed] == [
        ("plt-01_orderlines_2025-04.csv", True)]
    request = _query(url, "SELECT status, attempts, error FROM ops.ingest_requests")[0]
    assert (request.status, request.attempts) == ("failed", 1) and "unavailable" in request.error

    adapter.healthy = True
    retry = _ingest(client, "flaky-source-0001")
    assert retry.status_code == 200 and "Idempotent-Replayed" not in retry.headers
    assert retry.json()["attempts"] == 2 and retry.json()["validated"] == 2
    # the first file was ingested on the failed attempt; the retry replays it instead of loading it twice
    assert [f["replayed"] for f in retry.json()["files"]] == [True, False]
    assert _query(url, "SELECT count(*) FROM ops.batches WHERE status = 'validated'")[0][0] == 2
    assert _query(url, "SELECT status FROM ops.ingest_requests")[0].status == "completed"


def test_stale_extract_is_accepted_but_flagged(env: tuple[TestClient, str, Path, FlakyAdapter]) -> None:
    client, url, root, adapter = env
    adapter.healthy = True
    old = sorted((root / "sources" / "plt01").glob("*.csv"))[0]
    five_days_ago = time.time() - 5 * 24 * 3600
    os.utime(old, (five_days_ago, five_days_ago))
    body = _ingest(client, "stale-extract-0001").json()
    assert body["stale"] == [old.name] and body["validated"] == 2
    flags = dict(_query(url, "SELECT file_name, stale FROM ops.batches"))
    assert flags == {old.name: True, "plt-01_orderlines_2025-04.csv": False}


def test_a_key_still_in_progress_is_not_run_twice(env: tuple[TestClient, str, Path, FlakyAdapter]) -> None:
    client, url, _, adapter = env
    adapter.healthy = True
    engine = create_engine(url)
    with engine.begin() as conn:  # another worker holds this key and is mid-run
        conn.execute(text("INSERT INTO ops.ingest_requests (idempotency_key, source, request_hash, "
                          "requested_by, status) VALUES ('busy-key-0001', 'plt01', :h, 'analyst', "
                          "'in_progress')"),
                     {"h": request_hash({"source": "plt01", "files": None})})
    engine.dispose()
    busy = _ingest(client, "busy-key-0001")
    assert busy.status_code == 409 and busy.headers["Retry-After"] == "5"
    assert _query(url, "SELECT count(*) FROM ops.batches")[0][0] == 0
