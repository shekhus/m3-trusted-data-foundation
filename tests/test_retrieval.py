from __future__ import annotations

import json
import shutil
import uuid
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest
from sqlalchemy import Engine, create_engine, text

from app.config import REPO_ROOT
from db.migrate import migrate
from evals.rag_eval import access_for, load_questions, score
from llm.client import MemoryRecorder
from llm.embeddings import EmbeddingError, HashingEmbedder, VoyageEmbedder
from retrieval.access import AccessContext
from retrieval.hybrid import MODES, RetrievalError, rrf, search
from retrieval.lexical import BM25, TfIdf, tokens
from retrieval.store import candidates, sync

DATA = REPO_ROOT / "data"
KB = DATA / "kb"
needs_kb = pytest.mark.skipif(not (KB / "docs").is_dir(), reason="data/ not generated (run `make synth`)")


# --- access rules (POL-ACC-001) ------------------------------------------------------------


@pytest.mark.parametrize(
    ("access", "all_", "restricted", "own_plant", "other_plant"),
    [
        (AccessContext("analyst"), True, False, False, False),
        (AccessContext("leadership"), True, True, True, True),
        (AccessContext("data_owner"), True, True, False, False),
        (AccessContext("plant_user", "PLT-02"), True, False, True, False),
    ],
)
def test_access_rules(
    access: AccessContext, all_: bool, restricted: bool, own_plant: bool, other_plant: bool
) -> None:
    assert access.permits("all", None) is all_
    assert access.permits("restricted", None) is restricted
    assert access.permits("plant", "PLT-02") is own_plant
    assert access.permits("plant", "PLT-01") is other_plant


def test_access_context_rejects_unknown_roles_and_unassigned_plant_users() -> None:
    with pytest.raises(ValueError, match="unknown role"):
        AccessContext("owner")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="assigned to a plant"):
        AccessContext("plant_user")


# --- rankers --------------------------------------------------------------------------------


def test_rrf_rewards_agreement_between_lists() -> None:
    fused = rrf({"bm25": ["a", "b", "c"], "vector": ["b", "c", "a"]})
    assert [cid for cid, _, _ in fused] == ["b", "a", "c"]
    assert fused[0][2] == {"bm25": 2, "vector": 1}
    assert fused[0][1] == pytest.approx(1 / 62 + 1 / 61)


def test_lexical_rankers_prefer_the_document_with_the_rare_exact_terms() -> None:
    docs = [
        "Exceptions are resolved within 3 business days by the plant.",
        "Standard yield for PRIMALS is 71.5% plus or minus 2.0 pts.",
        "The plant ships daily; exceptions are reviewed weekly by the plant team.",
    ]
    for model in (BM25(docs), TfIdf(docs)):
        scores = model.scores("What is the standard yield for PRIMALS?")
        assert int(scores.argmax()) == 1 and scores[1] > 0
    assert "71.5%" in tokens("yield 71.5% today") and "not" in tokens("not weighted")


# --- Voyage client, against a fake transport ----------------------------------------------


def _voyage(handler: httpx.MockTransport, recorder: MemoryRecorder, dims: int = 3) -> VoyageEmbedder:
    return VoyageEmbedder(
        "pa-test",
        "voyage-4",
        dims,
        recorder,
        price_per_mtok=0.06,
        client=httpx.Client(transport=handler),
        sleep=lambda _: None,
    )


def test_voyage_embedder_sends_the_documented_request_and_records_the_call() -> None:
    seen: list[dict] = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append({"auth": request.headers["Authorization"], **json.loads(request.content)})
        body = json.loads(request.content)
        data = [{"index": i, "embedding": [0.1, 0.2, 0.3]} for i in reversed(range(len(body["input"])))]
        return httpx.Response(200, json={"data": data, "usage": {"total_tokens": 1000}})

    recorder = MemoryRecorder()
    vectors = _voyage(httpx.MockTransport(handle), recorder).embed(["a", "b"], "document")
    assert vectors == [[0.1, 0.2, 0.3], [0.1, 0.2, 0.3]]
    assert seen == [
        {
            "auth": "Bearer pa-test",
            "input": ["a", "b"],
            "model": "voyage-4",
            "input_type": "document",
            "output_dimension": 3,
            "truncation": False,
        }
    ]
    call = recorder.calls[0]
    assert (call.purpose, call.provider, call.outcome, call.input_tokens, call.cost_usd) == (
        "embed_document",
        "voyage",
        "ok",
        1000,
        0.00006,
    )


def test_voyage_embedder_waits_out_a_rate_limit_then_fails_loudly_on_errors() -> None:
    replies = iter(
        [
            httpx.Response(429, text="slow down"),
            httpx.Response(
                200, json={"data": [{"index": 0, "embedding": [1.0, 0.0, 0.0]}], "usage": {"total_tokens": 5}}
            ),
            httpx.Response(401, text="bad key"),
        ]
    )
    recorder = MemoryRecorder()
    embedder = _voyage(httpx.MockTransport(lambda _: next(replies)), recorder)
    assert embedder.embed(["q"], "query") == [[1.0, 0.0, 0.0]]
    with pytest.raises(EmbeddingError, match="401"):
        embedder.embed(["q"], "query")
    assert [c.outcome for c in recorder.calls] == ["error", "ok", "error"]
    with pytest.raises(EmbeddingError, match="VOYAGE_API_KEY"):
        VoyageEmbedder("", "voyage-4", 3, recorder)


