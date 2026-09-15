from __future__ import annotations

import json
import re

import pytest
from sqlalchemy import Engine, text

from app.config import REPO_ROOT
from evals.answer_eval import verdict_for
from llm.client import Completion, LLMClient, MemoryRecorder
from llm.embeddings import HashingEmbedder
from retrieval.access import AccessContext
from retrieval.answer import answer
from retrieval.chunker import parse_document
from retrieval.precedence import load_rules, retrieve, wants_history
from retrieval.refusal import ACCESS_MESSAGE, SIMILARITY_FLOOR, access_gate, floor_gate

DOCS = REPO_ROOT / "data" / "kb" / "docs"
needs_kb = pytest.mark.skipif(not DOCS.is_dir(), reason="data/ not generated (run `make synth`)")
ANALYST = AccessContext("analyst")


# --- precedence registry is true to the corpus ---------------------------------------------


@needs_kb
def test_every_precedence_rule_is_stated_by_the_winning_document() -> None:
    rules = load_rules()
    assert rules
    for rule in rules:
        _, winner = parse_document(DOCS / f"{rule.winner}.md")
        parse_document(DOCS / f"{rule.loser}.md")  # the loser exists
        collapsed = re.sub(r"\s+", " ", winner.replace("*", "")).lower()
        assert rule.evidence.lower() in collapsed, rule.winner
        assert rule.loser_named_as in winner, rule.winner
        assert all(rule.touches(t) for t in rule.terms)


@pytest.mark.parametrize(
    ("query", "history"),
    [
        ("Is SOP-DQ-001 version 1.0 still in force?", True),
        ("What changed in v2?", True),
        ("How long do I have to resolve a blocking exception?", False),
        ("Which vendor supplies 3 lines?", False),
    ],
)
def test_only_a_question_naming_a_version_opts_into_superseded_documents(query: str, history: bool) -> None:
    assert wants_history(query) is history


# --- precedence-aware retrieval (lexical, offline) -----------------------------------------


@pytest.mark.postgres
def test_superseded_versions_are_excluded_unless_the_question_names_a_version(kb_db: Engine) -> None:
    default = retrieve(
        kb_db, "How long do I have to resolve a blocking data quality exception?", ANALYST, None, mode="bm25"
    )
    assert "SOP-DQ-001-v2" in {h.chunk.doc_id for h in default.hits}
    assert all(h.chunk.status != "superseded" for h in default.hits)
    versioned = retrieve(kb_db, "Is SOP-DQ-001 version 1.0 still in force?", ANALYST, None, mode="bm25")
    old = [h for h in versioned.hits if h.chunk.doc_id == "SOP-DQ-001-v1"]
    assert versioned.include_superseded and old
    assert old[0].notes[0].startswith("SUPERSEDED by SOP-DQ-001-v2")


@pytest.mark.postgres
def test_expired_documents_stay_retrievable_and_are_labelled(kb_db: Engine) -> None:
    hits = retrieve(
        kb_db, "How were Hastings weights captured manually in August 2025?", ANALYST, None, mode="bm25"
    ).hits
    memo = [h for h in hits if h.chunk.doc_id == "MEMO-2025-07"]
    assert memo and all(h.notes and h.notes[0].startswith("EXPIRED") for h in memo)


@pytest.mark.postgres
def test_declared_precedence_brings_both_sources_and_labels_the_winner(kb_db: Engine) -> None:
    hits = retrieve(
        kb_db,
        "What unit are the WT_SHIP_KG weight fields in the Hastings extract?",
        ANALYST,
        None,
        k=4,
        mode="bm25",
    ).hits
    by_doc = {h.chunk.doc_id: h for h in hits}
    assert {"MEMO-2025-11", "SRC-PLT02-001"} <= set(by_doc) and len(hits) == 4
    assert any(n.startswith("TAKES PRECEDENCE over SRC-PLT02-001") for n in by_doc["MEMO-2025-11"].notes)
    assert any(n.startswith("OVERRIDDEN by MEMO-2025-11") for n in by_doc["SRC-PLT02-001"].notes)


@pytest.mark.postgres
def test_precedence_never_adds_a_document_the_asker_may_not_see(kb_db: Engine) -> None:
    for role in (AccessContext("analyst"), AccessContext("plant_user", "PLT-01")):
        for h in retrieve(
            kb_db, "Northgate on-time-in-full commitment and Hastings weights", role, None, k=10, mode="bm25"
        ).hits:
            assert role.permits(h.chunk.access_level, h.chunk.plant)


# --- refusal gate --------------------------------------------------------------------------


def test_similarity_floor_only_stops_unrelated_queries() -> None:
    assert (
        floor_gate(SIMILARITY_FLOOR - 0.01).refuse
        and floor_gate(SIMILARITY_FLOOR - 0.01).reason == "below_floor"
    )
    assert not floor_gate(SIMILARITY_FLOOR + 0.01).refuse and not floor_gate(None).refuse


