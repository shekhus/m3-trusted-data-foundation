"""Grounded answers from the knowledge base (docs/addendum1.md A14-A18; D-028).

Fixed sequence, no agent: access gate → precedence-aware retrieval → similarity floor → one structured LLM
call with the refusal instruction → contract check (citations must be retrieved chunk ids; an answer must
cite). An invalid answer is retried once, then the question is refused as ungrounded (CLAUDE.md: never more
than one retry). Only permitted chunks are ever put in the prompt.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import Engine

from llm.client import PROMPTS_DIR, InvalidOutput, LLMClient, LLMError
from llm.embeddings import Embedder
from retrieval.access import AccessContext
from retrieval.hybrid import TOP_K, Hit
from retrieval.precedence import retrieve
from retrieval.refusal import NOT_FOUND_MESSAGE, access_gate, floor_gate

Outcome = Literal["answered", "not_in_context", "refused_access", "refused_below_floor", "refused_ungrounded"]
UNGROUNDED_MESSAGE = "No answer could be grounded in the knowledge base for this question."
SYSTEM = (PROMPTS_DIR / "kb_answer_system.md").read_text(encoding="utf-8")


class AnswerContract(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["answered", "not_in_context"]
    answer: str = Field(min_length=1)
    citations: list[str]


SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["status", "answer", "citations"],
    "properties": {
        "status": {"type": "string", "enum": ["answered", "not_in_context"]},
        "answer": {"type": "string"},
        "citations": {"type": "array", "items": {"type": "string"}},
    },
}


@dataclass(frozen=True)
class Answer:
    outcome: Outcome
    text: str
    citations: list[str] = field(default_factory=list)
    retrieved: list[Hit] = field(default_factory=list)
    top_similarity: float | None = None
    llm_attempts: int = 0

    @property
    def refused(self) -> bool:
        return self.outcome != "answered"

    @property
    def cited_docs(self) -> list[str]:
        by_chunk = {h.chunk.chunk_id: h.chunk.doc_id for h in self.retrieved}
        return list(dict.fromkeys(by_chunk[c] for c in self.citations if c in by_chunk))


def _payload(question: str, hits: list[Hit]) -> str:
    excerpts = [
        {
            "chunk_id": h.chunk.chunk_id,
            "doc_id": h.chunk.doc_id,
            "version": h.chunk.version,
            "status": h.chunk.status,
            "effective_date": h.chunk.effective_date,
            "notes": list(h.notes),
            "text": h.chunk.text,
        }
        for h in hits
    ]
    return json.dumps({"question": question, "excerpts": excerpts}, ensure_ascii=False, indent=1)


def _parser(allowed: set[str]):  # noqa: ANN202 - returns a parse callback for LLMClient.complete_json
    def parse(data: dict) -> AnswerContract:
        result = AnswerContract.model_validate(data)
        unknown = [c for c in result.citations if c not in allowed]
        if unknown:
            raise ValueError(f"citations not among the retrieved excerpts: {unknown}")
        if result.status == "answered" and not result.citations:
            raise ValueError("an answered response must cite at least one excerpt")
        return result

    return parse


def answer(
    engine: Engine, question: str, access: AccessContext, embedder: Embedder, llm: LLMClient, k: int = TOP_K
) -> Answer:
    gate = access_gate(engine, embedder, question, access)
    if gate.refuse:
        return Answer("refused_access", gate.message or "")
    retrieved = retrieve(engine, question, access, embedder, k)
    floor = floor_gate(retrieved.top_similarity)
    if floor.refuse or not retrieved.hits:
        return Answer(
            "refused_below_floor",
            floor.message or NOT_FOUND_MESSAGE,
            [],
            retrieved.hits,
            retrieved.top_similarity,
        )
    parse = _parser({h.chunk.chunk_id for h in retrieved.hits})
    user = _payload(question, retrieved.hits)
    for attempt in (1, 2):
        try:
            result = llm.complete_json("kb_answer", SYSTEM, user, SCHEMA, parse, max_tokens=4000)
        except (InvalidOutput, LLMError):
            if attempt == 2:
                break
            continue
        return Answer(
            result.status, result.answer, result.citations, retrieved.hits, retrieved.top_similarity, attempt
        )
    return Answer("refused_ungrounded", UNGROUNDED_MESSAGE, [], retrieved.hits, retrieved.top_similarity, 2)
