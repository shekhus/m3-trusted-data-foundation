"""The A13 graph with a scripted model: loop cap, evidence check, fallback, the approval interrupt, resume
in a fresh process, and apply writing ops.exceptions only. No real model is called."""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

import pytest
from conftest import Loaded
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text

from agent.graph import (
    CLASSIFY_SYSTEM,
    MAX_TOOL_CALLS,
    AgentDeps,
    DecisionError,
    decide,
    load_run,
    start_run,
)
from app.config import REPO_ROOT, Settings, get_settings
from app.main import app
from app.routers.agent import get_agent_deps
from llm.client import Completion, LLMClient, MemoryRecorder

DATA = REPO_ROOT / "data"


class ScriptedModel:
    """Replies from two queues, by which system prompt is asking. Records every prompt it was sent."""

    provider, model = "scripted", "scripted-agent"

    def __init__(self, steps: list[dict | str], classifications: list[dict | str]) -> None:
        self.steps, self.classifications = list(steps), list(classifications)
        self.prompts: list[tuple[str, str]] = []

    def complete(self, system: str, user: str, schema: dict, max_tokens: int) -> Completion:
        kind = "classify" if system == CLASSIFY_SYSTEM else "investigate"
        self.prompts.append((kind, user))
        queue = self.classifications if kind == "classify" else self.steps
        reply = queue.pop(0) if len(queue) > 1 else queue[0]  # the last reply repeats
        return Completion(reply if isinstance(reply, str) else json.dumps(reply), 50, 20, "stop")


def step(action: str, **arguments: str) -> dict[str, Any]:
    base = {
        a: None
        for a in ("symptom", "rule_id", "query", "value", "master", "source", "row_key", "key", "field")
    }
    return {"thought": "next step", "action": action, **base, **arguments}


def classification(outcome: str, refs: list[tuple[str, str]], fix: dict | None = None, **extra: Any) -> dict:
    """In the model's flat output shape (fix_* fields), as CLASSIFY_SCHEMA asks for."""
    return {
        "outcome": outcome,
        "confidence": 0.8,
        "evidence_refs": [{"kind": k, "ref": r} for k, r in refs],
        "fix_kind": fix["kind"] if fix else None,
        "fix_changes": fix["changes"] if fix else [],
        "fix_retain_original": fix["retain_original"] if fix else False,
        "fix_description": fix.get("description", "") if fix else "",
        "not_covered": False,
        "rationale": "The evidence gathered supports this outcome.",
        **extra,
    }


@pytest.fixture(scope="module")
def db(silver_db: Engine, loaded: Loaded) -> Engine:
    return silver_db


def _resolution(view: Any) -> dict[str, Any]:  # noqa: ANN401 - a RunView
    assert view.resolution is not None
    return view.resolution


def _url(engine: Engine) -> str:
    return engine.url.render_as_string(hide_password=False)


def _deps(engine: Engine, model: ScriptedModel) -> tuple[AgentDeps, MemoryRecorder]:
    recorder = MemoryRecorder()
    return AgentDeps(engine, LLMClient(model, recorder, 0, 0), DATA / "master", None), recorder


def _exception(engine: Engine, rule: str) -> Any:  # noqa: ANN401 - a result row
    with engine.connect() as conn:
        return conn.execute(
            text(
                "SELECT e.exception_id, e.source, e.row_key, e.details, e.status, e.owner "
                "FROM ops.exceptions e "
                "WHERE e.rule_id = :r AND e.status = 'open' AND NOT EXISTS (SELECT 1 FROM ops.agent_runs a "
                "WHERE a.exception_id = e.exception_id) ORDER BY e.exception_id LIMIT 1"
            ),
            {"r": rule},
        ).one()


