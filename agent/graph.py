"""The exception resolution agent (docs/addendum1.md A13; CLAUDE.md "Agentic conventions"; D-030).

    triage → investigate (tool loop, ≤ 6 tool calls) → classify → policy_gate → [interrupt_before: apply]
           → apply → record

Why this is the one agentic component: the next investigation step depends on what the last tool returned,
so the number of steps is not knowable in advance. Everything around the loop is fixed code:

- **investigate** asks the model for one decision at a time (a tool and its arguments, or `classify`) and
  runs the tool in code. After 6 tool calls it must classify with what it has.
- **classify** asks for the Resolution contract (agent/contracts.py). Code then checks that every evidence_ref
  appears in what the tools returned. An invalid classification is retried once with the error; after that
  the run escalates with confidence 0 and says it could not conclude (`fallback`), never loops.
- **policy_gate** is agent/policy_gate.py, in code.
- The graph always stops **before apply** (LangGraph static interrupt, Postgres checkpointer). Nothing changes
  until an owner records a decision and the run is resumed, in any process, from the checkpoint.
- **apply** writes to ops.exceptions only: on approval it assigns the exception to the owner the outcome
  implies and records the classification, gate verdict and decision as an event. It never writes silver,
  gold or a master, and never applies a fix to data; an approved fix is recorded for the owner to carry out.
"""

from __future__ import annotations

import json
import operator
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any, Literal, TypedDict

import psycopg
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.graph import END, START, StateGraph
from psycopg.rows import dict_row
from pydantic import BaseModel, ConfigDict
from sqlalchemy import Engine, text
from sqlalchemy.engine import make_url

from agent.contracts import Resolution
from agent.policy_gate import evaluate
from agent.tools import AgentTools, exception_facts
from llm.client import PROMPTS_DIR, InvalidOutput, LLMClient, LLMError
from llm.embeddings import Embedder

MAX_TOOL_CALLS = 6
RESULT_CHARS = 2500  # per step, in the model's context; the full result is in ops.tool_calls
CHECKPOINT_SCHEMA = "agent_checkpoints"
INVESTIGATE_SYSTEM = (PROMPTS_DIR / "agent_investigate_system.md").read_text(encoding="utf-8")
CLASSIFY_SYSTEM = (PROMPTS_DIR / "agent_classify_system.md").read_text(encoding="utf-8")
TOOLS = (
    "search_prior_resolutions",
    "search_knowledge_base",
    "lookup_master",
    "inspect_source_rows",
    "check_other_sources",
)
ARGUMENTS = ("symptom", "rule_id", "query", "value", "master", "source", "row_key", "key", "field")
Decision = Literal["approve", "reject"]


class AgentState(TypedDict, total=False):
    run_id: str
    exception_id: int
    exception: dict[str, Any]
    steps: Annotated[list[dict[str, Any]], operator.add]
    tool_calls: int
    ready: bool
    resolution: dict[str, Any]
    fallback: bool
    classify_errors: list[str]
    gate: dict[str, Any]
    decision: Decision
    decided_by: str
    decision_note: str
    applied: dict[str, Any]


class NextStep(BaseModel):
    model_config = ConfigDict(extra="forbid")

    thought: str
    action: Literal[
        "search_prior_resolutions",
        "search_knowledge_base",
        "lookup_master",
        "inspect_source_rows",
        "check_other_sources",
        "classify",
    ]
    symptom: str | None
    rule_id: str | None
    query: str | None
    value: str | None
    master: Literal["customers", "items", "lots"] | None
    source: str | None
    row_key: str | None
    key: str | None
    field: Literal["customer_no", "item_no", "lot_no", "order_no"] | None


