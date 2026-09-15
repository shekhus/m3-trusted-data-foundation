"""Version and status precedence on top of hybrid retrieval (docs/addendum1.md A17; D-028).

- **Superseded** documents are excluded by default (C1: SOP-DQ-001 v1 must not be retrieved beside v2). A
  question that names a version ("version 1.0", "v1") opts back in, and the superseded chunk is then labelled
  with what replaced it.
- **Expired** documents stay retrievable but are always labelled as describing a past period only (C4: an
  expired memo is the right source for "how were weights captured in August 2025?", and the reason a
  suppression must not be applied today). Exclusion would make both golden questions unanswerable.
- **Declared precedence** (retrieval/precedence.yaml, C2): when a retrieved chunk of either document touches
  the overlap topic, the best-ranked overlapping chunk of the other document is brought in too, the winner
  labelled as prevailing and the loser as overridden, so an answer can state which applies and why.
  Only chunks the asker is permitted to see are ever added.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import Engine

from llm.embeddings import Embedder
from retrieval.access import AccessContext
from retrieval.chunker import Chunk
from retrieval.hybrid import TOP_K, Hit, Mode, rank

PRECEDENCE_FILE = Path(__file__).resolve().parent / "precedence.yaml"
VERSION_MENTION = re.compile(r"\b(?:version|v)\s*\d+(?:\.\d+)?\b", re.I)
DEFAULT_STATUSES = ("current", "expired")
ALL_STATUSES = ("current", "expired", "superseded")


class PrecedenceRule(BaseModel):
    model_config = ConfigDict(extra="forbid")

    winner: str
    loser: str
    topic: str
    evidence: str
    loser_named_as: str
    terms: list[str] = Field(min_length=1)

    def touches(self, text: str) -> bool:
        lowered = text.lower()
        return any(t.lower() in lowered for t in self.terms)


class PrecedenceFile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rules: list[PrecedenceRule]


def load_rules(path: Path = PRECEDENCE_FILE) -> list[PrecedenceRule]:
    return PrecedenceFile.model_validate(yaml.safe_load(path.read_text(encoding="utf-8"))).rules


@dataclass(frozen=True)
class Retrieved:
    hits: list[Hit]
    top_similarity: float | None
    include_superseded: bool


def wants_history(query: str) -> bool:
    """A question about a specific version opts back into superseded documents."""
    return bool(VERSION_MENTION.search(query))


def _status_note(hit: Hit) -> tuple[str, ...]:
    c = hit.chunk
    if c.status == "superseded":
        return (f"SUPERSEDED by {c.superseded_by}: kept for history only; not in force.",)
    if c.status == "expired":
        return (
            f"EXPIRED: effective from {c.effective_date} and no longer in force; it describes that past "
            "period only and must not be applied to later periods.",
        )
    return ()


def retrieve(
    engine: Engine,
    query: str,
    access: AccessContext,
    embedder: Embedder | None,
    k: int = TOP_K,
    mode: Mode = "hybrid",
    rules: list[PrecedenceRule] | None = None,
    keep: Callable[[Chunk], bool] | None = None,
) -> Retrieved:
    """`keep` narrows the ranked candidates before the top k are chosen (e.g. documents only, for the agent's
    knowledge-base tool, so prior resolutions cannot crowd documents out)."""
    history = wants_history(query)
    ranking = rank(engine, query, access, mode, embedder, ALL_STATUSES if history else DEFAULT_STATUSES)
    ordered = [h for h in ranking.hits if keep is None or keep(h.chunk)]
    chosen = ordered[:k]
    notes: dict[str, list[str]] = {h.chunk.chunk_id: list(_status_note(h)) for h in ordered}

    for rule in load_rules() if rules is None else rules:
        in_top = {h.chunk.doc_id for h in chosen if rule.touches(h.chunk.text)}
        if not in_top & {rule.winner, rule.loser}:
            continue
        for doc in (rule.winner, rule.loser):
            if not any(h.chunk.doc_id == doc and rule.touches(h.chunk.text) for h in chosen):
                extra = next(
                    (h for h in ordered if h.chunk.doc_id == doc and rule.touches(h.chunk.text)), None
                )
                if extra is None:  # not permitted for this asker, or excluded by status
                    continue
                drop = next(
                    (h for h in reversed(chosen) if h.chunk.doc_id not in (rule.winner, rule.loser)), None
                )
                chosen = [h for h in chosen if h is not drop] + [extra]
        for h in chosen:
            if h.chunk.doc_id == rule.winner and rule.touches(h.chunk.text):
                notes[h.chunk.chunk_id].append(f"TAKES PRECEDENCE over {rule.loser} on {rule.topic}.")
            if h.chunk.doc_id == rule.loser and rule.touches(h.chunk.text):
                notes[h.chunk.chunk_id].append(
                    f"OVERRIDDEN by {rule.winner} on {rule.topic}: "
                    f"where they disagree, {rule.winner} applies."
                )
    return Retrieved(
        [replace(h, notes=tuple(notes[h.chunk.chunk_id])) for h in chosen], ranking.top_similarity, history
    )