@pytest.mark.postgres
def test_run_investigates_classifies_and_stops_before_apply_then_a_fresh_process_resumes(db: Engine) -> None:
    e = _exception(db, "V004")
    customer = e.details["customer_no"]
    model = ScriptedModel(
        [step("lookup_master", value=customer, master="customers"), step("classify")],
        [classification("needs_master_data", [("master", f"customers:{customer}"), ("row", e.row_key)])],
    )
    deps, recorder = _deps(db, model)
    paused = start_run(deps, _url(db), e.exception_id, "analyst")
    assert (paused.status, paused.next_nodes) == ("awaiting_approval", ("apply",))
    assert _resolution(paused)["outcome"] == "needs_master_data" and paused.gate == {
        "disposition": "no_fix",
        "reasons": [],
    }
    with db.connect() as conn:
        run = (
            conn.execute(text("SELECT * FROM ops.agent_runs WHERE run_id = :r"), {"r": paused.run_id})
            .mappings()
            .one()
        )
        tools = conn.execute(
            text("SELECT tool, outcome FROM ops.tool_calls WHERE run_id = :r"), {"r": paused.run_id}
        ).all()
        untouched = conn.execute(
            text("SELECT status, owner FROM ops.exceptions WHERE exception_id = :id"), {"id": e.exception_id}
        ).one()
    assert (run["status"], run["iterations"], run["outcome"], run["decision"]) == (
        "awaiting_approval",
        1,
        "needs_master_data",
        None,
    )
    assert [(t.tool, t.outcome) for t in tools] == [("lookup_master", "empty")]
    assert (untouched.status, untouched.owner) == (e.status, e.owner)  # nothing changes before a decision
    assert [c.purpose for c in recorder.calls] == ["agent_investigate", "agent_investigate", "agent_classify"]

    # a new process: new deps, new compiled graph, only the checkpoint in Postgres
    silent = ScriptedModel([step("classify")], [classification("escalate", [("row", e.row_key)])])
    fresh, fresh_calls = _deps(db, silent)
    assert load_run(fresh, _url(db), paused.run_id).next_nodes == ("apply",)
    done = decide(fresh, _url(db), paused.run_id, "approve", "owner", "create the account")
    assert (done.status, done.next_nodes) == ("completed", ())
    assert done.applied == {"decision": "approve", "owner": "master_data:customers", "fix_applicable": False}
    assert fresh_calls.calls == []  # resuming re-runs nothing: no model call after the interrupt
    with db.connect() as conn:
        after = conn.execute(
            text("SELECT status, owner FROM ops.exceptions WHERE exception_id = :id"), {"id": e.exception_id}
        ).one()
        event = conn.execute(
            text(
                "SELECT action, actor, details FROM ops.exception_events WHERE exception_id = :id "
                "ORDER BY event_id DESC LIMIT 1"
            ),
            {"id": e.exception_id},
        ).one()
    assert (after.status, after.owner) == ("assigned", "master_data:customers")
    assert (event.action, event.actor) == ("agent_classified", "owner")
    assert event.details["run_id"] == paused.run_id and event.details["note"] == "create the account"
    with pytest.raises(DecisionError, match="not awaiting approval"):
        decide(fresh, _url(db), paused.run_id, "reject", "owner")


@pytest.mark.postgres
def test_the_loop_stops_at_six_tool_calls_and_must_classify(db: Engine) -> None:
    e = _exception(db, "V007")
    model = ScriptedModel(
        [step("search_prior_resolutions", symptom="negative quantity", rule_id="V007")],
        [classification("escalate", [("row", e.row_key)])],
    )
    deps, recorder = _deps(db, model)
    paused = start_run(deps, _url(db), e.exception_id, "analyst")
    assert len([s for s in paused.steps if s.get("tool")]) == MAX_TOOL_CALLS == 6
    assert [c.purpose for c in recorder.calls].count("agent_investigate") == 6
    assert _resolution(paused)["outcome"] == "escalate" and paused.next_nodes == ("apply",)


@pytest.mark.postgres
def test_uncited_evidence_is_retried_once_then_the_run_says_it_could_not_conclude(db: Engine) -> None:
    e = _exception(db, "V002")
    invented = classification("source_defect", [("resolution", "RES-9999")])
    model = ScriptedModel([step("classify")], [invented])
    deps, recorder = _deps(db, model)
    paused = start_run(deps, _url(db), e.exception_id, "analyst")
    assert paused.fallback and _resolution(paused)["outcome"] == "escalate"
    assert _resolution(paused)["confidence"] == 0.0 and "could not reach" in _resolution(paused)["rationale"]
    assert [c.purpose for c in recorder.calls].count("agent_classify") == 2  # one retry, never more
    retry_prompt = [u for k, u in model.prompts if k == "classify"][1]
    assert "RES-9999 does not appear" in retry_prompt