NULLABLE_STRING = {"type": ["string", "null"]}
NEXT_STEP_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["thought", "action", *ARGUMENTS],
    "properties": {
        "thought": {"type": "string"},
        "action": {"type": "string", "enum": [*TOOLS, "classify"]},
        **{a: NULLABLE_STRING for a in ARGUMENTS if a not in ("master", "field")},
        "master": {"type": ["string", "null"], "enum": ["customers", "items", "lots", None]},
        "field": {"type": ["string", "null"], "enum": ["customer_no", "item_no", "lot_no", "order_no", None]},
    },
}
VALUE = {"type": ["string", "number", "null"]}
CLASSIFY_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "outcome",
        "confidence",
        "evidence_refs",
        "fix_kind",
        "fix_changes",
        "fix_retain_original",
        "fix_description",
        "not_covered",
        "rationale",
    ],
    "properties": {
        "outcome": {
            "type": "string",
            "enum": ["auto_fixable", "needs_master_data", "source_defect", "escalate"],
        },
        "confidence": {"type": "number"},
        "evidence_refs": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["kind", "ref"],
                "properties": {
                    "kind": {
                        "type": "string",
                        "enum": ["resolution", "document", "row", "master", "other_source"],
                    },
                    "ref": {"type": "string"},
                },
            },
        },
        # Flat, not a nullable nested object: Groq's strict decoder fails on anyOf[null, object] (D-030).
        # fix_kind null means no fix; code rebuilds the ProposedFix from these fields (`_from_flat`).
        "fix_kind": {"type": ["string", "null"]},
        "fix_changes": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["field", "original", "proposed"],
                "properties": {"field": {"type": "string"}, "original": VALUE, "proposed": VALUE},
            },
        },
        "fix_retain_original": {"type": "boolean"},
        "fix_description": {"type": "string"},
        "not_covered": {"type": "boolean"},
        "rationale": {"type": "string"},
    },
}


def _from_flat(data: dict[str, Any], exception_id: int) -> dict[str, Any]:
    """The model's flat classification → the Resolution contract's shape."""
    fix_kind = data.get("fix_kind")
    fix = None
    if fix_kind:
        fix = {
            "kind": fix_kind,
            "changes": data.get("fix_changes", []),
            "retain_original": data.get("fix_retain_original", False),
            "description": data.get("fix_description", ""),
        }
    rest = {k: v for k, v in data.items() if not k.startswith("fix_")}
    return {**rest, "proposed_fix": fix, "exception_id": exception_id}


OWNERS = {
    "needs_master_data": {
        "V004": "master_data:customers",
        "V005": "master_data:items",
        "V006": "master_data:lots",
    },
    "escalate": {"V003": "commercial", "V006": "quality", "V007": "finance", "V012": "quality"},
}


def owner_for(outcome: str, rule_id: str, source: str) -> str:
    if outcome in ("auto_fixable", "source_defect"):
        return f"source:{source}"
    if outcome == "needs_master_data":
        return OWNERS["needs_master_data"].get(
            rule_id, f"source:{source}"
        )  # e.g. a confirmation date to supply
    return OWNERS["escalate"].get(rule_id, "quality")


def evidence_problems(
    resolution: Resolution, exception: dict[str, Any], steps: list[dict[str, Any]]
) -> list[str]:
    """Every evidence_ref must appear in what the investigation returned (or be the exception's own row)."""
    seen = json.dumps([exception, [s.get("result") for s in steps]], default=str)
    problems = []
    for ref in resolution.evidence_refs:
        tail = ref.ref.split(":", 1)[-1]
        if ref.ref not in seen and not (len(tail) >= 4 and tail in seen):  # a short tail would match anything
            problems.append(f"evidence {ref.kind}:{ref.ref} does not appear in the investigation results")
    return problems


@dataclass
class AgentDeps:
    engine: Engine
    llm: LLMClient
    master_dir: Path
    embedder: Embedder | None


