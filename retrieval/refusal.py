"""Refusal gate (docs/addendum1.md A18; CLAUDE.md principle 10; D-028).

Three layers, in order; the first two never call the model:

1. **Access.** If the single best match for the question in the whole index is a document the asker may not
   see, the answer is POL-ACC-001's prescribed response: the information exists but is not available at this
   access level. Only the match's access level is read, in SQL; no text of it leaves the database, and the
   model is not called, so nothing restricted can be quoted, summarised or paraphrased.
2. **Similarity floor.** If nothing the asker may see is even loosely related (best cosine similarity below
   SIMILARITY_FLOOR), refuse as not in the knowledge base. The floor is deliberately low: on the golden set
   the weakest answerable question scores below every unanswerable one, so a floor high enough to catch those
   would refuse real questions. It only stops queries with no relation to the corpus.
3. **Instruction.** Everything else goes to the model with an explicit rule: if the answer is not in the
   provided context, say so. Most refusals of unanswerable questions happen here.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from sqlalchemy import Engine, text

from llm.embeddings import Embedder
from retrieval.access import AccessContext
from retrieval.store import query_vector

SIMILARITY_FLOOR = 0.10
ACCESS_MESSAGE = (
    "Information on this exists in the knowledge base, but it is not available at your access "
    "level. Ask a data owner or the analytics leadership team if you need it."
)
NOT_FOUND_MESSAGE = "The knowledge base does not contain an answer to this question."

GateReason = Literal["access", "below_floor"]


@dataclass(frozen=True)
class GateDecision:
    refuse: bool
    reason: GateReason | None = None
    message: str | None = None


def access_gate(engine: Engine, embedder: Embedder, query: str, access: AccessContext) -> GateDecision:
    literal = query_vector(engine, embedder, query)
    with engine.connect() as conn:
        top = conn.execute(
            text(
                "SELECT c.access_level, c.plant FROM retrieval.chunks c JOIN retrieval.embeddings e "
                "ON e.text_sha256 = c.text_sha256 AND e.model = :m AND e.input_type = 'document' "
                "AND e.dimensions = :d WHERE c.status <> 'superseded' "
                "ORDER BY e.embedding <=> CAST(:q AS vector), c.chunk_id LIMIT 1"
            ),
            {"m": embedder.model, "d": embedder.dimensions, "q": literal},
        ).first()
    if top is not None and not access.permits(top.access_level, top.plant):
        return GateDecision(True, "access", ACCESS_MESSAGE)
    return GateDecision(False)


def floor_gate(top_similarity: float | None) -> GateDecision:
    if top_similarity is not None and top_similarity < SIMILARITY_FLOOR:
        return GateDecision(True, "below_floor", NOT_FOUND_MESSAGE)
    return GateDecision(False)
