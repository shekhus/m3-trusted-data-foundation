"""docs/plan.md §2.6 failure cases that touch mapping:

- "Analyst tries to confirm a mapping" → 403; only owner can confirm.
- "LLM returns invalid JSON / hallucinated column" → the contract rejects it (the heuristic fallback that
  follows is exercised with the LLM client in task 7).
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.config import REPO_ROOT, Settings, get_settings
from app.main import app
from db.migrate import migrate
from pipeline.mapper.contract import MappingProposal

DATA = REPO_ROOT / "data"
KEYS = {"k-viewer": "viewer", "k-analyst": "analyst", "k-owner": "owner"}


@pytest.fixture
def client(pg_url: str) -> Iterator[TestClient]:
    if not (DATA / "sources").is_dir():
        pytest.skip("data/ not generated (run `make synth`)")
    migrate(pg_url)
    settings = Settings(database_url=pg_url, api_keys=KEYS, data_dir=DATA)
    app.dependency_overrides[get_settings] = lambda: settings
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


@pytest.mark.postgres
@pytest.mark.parametrize("key", ["k-analyst", "k-viewer"])
def test_only_owner_can_confirm_or_reject(client: TestClient, key: str) -> None:
    proposal = client.post("/sources/plt01/mappings/propose", headers={"X-API-Key": "k-analyst"}).json()[0]
    for action in ("confirm", "reject"):
        url = f"/mappings/{proposal['mapping_version_id']}/{action}"
        response = client.post(url, headers={"X-API-Key": key})
        assert response.status_code == 403, action
    listed = client.get("/sources/plt01/mappings", headers={"X-API-Key": key}).json()
    assert {v["status"] for v in listed} == {"proposed"}  # nothing changed


@pytest.mark.postgres
def test_viewer_cannot_propose(client: TestClient) -> None:
    response = client.post("/sources/plt01/mappings/propose", headers={"X-API-Key": "k-viewer"})
    assert response.status_code == 403


def test_llm_style_answer_with_hallucinated_column_is_rejected() -> None:
    answer = {
        "source": "plt01", "header": ["ord_no", "ln"], "proposed_by": "llm",
        "columns": [
            {"source_col": "ord_no", "canonical_col": "order_no", "confidence": 0.99, "rationale": "name"},
            {"source_col": "line_number", "canonical_col": "line_no", "confidence": 0.9,
             "rationale": "invented"},
        ],
    }
    with pytest.raises(ValidationError, match="not in header \\['line_number'\\]"):
        MappingProposal.model_validate(answer)