def build_graph(deps: AgentDeps) -> StateGraph:
    def tools_for(state: AgentState) -> AgentTools:
        return AgentTools(
            deps.engine,
            deps.master_dir,
            deps.embedder,
            run_id=state["run_id"],
            exception_id=state["exception_id"],
        )

    def triage(state: AgentState) -> AgentState:
        with deps.engine.connect() as conn:
            e = (
                conn.execute(
                    text(
                        "SELECT exception_id, source, rule_id, severity, row_key, reason, suggested_fix, "
                        "details "
                        "FROM ops.exceptions WHERE exception_id = :id"
                    ),
                    {"id": state["exception_id"]},
                )
                .mappings()
                .one()
            )
        facts = exception_facts(deps.engine, state["exception_id"], deps.master_dir)
        exception = {
            **dict(e),
            "row": {k: v for k, v in facts.row.items() if k not in ("batch_id", "mapping_version_id")},
            "raw_source_text": facts.raw,
        }
        return {
            "exception": json.loads(json.dumps(exception, default=str)),
            "steps": [],
            "tool_calls": 0,
            "ready": False,
            "classify_errors": [],
        }

    def investigate(state: AgentState) -> AgentState:
        remaining = MAX_TOOL_CALLS - state.get("tool_calls", 0)
        user = json.dumps(
            {
                "exception": state["exception"],
                "steps": state.get("steps", []),
                "remaining_tool_calls": remaining,
            },
            default=str,
        )
        step: NextStep | None = None
        for _ in (1, 2):
            try:
                step = deps.llm.complete_json(
                    "agent_investigate",
                    INVESTIGATE_SYSTEM,
                    user,
                    NEXT_STEP_SCHEMA,
                    NextStep.model_validate,
                    max_tokens=2000,
                )
                break
            except (InvalidOutput, LLMError):
                continue
        if step is None:  # no usable decision after one retry: classify with what exists
            return {"ready": True, "steps": [{"tool": None, "error": "no valid next-step decision"}]}
        if step.action == "classify":
            return {"ready": True}
        args = {a: getattr(step, a) for a in ARGUMENTS if getattr(step, a) is not None}
        record: dict[str, Any] = {"tool": step.action, "thought": step.thought, "arguments": args}
        try:
            result = _dispatch(tools_for(state), step.action, args)
            record["result"] = json.dumps(result.model_dump(mode="json"), default=str)[:RESULT_CHARS]
        except (TypeError, ValueError, LLMError) as exc:
            record["error"] = f"{type(exc).__name__}: {exc}"[:500]
        return {"steps": [record], "tool_calls": state.get("tool_calls", 0) + 1}

    def after_investigate(state: AgentState) -> str:
        return (
            "classify"
            if state.get("ready") or state.get("tool_calls", 0) >= MAX_TOOL_CALLS
            else "investigate"
        )

    def classify(state: AgentState) -> AgentState:
        errors: list[str] = []
        base = {"exception": state["exception"], "steps": state.get("steps", [])}
        for _ in (1, 2):
            user = json.dumps({**base, "previous_attempt_errors": errors} if errors else base, default=str)
            try:
                resolution = deps.llm.complete_json(
                    "agent_classify",
                    CLASSIFY_SYSTEM,
                    user,
                    CLASSIFY_SCHEMA,
                    lambda d: Resolution.model_validate(_from_flat(d, state["exception_id"])),
                    max_tokens=3000,
                )
            except (InvalidOutput, LLMError) as exc:
                errors.append(str(exc)[:500])
                continue
            problems = evidence_problems(resolution, state["exception"], state.get("steps", []))
            if not problems:
                return {
                    "resolution": resolution.model_dump(mode="json"),
                    "fallback": False,
                    "classify_errors": errors,
                }
            errors += problems
        fallback = Resolution(
            exception_id=state["exception_id"],
            outcome="escalate",
            confidence=0.0,
            evidence_refs=[{"kind": "row", "ref": state["exception"]["row_key"]}],  # type: ignore[list-item]
            rationale="The agent could not reach a valid, evidenced classification after one retry; "
            "a person must "
            "decide. Errors: " + "; ".join(errors)[:800],
        )
        return {"resolution": fallback.model_dump(mode="json"), "fallback": True, "classify_errors": errors}

    def policy_gate(state: AgentState) -> AgentState:
        resolution = Resolution.model_validate(state["resolution"])
        verdict = evaluate(resolution, exception_facts(deps.engine, state["exception_id"], deps.master_dir))
        gate = {"disposition": verdict.disposition, "reasons": verdict.reasons}
        with deps.engine.begin() as conn:
            conn.execute(
                text(
                    "UPDATE ops.agent_runs SET status = 'awaiting_approval', iterations = :n, outcome = :o, "
                    "confidence = :c, not_covered = :nc, fallback = :f, resolution = CAST(:r AS jsonb), "
                    "gate = CAST(:g AS jsonb) WHERE run_id = :run"
                ),
                {
                    "n": state.get("tool_calls", 0),
                    "o": resolution.outcome,
                    "c": resolution.confidence,
                    "nc": resolution.not_covered,
                    "f": state.get("fallback", False),
                    "r": json.dumps(state["resolution"]),
                    "g": json.dumps(gate),
                    "run": state["run_id"],
                },
            )
        return {"gate": gate}

    def apply(state: AgentState) -> AgentState:
        decision = state.get("decision")
        if decision not in ("approve", "reject") or not state.get("decided_by"):
            raise RuntimeError("apply reached without an owner's decision")
        e = state["exception"]
        resolution, gate = state["resolution"], state["gate"]
        details = {
            "run_id": state["run_id"],
            "outcome": resolution["outcome"],
            "confidence": resolution["confidence"],
            "not_covered": resolution["not_covered"],
            "evidence_refs": resolution["evidence_refs"],
            "proposed_fix": resolution["proposed_fix"],
            "gate": gate,
            "decision": decision,
            "note": state.get("decision_note"),
        }
        owner = owner_for(resolution["outcome"], e["rule_id"], e["source"])
        with deps.engine.begin() as conn:
            if decision == "approve":
                conn.execute(
                    text(
                        "UPDATE ops.exceptions SET owner = :owner, status = 'assigned', assigned_by = :by, "
                        "assigned_at = now() WHERE exception_id = :id AND status <> 'resolved'"
                    ),
                    {"owner": owner, "by": f"agent:{state['decided_by']}", "id": state["exception_id"]},
                )
            conn.execute(
                text(
                    "INSERT INTO ops.exception_events (exception_id, action, actor, details) "
                    "VALUES (:id, :action, :actor, CAST(:details AS jsonb))"
                ),
                {
                    "id": state["exception_id"],
                    "actor": state["decided_by"],
                    "details": json.dumps(details),
                    "action": "agent_classified" if decision == "approve" else "agent_rejected",
                },
            )
        return {
            "applied": {
                "decision": decision,
                "owner": owner if decision == "approve" else None,
                "fix_applicable": gate["disposition"] == "apply_after_approval",
            }
        }

    def record(state: AgentState) -> AgentState:
        with deps.engine.begin() as conn:
            conn.execute(
                text(
                    "UPDATE ops.agent_runs SET status = 'completed', decision = :d, decided_by = :by, "
                    "decision_note = :note, decided_at = now(), finished_at = now() WHERE run_id = :run"
                ),
                {
                    "d": state["decision"],
                    "by": state["decided_by"],
                    "note": state.get("decision_note"),
                    "run": state["run_id"],
                },
            )
        return {}

    graph = StateGraph(AgentState)
    for name, node in (
        ("triage", triage),
        ("investigate", investigate),
        ("classify", classify),
        ("policy_gate", policy_gate),
        ("apply", apply),
        ("record", record),
    ):
        graph.add_node(name, node)
    graph.add_edge(START, "triage")
    graph.add_edge("triage", "investigate")
    graph.add_conditional_edges(
        "investigate", after_investigate, {"investigate": "investigate", "classify": "classify"}
    )
    graph.add_edge("classify", "policy_gate")
    graph.add_edge("policy_gate", "apply")
    graph.add_edge("apply", "record")
    graph.add_edge("record", END)
    return graph