def test_voyage_embedder_rejects_vectors_of_the_wrong_size() -> None:
    reply = httpx.Response(
        200, json={"data": [{"index": 0, "embedding": [1.0]}], "usage": {"total_tokens": 1}}
    )
    recorder = MemoryRecorder()
    with pytest.raises(EmbeddingError, match="wrong number or size"):
        _voyage(httpx.MockTransport(lambda _: reply), recorder).embed(["q"], "query")
    assert recorder.calls[0].outcome == "invalid_output"


# --- the index in Postgres (offline hashing embedder; never reported) -----------------------


@pytest.fixture(scope="module")
def kb_db(pg_admin: Engine) -> Iterator[Engine]:
    if not (KB / "docs").is_dir():
        pytest.skip("data/ not generated (run `make synth`)")
    name = f"m3tdf_test_{uuid.uuid4().hex[:12]}"
    with pg_admin.connect() as conn:
        conn.execute(text(f'CREATE DATABASE "{name}"'))
    url = pg_admin.url.set(database=name).render_as_string(hide_password=False)
    engine = create_engine(url)
    try:
        migrate(url)
        sync(engine, KB, HashingEmbedder())
        yield engine
    finally:
        engine.dispose()
        with pg_admin.connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))


@pytest.mark.postgres
def test_sync_embeds_only_what_changed(kb_db: Engine, tmp_path: Path) -> None:
    embedder = HashingEmbedder()
    again = sync(kb_db, KB, embedder)
    assert (again.chunks, again.embedded, again.removed, embedder.calls) == (208, 0, 0, 0)
    kb = tmp_path / "kb"
    shutil.copytree(KB, kb)
    doc = kb / "docs" / "SOP-TR-001.md"
    doc.write_text(
        doc.read_text(encoding="utf-8") + "\n## Added section\n\nNew guidance.\n", encoding="utf-8"
    )
    changed = sync(kb_db, kb, embedder)
    assert (changed.chunks, changed.embedded, embedder.calls) == (209, 1, 1)
    restored = sync(kb_db, KB, embedder)
    assert (restored.chunks, restored.removed, restored.embedded) == (208, 1, 0)
    with kb_db.connect() as conn:
        builds = conn.execute(text("SELECT count(*) FROM retrieval.index_builds")).scalar_one()
    assert builds == 4


@pytest.mark.postgres
@pytest.mark.parametrize(
    "access",
    [
        AccessContext("analyst"),
        AccessContext("leadership"),
        AccessContext("data_owner"),
        AccessContext("plant_user", "PLT-01"),
    ],
)
def test_sql_candidate_filter_matches_the_access_rules_exactly(kb_db: Engine, access: AccessContext) -> None:
    with kb_db.connect() as conn:
        everything = candidates(conn, None)
        permitted = {c.chunk_id for c in candidates(conn, access)}
    assert permitted == {c.chunk_id for c in everything if access.permits(c.access_level, c.plant)}
    assert 0 < len(permitted) <= len(everything) == 208


@pytest.mark.postgres
def test_no_mode_ever_returns_a_chunk_the_asker_may_not_see(kb_db: Engine) -> None:
    embedder = HashingEmbedder()
    for q in load_questions(DATA):
        access = access_for(q)
        for mode in MODES:
            for hit in search(kb_db, q["question"], access, mode, embedder, k=20):
                assert access.permits(hit.chunk.access_level, hit.chunk.plant), (q["question_id"], mode)


@pytest.mark.postgres
def test_vector_modes_need_an_embedder_and_a_complete_index(kb_db: Engine) -> None:
    with pytest.raises(RetrievalError, match="needs an embedder"):
        search(kb_db, "yield", AccessContext("analyst"), "hybrid", None)
    with pytest.raises(RetrievalError, match="no hashing-32 embedding"):
        search(kb_db, "yield", AccessContext("analyst"), "vector", HashingEmbedder(32))


@needs_kb
@pytest.mark.postgres
def test_eval_reports_per_challenge_with_zero_exposures_and_shows_what_the_filter_prevents(
    kb_db: Engine,
) -> None:
    result = score(kb_db, DATA, HashingEmbedder())
    assert result.modes == ["tfidf", "bm25", "vector", "hybrid"]
    assert {c.challenge for c in result.challenges} == {f"C{i}" for i in range(1, 11)}
    assert sum(c.questions for c in result.challenges if c.mode == "tfidf") == 53
    assert all(result.exposures(m) == 0 for m in result.modes)
    assert result.unfiltered_exposures == 4  # every unauthorised question leaks without the filter
    assert result.over_restricted == 0
    c3 = next(c for c in result.challenges if c.challenge == "C3" and c.mode == "tfidf")
    assert c3.doc_recall == 1.0  # the authorised twins still get their restricted/plant documents