@pytest.mark.postgres
def test_a_right_but_forbidden_fix_goes_to_a_person_and_approval_never_touches_silver(db: Engine) -> None:
    e = _exception(db, "V005")
    item = e.details["item_no"]
    fix = {
        "kind": "item_no_normalisation",
        "retain_original": True,
        "description": "use the master item",
        "changes": [{"field": "item_no", "original": item, "proposed": "IT00001"}],
    }
    model = ScriptedModel(
        [step("lookup_master", value=item, master="items"), step("classify")],
        [classification("auto_fixable", [("row", e.row_key)], fix)],
    )
    deps, _ = _deps(db, model)
    with db.connect() as conn:
        before = (
            conn.execute(
                text(
                    "SELECT s.* FROM silver.order_lines s JOIN ops.exceptions x ON x.batch_id = s.batch_id "
                    "AND x.source_row = s.source_row WHERE x.exception_id = :id"
                ),
                {"id": e.exception_id},
            )
            .mappings()
            .one()
        )
    paused = start_run(deps, _url(db), e.exception_id, "analyst")
    assert paused.gate is not None and paused.gate["disposition"] == "human_proposal"
    assert any("not an allowed auto-fix" in r for r in paused.gate["reasons"])
    done = decide(deps, _url(db), paused.run_id, "approve", "owner")
    assert done.applied == {"decision": "approve", "owner": f"source:{e.source}", "fix_applicable": False}
    with db.connect() as conn:
        after = (
            conn.execute(
                text(
                    "SELECT s.* FROM silver.order_lines s JOIN ops.exceptions x ON x.batch_id = s.batch_id "
                    "AND x.source_row = s.source_row WHERE x.exception_id = :id"
                ),
                {"id": e.exception_id},
            )
            .mappings()
            .one()
        )
    assert dict(after) == dict(before)


@pytest.mark.postgres
def test_reject_records_the_decision_and_leaves_the_exception_alone(db: Engine) -> None:
    e = _exception(db, "V003")
    model = ScriptedModel(["not json", "still not json"], [classification("escalate", [("row", e.row_key)])])
    deps, _ = _deps(db, model)
    paused = start_run(deps, _url(db), e.exception_id, "analyst")
    assert paused.steps[-1] == {"tool": None, "error": "no valid next-step decision"}
    done = decide(deps, _url(db), paused.run_id, "reject", "owner", "commercial already handled it")
    assert done.applied == {"decision": "reject", "owner": None, "fix_applicable": False}
    with db.connect() as conn:
        after = conn.execute(
            text("SELECT status, owner FROM ops.exceptions WHERE exception_id = :id"), {"id": e.exception_id}
        ).one()
        action = conn.execute(
            text(
                "SELECT action FROM ops.exception_events WHERE exception_id = :id "
                "ORDER BY event_id DESC LIMIT 1"
            ),
            {"id": e.exception_id},
        ).scalar_one()
        run = conn.execute(
            text("SELECT status, decision, decision_note FROM ops.agent_runs WHERE run_id = :r"),
            {"r": paused.run_id},
        ).one()
    assert (after.status, after.owner, action) == (e.status, e.owner, "agent_rejected")
    assert tuple(run) == ("completed", "reject", "commercial already handled it")


# --- API ------------------------------------------------------------------------------------


@pytest.fixture
def api(db: Engine) -> Iterator[tuple[TestClient, ScriptedModel]]:
    model = ScriptedModel([step("classify")], [])
    settings = Settings(
        database_url=_url(db),
        llm_provider="none",
        data_dir=DATA,
        api_keys={"k-viewer": "viewer", "k-analyst": "analyst", "k-owner": "owner"},
    )
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_agent_deps] = lambda: _deps(db, model)[0]
    try:
        yield TestClient(app), model
    finally:
        app.dependency_overrides.clear()


@pytest.mark.postgres
def test_api_roles_start_read_and_owner_only_decision(
    api: tuple[TestClient, ScriptedModel], db: Engine
) -> None:
    client, model = api
    e = _exception(db, "V011")
    model.classifications = [classification("source_defect", [("row", e.row_key)])]
    path = f"/exceptions/{e.exception_id}/agent-runs"
    assert client.post(path, headers={"X-API-Key": "k-viewer"}).status_code == 403
    started = client.post(path, headers={"X-API-Key": "k-analyst"})
    assert started.status_code == 201 and started.json()["awaiting_approval"]
    run_id = started.json()["run_id"]
    assert (
        client.post(path, headers={"X-API-Key": "k-analyst"}).status_code == 409
    )  # one open run per exception
    assert (
        client.get(f"/agent-runs/{run_id}", headers={"X-API-Key": "k-viewer"}).json()["status"]
        == "awaiting_approval"
    )
    decision = f"/agent-runs/{run_id}/decision"
    assert (
        client.post(decision, json={"decision": "approve"}, headers={"X-API-Key": "k-analyst"}).status_code
        == 403
    )
    done = client.post(decision, json={"decision": "approve"}, headers={"X-API-Key": "k-owner"})
    assert done.status_code == 200 and done.json()["status"] == "completed"
    assert (
        client.post(decision, json={"decision": "approve"}, headers={"X-API-Key": "k-owner"}).status_code
        == 409
    )
    assert client.get("/agent-runs/not-a-uuid", headers={"X-API-Key": "k-viewer"}).status_code == 404