REQUIRED_ARGUMENTS = {
    "search_prior_resolutions": ("symptom",),
    "search_knowledge_base": ("query",),
    "lookup_master": ("value", "master"),
    "inspect_source_rows": ("source", "row_key"),
    "check_other_sources": ("key", "field"),
}


def _dispatch(tools: AgentTools, action: str, args: dict[str, str]) -> BaseModel:
    missing = [n for n in REQUIRED_ARGUMENTS.get(action, ()) if not args.get(n)]
    if missing:
        raise ValueError(f"{action} needs {', '.join(missing)}")
    if action == "search_prior_resolutions":
        return tools.search_prior_resolutions(args["symptom"], args.get("rule_id"))
    if action == "search_knowledge_base":
        return tools.search_knowledge_base(args["query"])
    if action == "lookup_master":
        return tools.lookup_master(args["value"], args["master"])  # type: ignore[arg-type]
    if action == "inspect_source_rows":
        return tools.inspect_source_rows(args["source"], args["row_key"])
    if action == "check_other_sources":
        return tools.check_other_sources(args["key"], args["field"])
    raise ValueError(f"unknown tool {action}")


def _config(run_id: str) -> RunnableConfig:
    return {"configurable": {"thread_id": run_id}}


@contextmanager
def checkpointer(database_url: str) -> Iterator[PostgresSaver]:
    dsn = make_url(database_url).set(drivername="postgresql").render_as_string(hide_password=False)
    with psycopg.connect(
        dsn, autocommit=True, row_factory=dict_row, options=f"-c search_path={CHECKPOINT_SCHEMA}"
    ) as conn:
        saver = PostgresSaver(conn)
        saver.setup()
        yield saver


