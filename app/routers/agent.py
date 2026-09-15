"""Exception resolution agent API (docs/addendum1.md A13): start a run (analyst/owner), read it (any
role), and decide on a paused run (owner only). A run always stops before apply; only the owner's decision
resumes it.
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import Engine, text

from agent.graph import AgentDeps, DecisionError, RunView, decide, load_run, start_run
from app.auth import Principal, require
from app.config import Settings, get_settings
from app.db import get_engine
from llm.client import DbRecorder, build_client
from llm.embeddings import EmbeddingError, embedder_for

router = APIRouter(tags=["agent"])
AnyRole = Depends(require("viewer", "analyst", "owner"))
Worker = Depends(require("analyst", "owner"))
Owner = Depends(require("owner"))


class RunOut(BaseModel):
    run_id: str
    exception_id: int
    status: str
    awaiting_approval: bool
    resolution: dict[str, Any] | None
    gate: dict[str, Any] | None
    steps: list[dict[str, Any]]
    fallback: bool
    applied: dict[str, Any] | None


class DecisionIn(BaseModel):
    decision: Literal["approve", "reject"]
    note: str | None = Field(default=None, max_length=2000)


def get_agent_deps(
    settings: Annotated[Settings, Depends(get_settings)], engine: Annotated[Engine, Depends(get_engine)]
) -> AgentDeps:
    recorder = DbRecorder(engine)
    llm = build_client(settings, recorder)
    if llm is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "the agent needs LLM_PROVIDER configured")
    try:
        embedder = embedder_for(settings, recorder)
    except EmbeddingError:
        embedder = None  # the tools fall back to lexical retrieval
    return AgentDeps(engine, llm, settings.data_dir / "master", embedder)


def _out(view: RunView) -> RunOut:
    return RunOut(
        run_id=view.run_id,
        exception_id=view.exception_id,
        status=view.status,
        awaiting_approval=view.next_nodes == ("apply",),
        resolution=view.resolution,
        gate=view.gate,
        steps=view.steps,
        fallback=view.fallback,
        applied=view.applied,
    )


def _run_exists(engine: Engine, run_id: str) -> None:
    try:
        uuid.UUID(run_id)
    except ValueError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "agent run not found") from exc
    with engine.connect() as conn:
        if (
            conn.execute(
                text("SELECT 1 FROM ops.agent_runs WHERE run_id = CAST(:r AS uuid)"), {"r": run_id}
            ).first()
            is None
        ):
            raise HTTPException(status.HTTP_404_NOT_FOUND, "agent run not found")


@router.post(
    "/exceptions/{exception_id}/agent-runs", response_model=RunOut, status_code=status.HTTP_201_CREATED
)
def start(
    exception_id: int,
    principal: Annotated[Principal, Worker],
    settings: Annotated[Settings, Depends(get_settings)],
    deps: Annotated[AgentDeps, Depends(get_agent_deps)],
) -> RunOut:
    with deps.engine.connect() as conn:
        found = conn.execute(
            text("SELECT status FROM ops.exceptions WHERE exception_id = :id"), {"id": exception_id}
        ).first()
        running = conn.execute(
            text(
                "SELECT run_id FROM ops.agent_runs WHERE exception_id = :id "
                "AND status IN ('running', 'awaiting_approval')"
            ),
            {"id": exception_id},
        ).first()
    if found is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "exception not found")
    if found.status == "resolved":
        raise HTTPException(status.HTTP_409_CONFLICT, "exception is already resolved")
    if running is not None:
        raise HTTPException(
            status.HTTP_409_CONFLICT, f"run {running.run_id} is already open for this exception"
        )
    return _out(start_run(deps, settings.database_url, exception_id, principal.role))


@router.get("/agent-runs/{run_id}", response_model=RunOut)
def read(
    run_id: str,
    _: Annotated[Principal, AnyRole],
    settings: Annotated[Settings, Depends(get_settings)],
    deps: Annotated[AgentDeps, Depends(get_agent_deps)],
) -> RunOut:
    _run_exists(deps.engine, run_id)
    return _out(load_run(deps, settings.database_url, run_id))


@router.post("/agent-runs/{run_id}/decision", response_model=RunOut)
def decision(
    run_id: str,
    body: DecisionIn,
    principal: Annotated[Principal, Owner],
    settings: Annotated[Settings, Depends(get_settings)],
    deps: Annotated[AgentDeps, Depends(get_agent_deps)],
) -> RunOut:
    _run_exists(deps.engine, run_id)
    try:
        return _out(decide(deps, settings.database_url, run_id, body.decision, principal.role, body.note))
    except DecisionError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
