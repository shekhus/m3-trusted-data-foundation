"""Hybrid retrieval (docs/addendum1.md A15): BM25 + embeddings fused by reciprocal rank, TF-IDF as the floor.

Modes: `tfidf` (the lexical floor, reported alongside everything), `bm25`, `vector`, and `hybrid`
(RRF of bm25 and vector). Every mode ranks only the candidates the asker is permitted to see (A16), selected
in SQL before any scoring. Version/status precedence (A17) and the refusal gate (A18) sit on top of this.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Literal

from sqlalchemy import Engine

from llm.embeddings import Embedder
from retrieval.access import AccessContext
from retrieval.chunker import Chunk
from retrieval.lexical import BM25, TfIdf
from retrieval.store import candidates, query_vector, vector_ranking

Mode = Literal["tfidf", "bm25", "vector", "hybrid"]
MODES: tuple[Mode, ...] = ("tfidf", "bm25", "vector", "hybrid")
TOP_K = 6
RRF_K = 60


@dataclass(frozen=True)
class Hit:
    chunk: Chunk
    score: float
    ranks: dict[str, int] = field(default_factory=dict)  # 1-based rank in each contributing list
    notes: tuple[str, ...] = ()  # status and precedence labels shown to the model with the chunk (A17)


@dataclass(frozen=True)
class Ranking:
    """Every permitted candidate, best first, with the best vector similarity (None without an embedder)."""

    hits: list[Hit]
    top_similarity: float | None


class RetrievalError(RuntimeError):
    pass


def rrf(rankings: dict[str, list[str]], k: int = RRF_K) -> list[tuple[str, float, dict[str, int]]]:
    """Reciprocal rank fusion: score(d) = sum over lists of 1 / (k + rank); ties break on id."""
    scores: dict[str, float] = {}
    ranks: dict[str, dict[str, int]] = {}
    for name, ids in rankings.items():
        for rank, chunk_id in enumerate(ids, start=1):
            scores[chunk_id] = scores.get(chunk_id, 0.0) + 1.0 / (k + rank)
            ranks.setdefault(chunk_id, {})[name] = rank
    return sorted(((cid, s, ranks[cid]) for cid, s in scores.items()), key=lambda t: (-t[1], t[0]))


def _lexical(model: BM25 | TfIdf, pool: list[Chunk], query: str) -> list[tuple[str, float]]:
    scores = model.scores(query)
    ranked = sorted(
        ((pool[i].chunk_id, float(s)) for i, s in enumerate(scores) if s > 0), key=lambda t: (-t[1], t[0])
    )
    return ranked


def search(
    engine: Engine,
    query: str,
    access: AccessContext | None,
    mode: Mode = "hybrid",
    embedder: Embedder | None = None,
    k: int = TOP_K,
    statuses: Sequence[str] | None = None,
) -> list[Hit]:
    return rank(engine, query, access, mode, embedder, statuses).hits[:k]


def rank(
    engine: Engine,
    query: str,
    access: AccessContext | None,
    mode: Mode = "hybrid",
    embedder: Embedder | None = None,
    statuses: Sequence[str] | None = None,
) -> Ranking:
    if mode in ("vector", "hybrid") and embedder is None:
        raise RetrievalError(f"mode {mode} needs an embedder (EMBEDDING_PROVIDER=voyage)")
    with engine.connect() as conn:
        pool = candidates(conn, access, statuses)
    by_id = {c.chunk_id: c for c in pool}
    if not pool:
        return Ranking([], None)
    lists: dict[str, list[tuple[str, float]]] = {}
    if mode == "tfidf":
        lists["tfidf"] = _lexical(TfIdf([c.text for c in pool]), pool, query)
    if mode in ("bm25", "hybrid"):
        lists["bm25"] = _lexical(BM25([c.text for c in pool]), pool, query)
    if mode in ("vector", "hybrid"):
        assert embedder is not None
        literal = query_vector(engine, embedder, query)
        with engine.connect() as conn:
            lists["vector"] = vector_ranking(conn, access, embedder, literal, statuses)
        if len(lists["vector"]) < len(pool):
            raise RetrievalError(
                f"{len(pool) - len(lists['vector'])} permitted chunks have no {embedder.model} "
                "embedding; run `make index`"
            )
    top = lists["vector"][0][1] if lists.get("vector") else None
    if mode == "hybrid":
        fused = rrf({name: [cid for cid, _ in ranked] for name, ranked in lists.items()})
        return Ranking([Hit(by_id[cid], score, r) for cid, score, r in fused], top)
    ((name, ranked),) = lists.items()
    return Ranking([Hit(by_id[cid], score, {name: i}) for i, (cid, score) in enumerate(ranked, start=1)], top)