@dataclass(frozen=True)
class RunView:
    run_id: str
    exception_id: int
    status: str
    next_nodes: tuple[str, ...]
    resolution: dict[str, Any] | None
    gate: dict[str, Any] | None
    steps: list[dict[str, Any]]
    fallback: bool
    applied: dict[str, Any] | None


def _view(compiled: Any, run_id: str, engine: Engine) -> RunView:  # noqa: ANN401 - CompiledStateGraph
    snapshot = compiled.get_state(_config(run_id))
    values = snapshot.values
    with engine.connect() as conn:
        status = conn.execute(
            text("SELECT status FROM ops.agent_runs WHERE run_id = :r"), {"r": run_id}
        ).scalar_one()
    return RunView(
        run_id,
        values.get("exception_id", 0),
        status,
        tuple(snapshot.next),
        values.get("resolution"),
        values.get("gate"),
        values.get("steps", []),
        values.get("fallback", False),
        values.get("applied"),
    )


def start_run(deps: AgentDeps, database_url: str, exception_id: int, started_by: str) -> RunView:
    """Run triage → … → policy_gate and stop before apply. Returns the run awaiting an owner's decision."""
    run_id = str(uuid.uuid4())
    with deps.engine.begin() as conn:
        conn.execute(
            text("INSERT INTO ops.agent_runs (run_id, exception_id, started_by) VALUES (:r, :e, :by)"),
            {"r": run_id, "e": exception_id, "by": started_by},
        )
    config = _config(run_id)
    with checkpointer(database_url) as saver:
        compiled = build_graph(deps).compile(checkpointer=saver, interrupt_before=["apply"])
        try:
            compiled.invoke({"run_id": run_id, "exception_id": exception_id}, config)
        except Exception as exc:
            with deps.engine.begin() as conn:
                conn.execute(
                    text(
                        "UPDATE ops.agent_runs SET status = 'failed', error = :e, finished_at = now() "
                        "WHERE run_id = :r"
                    ),
                    {"e": f"{type(exc).__name__}: {exc}"[:2000], "r": run_id},
                )
            raise
        return _view(compiled, run_id, deps.engine)


class DecisionError(ValueError):
    pass


def decide(
    deps: AgentDeps,
    database_url: str,
    run_id: str,
    decision: Decision,
    decided_by: str,
    note: str | None = None,
) -> RunView:
    """Record an owner's decision on a paused run and resume it from the checkpoint (any process)."""
    config = _config(run_id)
    with checkpointer(database_url) as saver:
        compiled = build_graph(deps).compile(checkpointer=saver, interrupt_before=["apply"])
        snapshot = compiled.get_state(config)
        if tuple(snapshot.next) != ("apply",):
            raise DecisionError(f"run {run_id} is not awaiting approval")
        compiled.update_state(config, {"decision": decision, "decided_by": decided_by, "decision_note": note})
        compiled.invoke(None, config)  # type: ignore[call-overload]
        return _view(compiled, run_id, deps.engine)


def load_run(deps: AgentDeps, database_url: str, run_id: str) -> RunView:
    with checkpointer(database_url) as saver:
        compiled = build_graph(deps).compile(checkpointer=saver, interrupt_before=["apply"])
        return _view(compiled, run_id, deps.engine)


__all__ = [
    "MAX_TOOL_CALLS",
    "AgentDeps",
    "DecisionError",
    "RunView",
    "build_graph",
    "decide",
    "load_run",
    "start_run",
]
