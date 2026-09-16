"""Grounded answers over the knowledge base (docs/addendum1.md A14-A18; D-033).

`POST /kb/ask` runs exactly the pipeline the evals score: access gate, precedence-aware retrieval, similarity
floor, one structured model call that must cite the excerpts it was given. The asker's role and plant are
stated in the request and filtered in SQL **before** ranking, so a document above their level never reaches
the model. Answers, refusals and citations come back as they are; nothing is rewritten here.

The caller's API-key role is the permission to use this endpoint; the `role`/`plant` in the body is the
knowledge-base access context being asked about (POL-ACC-001's levels), which is not the same thing: only an
owner may ask as `leadership` or `data_owner`.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import Engine

from app.auth import Principal, require
from app.config import Settings, get_settings
from app.db import get_engine
from llm.client import DbRecorder, build_client
from llm.embeddings import EmbeddingError, embedder_for
from retrieval.access import AccessContext
from retrieval.answer import answer
from retrieval.hybrid import TOP_K, RetrievalError

router = APIRouter(tags=["knowledge"])
AnyRole = Depends(require("viewer", "analyst", "owner"))
PRIVILEGED = ("leadership", "data_owner")  # only an owner may ask as a role that sees restricted material


class AskIn(BaseModel):
    question: str = Field(min_length=3, max_length=1000)
    role: Literal["analyst", "leadership", "plant_user", "data_owner"] = "analyst"
    plant: str | None = Field(default=None, pattern=r"^PLT-\d{2}$")
    k: int = Field(default=TOP_K, ge=1, le=20)


class Excerpt(BaseModel):
    chunk_id: str
    doc_id: str
    status: str
    notes: list[str]
    cited: bool


class AskOut(BaseModel):
    question: str
    asked_as: dict[str, str | None]
    outcome: str
    refused: bool
    answer: str
    citations: list[str]
    cited_documents: list[str]
    excerpts: list[Excerpt]
    top_similarity: float | None


@router.post("/kb/ask", response_model=AskOut)
def ask(body: AskIn, principal: Annotated[Principal, AnyRole],
        settings: Annotated[Settings, Depends(get_settings)],
        engine: Annotated[Engine, Depends(get_engine)]) -> AskOut:
    if body.role in PRIVILEGED and principal.role != "owner":
        raise HTTPException(status.HTTP_403_FORBIDDEN,
                            f"only an owner may ask as '{body.role}'; ask as analyst or plant_user")
    try:
        access = AccessContext(body.role, body.plant)
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
    recorder = DbRecorder(engine)
    llm = build_client(settings, recorder)
    if llm is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "answering needs LLM_PROVIDER configured")
    try:
        embedder = embedder_for(settings, recorder)
        if embedder is None:
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE,
                                "answering needs EMBEDDING_PROVIDER configured")
        result = answer(engine, body.question, access, embedder, llm, body.k)
    except EmbeddingError as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc
    except RetrievalError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, f"{exc} (run `make index`)") from exc
    cited = set(result.citations)
    return AskOut(
        question=body.question, asked_as={"role": body.role, "plant": body.plant}, outcome=result.outcome,
        refused=result.refused, answer=result.text, citations=result.citations,
        cited_documents=result.cited_docs, top_similarity=result.top_similarity,
        excerpts=[Excerpt(chunk_id=h.chunk.chunk_id, doc_id=h.chunk.doc_id, status=h.chunk.status,
                          notes=list(h.notes), cited=h.chunk.chunk_id in cited) for h in result.retrieved])


class SourceOut(BaseModel):
    documents: list[dict[str, Any]]
    prior_resolutions: int


@router.get("/kb/documents", response_model=SourceOut)
def documents(_: Annotated[Principal, AnyRole], engine: Annotated[Engine, Depends(get_engine)]) -> SourceOut:
    """What the knowledge base holds, for the portal's picker. Titles only: no document text."""
    from sqlalchemy import text

    with engine.connect() as conn:
        rows = conn.execute(text(
            "SELECT doc_id, min(title) AS title, min(doc_type) AS doc_type, min(version) AS version, "
            "min(status) AS status, min(access_level) AS access_level, min(plant) AS plant, "
            "count(*) AS chunks "
            "FROM retrieval.chunks WHERE doc_type <> 'prior_resolution' GROUP BY doc_id ORDER BY doc_id")
        ).mappings().all()
        resolutions = conn.execute(text(
            "SELECT count(*) FROM retrieval.chunks WHERE doc_type = 'prior_resolution'")).scalar_one()
    return SourceOut(documents=[dict(r) for r in rows], prior_resolutions=int(resolutions))
