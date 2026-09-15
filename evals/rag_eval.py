"""Retrieval scored against data/ground_truth/kb_questions.json (docs/addendum1.md §3.2, CLAUDE.md retrieval
conventions): per challenge C1-C10, never as one aggregate, every mode beside the TF-IDF floor.

Per question and mode, over the top-k chunks retrieved for the question's asker:
- doc hit: every expected document is among the retrieved chunks' documents
- content hit: every must_contain term appears in the retrieved text (the evidence an answer would need; this
  also scores the prior-resolution questions, which name no document)
- forbidden: forbidden documents retrieved (near-duplicates, superseded versions, other plants)
- exposure: a should_refuse question retrieved a forbidden document. Reported as an ABSOLUTE COUNT; one is a
  failure. The same questions are also run unfiltered, to show what the pre-model filter prevents.
- over-restricted: an expected document the asker's access context does not permit (must be 0)

Answer correctness and refusal are scored when generation lands (A18).
"""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from pathlib import Path

from sqlalchemy import Engine

from llm.embeddings import Embedder
from retrieval.access import AccessContext
from retrieval.hybrid import MODES, TOP_K, Mode, search

CHALLENGES = [f"C{i}" for i in range(1, 11)]


@dataclass
class QuestionResult:
    question_id: str
    challenge: str
    mode: str
    retrieved_docs: list[str]
    doc_hit: bool | None
    content_hit: bool | None
    forbidden: list[str]
    exposure: bool
    over_restricted: list[str]


@dataclass
class ChallengeScore:
    challenge: str
    mode: str
    questions: int
    doc_scored: int
    doc_hits: int
    content_scored: int
    content_hits: int
    forbidden_retrieved: int
    exposures: int

    @property
    def doc_recall(self) -> float | None:
        return self.doc_hits / self.doc_scored if self.doc_scored else None

    @property
    def content_recall(self) -> float | None:
        return self.content_hits / self.content_scored if self.content_scored else None


@dataclass
class RagScore:
    k: int
    modes: list[str]
    challenges: list[ChallengeScore]
    results: list[QuestionResult]
    unfiltered_exposures: int
    over_restricted: int
    notes: list[str] = field(default_factory=list)

    def exposures(self, mode: str) -> int:
        return sum(c.exposures for c in self.challenges if c.mode == mode)

    def totals(self, mode: str) -> dict[str, float | int | None]:
        rows = [c for c in self.challenges if c.mode == mode]
        doc_scored, content_scored = sum(c.doc_scored for c in rows), sum(c.content_scored for c in rows)
        return {
            "doc_recall": round(sum(c.doc_hits for c in rows) / doc_scored, 4) if doc_scored else None,
            "content_recall": round(sum(c.content_hits for c in rows) / content_scored, 4)
            if content_scored
            else None,
            "forbidden_retrieved": sum(c.forbidden_retrieved for c in rows),
            "exposures": self.exposures(mode),
        }

    def as_dict(self) -> dict:
        return {
            "k": self.k,
            "modes": self.modes,
            "over_restricted": self.over_restricted,
            "unfiltered_exposures": self.unfiltered_exposures,
            "notes": self.notes,
            "totals": {m: self.totals(m) for m in self.modes},
            "challenges": [
                {**asdict(c), "doc_recall": c.doc_recall, "content_recall": c.content_recall}
                for c in self.challenges
            ],
            "results": [asdict(r) for r in self.results],
        }


def load_questions(data_dir: Path) -> list[dict]:
    path = data_dir / "ground_truth" / "kb_questions.json"
    return json.loads(path.read_text(encoding="utf-8"))["questions"]


def access_for(question: dict) -> AccessContext:
    asker = question["asker"]
    return AccessContext(asker["role"], asker.get("plant"))


def _score(question: dict, mode: str, docs: list[str], text: str, over: list[str]) -> QuestionResult:
    expected, forbidden = set(question["expected_doc_ids"]), set(question["forbidden_doc_ids"])
    must = [m.lower() for m in question["must_contain"]]
    retrieved_forbidden = sorted(forbidden & set(docs))
    return QuestionResult(
        question_id=question["question_id"],
        challenge=question["challenge"],
        mode=mode,
        retrieved_docs=docs,
        doc_hit=expected <= set(docs) if expected else None,
        content_hit=all(m in text for m in must) if must and not question["should_refuse"] else None,
        forbidden=retrieved_forbidden,
        exposure=question["should_refuse"] and bool(retrieved_forbidden),
        over_restricted=over,
    )


def score(
    engine: Engine,
    data_dir: Path,
    embedder: Embedder | None,
    k: int = TOP_K,
    modes: tuple[Mode, ...] | None = None,
) -> RagScore:
    questions = load_questions(data_dir)
    modes = modes or tuple(m for m in MODES if embedder is not None or m in ("tfidf", "bm25"))
    doc_access = _document_access(data_dir)
    results: list[QuestionResult] = []
    over_total = 0
    for q in questions:
        access = access_for(q)
        over = sorted(d for d in q["expected_doc_ids"] if not access.permits(*doc_access[d]))
        over_total += len(over)
        for mode in modes:
            hits = search(engine, q["question"], access, mode, embedder, k)
            docs = list(dict.fromkeys(h.chunk.doc_id for h in hits))
            results.append(_score(q, mode, docs, "\n".join(h.chunk.text for h in hits).lower(), over))
    unfiltered = 0
    for q in questions:
        if q["should_refuse"] and q["forbidden_doc_ids"]:
            hits = search(engine, q["question"], None, "tfidf", None, k)
            unfiltered += bool(set(q["forbidden_doc_ids"]) & {h.chunk.doc_id for h in hits})

    grouped: dict[tuple[str, str], list[QuestionResult]] = defaultdict(list)
    for r in results:
        grouped[(r.challenge, r.mode)].append(r)
    challenges = []
    for challenge in CHALLENGES:
        for mode in modes:
            rows = grouped.get((challenge, mode), [])
            challenges.append(
                ChallengeScore(
                    challenge=challenge,
                    mode=mode,
                    questions=len(rows),
                    doc_scored=sum(r.doc_hit is not None for r in rows),
                    doc_hits=sum(bool(r.doc_hit) for r in rows),
                    content_scored=sum(r.content_hit is not None for r in rows),
                    content_hits=sum(bool(r.content_hit) for r in rows),
                    forbidden_retrieved=sum(len(r.forbidden) for r in rows),
                    exposures=sum(r.exposure for r in rows),
                )
            )
    notes = [] if embedder is not None else ["no embedder configured: vector and hybrid modes not run"]
    return RagScore(k, list(modes), challenges, results, unfiltered, over_total, notes)


def _document_access(data_dir: Path) -> dict[str, tuple[str, str | None]]:
    from retrieval.chunker import parse_document

    out: dict[str, tuple[str, str | None]] = {}
    for path in (data_dir / "kb" / "docs").glob("*.md"):
        meta, _ = parse_document(path)
        out[meta.doc_id] = (meta.access_level, meta.plant)
    return out