@pytest.mark.postgres
def test_access_gate_says_the_information_exists_without_showing_it(kb_db: Engine) -> None:
    with kb_db.connect() as conn:
        sla = conn.execute(
            text(
                "SELECT text FROM retrieval.chunks WHERE doc_id = 'SLA-C000031' "
                "ORDER BY chunk_id LIMIT 1 OFFSET 1"
            )
        ).scalar_one()
    embedder = HashingEmbedder()
    refused = access_gate(kb_db, embedder, sla, AccessContext("plant_user", "PLT-01"))
    assert (refused.refuse, refused.reason, refused.message) == (True, "access", ACCESS_MESSAGE)
    assert not access_gate(kb_db, embedder, sla, AccessContext("leadership")).refuse


# --- the answer pipeline with a scripted model ---------------------------------------------


class ScriptedBackend:
    provider, model = "scripted", "scripted-1"

    def __init__(self, *replies: dict | str) -> None:
        self.replies = list(replies)
        self.users: list[str] = []

    def complete(self, system: str, user: str, schema: dict, max_tokens: int) -> Completion:
        self.users.append(user)
        reply = self.replies.pop(0)
        body = reply if isinstance(reply, str) else json.dumps(reply)
        return Completion(body, 100, 20, "stop")


def _client(backend: ScriptedBackend) -> tuple[LLMClient, MemoryRecorder]:
    recorder = MemoryRecorder()
    return LLMClient(backend, recorder, 0.0, 0.0), recorder


def _first_chunk(user: str) -> str:
    return json.loads(user)["excerpts"][0]["chunk_id"]


@pytest.mark.postgres
def test_answer_passes_only_permitted_excerpts_and_checks_citations(kb_db: Engine) -> None:
    backend = ScriptedBackend({"status": "answered", "answer": "Plan", "citations": ["NOT-A-CHUNK#0"]})
    llm, recorder = _client(backend)
    # second reply cites a real excerpt from the prompt it was given
    original = backend.complete

    def complete(system: str, user: str, schema: dict, max_tokens: int) -> Completion:
        if not backend.replies:
            backend.replies.append(
                {"status": "answered", "answer": "Three days.", "citations": [_first_chunk(user)]}
            )
        return original(system, user, schema, max_tokens)

    backend.complete = complete  # type: ignore[method-assign]
    result = answer(
        kb_db,
        "How long do I have to resolve a blocking data quality exception?",
        ANALYST,
        HashingEmbedder(),
        llm,
    )
    assert (result.outcome, result.llm_attempts) == ("answered", 2)
    assert [c.outcome for c in recorder.calls] == ["invalid_output", "ok"]
    for user in backend.users:
        for excerpt in json.loads(user)["excerpts"]:
            assert excerpt["doc_id"] not in ("SLA-C000031", "SLA-C000002", "POL-ACC-001")
            assert not excerpt["doc_id"].startswith("PLANT-")
            assert excerpt["status"] != "superseded"


@pytest.mark.postgres
def test_two_invalid_answers_refuse_as_ungrounded_and_access_refusals_skip_the_model(kb_db: Engine) -> None:
    backend = ScriptedBackend("not json", {"status": "answered", "answer": "x", "citations": []})
    llm, recorder = _client(backend)
    result = answer(kb_db, "What is the standard yield for PRIMALS?", ANALYST, HashingEmbedder(), llm)
    assert (result.outcome, result.llm_attempts, len(recorder.calls)) == ("refused_ungrounded", 2, 2)

    with kb_db.connect() as conn:
        policy = conn.execute(
            text(
                "SELECT text FROM retrieval.chunks WHERE doc_id = 'POL-ACC-001' "
                "ORDER BY chunk_id LIMIT 1 OFFSET 1"
            )
        ).scalar_one()
    silent = ScriptedBackend()
    llm, recorder = _client(silent)
    refused = answer(kb_db, policy, AccessContext("analyst"), HashingEmbedder(), llm)
    assert refused.outcome == "refused_access" and refused.text == ACCESS_MESSAGE
    assert silent.users == [] and recorder.calls == []


# --- verdicts ------------------------------------------------------------------------------


def _q(**overrides: object) -> dict:
    return {
        "must_contain": ["3"],
        "must_not_contain": ["10 business days"],
        "should_refuse": False,
        "forbidden_doc_ids": [],
        **overrides,
    }


@pytest.mark.parametrize(
    ("question", "outcome", "text_", "verdict"),
    [
        (_q(), "answered", "Within 3 days.", "correct"),
        (_q(must_contain=["Tier 2"]), "answered", "It is Tier 2.", "correct"),  # narrow no-break space
        (
            _q(must_contain=["on time"]),
            "answered",
            "It is on‑time.",
            "wrong_answer",
        ),  # hyphen stays different
        (_q(), "answered", "Within 10 business days.", "wrong_answer"),
        (_q(), "not_in_context", "Not in the knowledge base.", "over_refusal"),
        (_q(must_contain=[], should_refuse=True), "refused_below_floor", "No.", "correct_refusal"),
        (_q(must_contain=[], should_refuse=True), "answered", "About $4.10.", "answered_unanswerable"),
        (
            _q(must_contain=[], should_refuse=True, forbidden_doc_ids=["SLA-C000031"]),
            "answered",
            "96%",
            "leak",
        ),
    ],
)
def test_verdicts(question: dict, outcome: str, text_: str, verdict: str) -> None:
    assert verdict_for(question, outcome, text_)[0] == verdict
