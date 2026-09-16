"""POST /kb/ask: the evaluated answer pipeline behind an endpoint, with roles enforced twice (D-033).

The API key says who may call; the body's role/plant is the knowledge-base access context. A scripted model
stands in for the provider, so these tests make no API calls.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text

from app.config import REPO_ROOT, Settings, get_settings
from app.main import app
from llm.client import Completion, LLMClient, MemoryRecorder
from llm.embeddings import HashingEmbedder

KEYS = {"k-viewer": "viewer", "k-analyst": "analyst", "k-owner": "owner"}
DATA = REPO_ROOT / "data"


class ScriptedModel:
    provider, model = "scripted", "scripted-kb"

    def __init__(self) -> None:
        self.prompts: list[dict[str, Any]] = []

    def complete(self, system: str, user: str, schema: dict, max_tokens: int) -> Completion:
        payload = json.loads(user)
        self.prompts.append(payload)
        first = payload["excerpts"][0]["chunk_id"]
        return Completion(
            json.dumps(
                {
                    "status": "answered",
                    "answer": "Three business days for blocking rules.",
                    "citations": [first],
                }
            ),
            100,
            20,
            "stop",
        )


@pytest.fixture
def api(kb_db: Engine, monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[TestClient, ScriptedModel]]:
    model = ScriptedModel()
    settings = Settings(
        database_url=kb_db.url.render_as_string(hide_password=False),
        llm_provider="groq",
        data_dir=DATA,
        api_keys=KEYS,
    )
    app.dependency_overrides[get_settings] = lambda: settings
    from app.routers import knowledge

    monkeypatch.setattr(knowledge, "build_client", lambda s, r: LLMClient(model, MemoryRecorder(), 0, 0))
    monkeypatch.setattr(knowledge, "embedder_for", lambda s, r: HashingEmbedder())
    try:
        yield TestClient(app), model
    finally:
        app.dependency_overrides.clear()


@pytest.mark.postgres
def test_ask_answers_with_citations_and_shows_the_excerpts_it_used(
    api: tuple[TestClient, ScriptedModel],
) -> None:
    client, model = api
    body = client.post(
        "/kb/ask",
        json={"question": "How long do I have to resolve a blocking exception?"},
        headers={"X-API-Key": "k-analyst"},
    ).json()
    assert body["outcome"] == "answered" and not body["refused"]
    assert body["citations"] and body["cited_documents"]
    assert any(e["cited"] for e in body["excerpts"])
    assert body["asked_as"] == {"role": "analyst", "plant": None}
    assert all(e["status"] != "superseded" for e in body["excerpts"])  # v1 is out unless a version is named
    prompted = {e["doc_id"] for e in model.prompts[0]["excerpts"]}
    assert not prompted & {"SLA-C000031", "SLA-C000002", "POL-ACC-001"}  # restricted never reaches the model


@pytest.mark.postgres
def test_restricted_questions_are_refused_without_showing_anything(
    api: tuple[TestClient, ScriptedModel], kb_db: Engine
) -> None:
    client, model = api
    with kb_db.connect() as conn:  # the restricted text itself: the nearest chunk is certainly that document
        sla = conn.execute(
            text(
                "SELECT text FROM retrieval.chunks WHERE doc_id = 'SLA-C000031' ORDER BY chunk_id "
                "LIMIT 1 OFFSET 1"
            )
        ).scalar_one()
    with_text = client.post(
        "/kb/ask",
        json={"question": sla[:900], "role": "plant_user", "plant": "PLT-01"},
        headers={"X-API-Key": "k-owner"},
    ).json()
    assert with_text["refused"] and with_text["outcome"] == "refused_access"
    assert "not available at your access level" in with_text["answer"]
    assert with_text["excerpts"] == [] and with_text["citations"] == []
    assert model.prompts == []  # the model was never called


@pytest.mark.postgres
def test_roles_are_enforced_on_the_key_and_on_the_asked_context(
    api: tuple[TestClient, ScriptedModel],
) -> None:
    client, _ = api
    question = {"question": "What are our data classification levels?", "role": "leadership"}
    assert client.post("/kb/ask", json=question).status_code == 401
    assert client.post("/kb/ask", json=question, headers={"X-API-Key": "k-analyst"}).status_code == 403
    assert client.post("/kb/ask", json=question, headers={"X-API-Key": "k-owner"}).status_code == 200
    assert (
        client.post(
            "/kb/ask", json={"question": "x", "role": "analyst"}, headers={"X-API-Key": "k-analyst"}
        ).status_code
        == 422
    )  # question too short
    assert (
        client.post(
            "/kb/ask",
            json={"question": "who ships on Sundays?", "role": "plant_user"},
            headers={"X-API-Key": "k-analyst"},
        ).status_code
        == 422
    )  # plant_user needs a plant


@pytest.mark.postgres
def test_documents_lists_the_corpus_without_its_text(api: tuple[TestClient, ScriptedModel]) -> None:
    client, _ = api
    body = client.get("/kb/documents", headers={"X-API-Key": "k-viewer"}).json()
    assert body["prior_resolutions"] == 93 and len(body["documents"]) == 26
    assert {"doc_id", "title", "doc_type", "version", "status", "access_level", "chunks"} <= set(
        body["documents"][0]
    )
    assert not any("text" in d for d in body["documents"])


@pytest.mark.postgres
def test_ask_needs_a_configured_provider(kb_db: Engine) -> None:
    settings = Settings(
        database_url=kb_db.url.render_as_string(hide_password=False),
        llm_provider="none",
        data_dir=DATA,
        api_keys=KEYS,
    )
    app.dependency_overrides[get_settings] = lambda: settings
    try:
        response = TestClient(app).post(
            "/kb/ask", json={"question": "anything at all"}, headers={"X-API-Key": "k-analyst"}
        )
        assert response.status_code == 503 and "LLM_PROVIDER" in response.json()["detail"]
    finally:
        app.dependency_overrides.clear()
