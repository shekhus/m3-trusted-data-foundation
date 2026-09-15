from __future__ import annotations

import pytest
from conftest import Loaded
from sqlalchemy import Engine, text

from agent.contracts import EvidenceRef, FieldChange, ProposedFix, Resolution
from agent.policy_gate import evaluate
from agent.tools import AgentTools, exception_facts, new_run_id, normalisations
from app.config import REPO_ROOT
from llm.embeddings import HashingEmbedder
from retrieval.access import AccessContext
from retrieval.store import sync

DATA = REPO_ROOT / "data"


@pytest.mark.parametrize(
    ("value", "master", "expected"),
    [
        ("CUST-0004", "customers", ("customer_prefix_and_padding", "C000004")),
        ("c4", "customers", ("customer_prefix_and_padding", "C000004")),
        ("IT00037  ", "items", ("trim", "IT00037")),
    ],
)
def test_known_normalisations(value: str, master: str, expected: tuple[str, str]) -> None:
    assert expected in normalisations(value, master)


def _registered_run(engine: Engine) -> str:
    """Tool calls made inside a run must belong to a real ops.agent_runs row (the tool_calls_run_fk)."""
    run_id = new_run_id()
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO ops.agent_runs (run_id, exception_id, started_by) "
                "SELECT :r, min(exception_id), 'test' FROM ops.exceptions"
            ),
            {"r": run_id},
        )
    return run_id


@pytest.fixture(scope="module")
def tools(silver_db: Engine, loaded: Loaded) -> AgentTools:
    sync(silver_db, DATA / "kb", HashingEmbedder())  # the agent's database also holds the retrieval index
    return AgentTools(silver_db, DATA / "master", None, run_id=_registered_run(silver_db))


def _calls(engine: Engine, run_id: str) -> list:
    with engine.connect() as conn:
        return list(
            conn.execute(
                text(
                    "SELECT tool, outcome, result_count, arguments, result, latency_ms, error "
                    "FROM ops.tool_calls WHERE run_id = :r ORDER BY tool_call_id"
                ),
                {"r": run_id},
            ).all()
        )


@pytest.mark.postgres
def test_lookup_master_tries_exact_then_normalisations_then_fuzzy(tools: AgentTools) -> None:
    assert [m.method for m in tools.lookup_master("C000004", "customers").matches] == ["exact"]
    assert [(m.candidate, m.method) for m in tools.lookup_master("CUST-0004", "customers").matches] == [
        ("C000004", "normalised:customer_prefix_and_padding")
    ]
    fuzzy = tools.lookup_master("IT0003", "items").matches
    assert fuzzy and all(m.method == "fuzzy" and 0.85 <= m.score < 1 for m in fuzzy)
    assert tools.lookup_master("C929079", "customers", fuzzy=False).matches == []  # genuinely absent


@pytest.mark.postgres
def test_prior_resolutions_are_found_by_symptom_and_filtered_by_rule(tools: AgentTools) -> None:
    found = tools.search_prior_resolutions(
        "customer number in the CUST-#### form is not in the master", "V004_unknown_customer"
    ).results
    assert found and all(r.rule_id.startswith("V004") for r in found)
    assert found[0].classification == "auto_fixable"


@pytest.mark.postgres
def test_knowledge_base_search_returns_documents_filtered_for_the_agent(tools: AgentTools) -> None:
    results = tools.search_knowledge_base(
        "auto-fix policy customer number item number quantity weight"
    ).results
    assert results and all(not c.doc_id.startswith("RES-") for c in results)
    assert "SOP-DQ-001-v2" in {c.doc_id for c in results}
    commercial = tools.search_knowledge_base("Northgate Markets on-time-in-full commitment rebate").results
    assert all(c.doc_id not in ("SLA-C000031", "SLA-C000002", "POL-ACC-001") for c in commercial)


@pytest.mark.postgres
def test_inspect_source_rows_returns_the_raw_neighbourhood_and_other_sources(
    tools: AgentTools, silver_db: Engine
) -> None:
    with silver_db.connect() as conn:
        e = conn.execute(
            text(
                "SELECT source, row_key, batch_id::text AS batch_id, source_row FROM ops.exceptions "
                "WHERE rule_id = 'V004' ORDER BY exception_id LIMIT 1"
            )
        ).one()
    rows = tools.inspect_source_rows(e.source, e.row_key, window=2, batch_id=e.batch_id)
    assert [r.source_row for r in rows.rows if r.is_target] == [e.source_row]
    assert len(rows.rows) == 5 and rows.file_name and rows.rows[0].record
    missing = tools.inspect_source_rows(e.source, "SO9999999|1")
    assert missing.rows == [] and missing.batch_id is None

    others = tools.check_other_sources("C000004", "customer_no", exclude_source="plt01")
    assert set(others.by_source) <= {"plt02", "plt03"} and others.by_source
    with pytest.raises(ValueError, match="field must be one of"):
        tools.check_other_sources("x", "customer_no; DROP TABLE ops.exceptions")


@pytest.mark.postgres
def test_every_tool_call_is_logged_with_arguments_result_latency_and_outcome(
    silver_db: Engine, loaded: Loaded
) -> None:
    run = _registered_run(silver_db)
    t = AgentTools(silver_db, DATA / "master", None, access=AccessContext("analyst"), run_id=run)
    t.lookup_master("CUST-0004", "customers")
    t.lookup_master("ZZZ", "customers", fuzzy=False)
    with pytest.raises(ValueError):
        t.check_other_sources("x", "nope")
    calls = _calls(silver_db, run)
    assert [(c.tool, c.outcome, c.result_count) for c in calls] == [
        ("lookup_master", "ok", 1),
        ("lookup_master", "empty", 0),
        ("check_other_sources", "error", None),
    ]
    assert calls[0].arguments == {"value": "CUST-0004", "master": "customers", "fuzzy": True}
    assert calls[0].result["matches"][0]["candidate"] == "C000004"
    assert calls[2].error.startswith("ValueError") and all(c.latency_ms >= 0 for c in calls)


@pytest.mark.postgres
def test_gate_facts_come_from_the_database_and_block_a_right_but_forbidden_fix(
    silver_db: Engine, loaded: Loaded
) -> None:
    with silver_db.connect() as conn:
        e = conn.execute(
            text(
                "SELECT exception_id, details->>'uom' AS uom FROM ops.exceptions "
                "WHERE rule_id = 'V009' ORDER BY exception_id LIMIT 1"
            )
        ).one()
    facts = exception_facts(silver_db, e.exception_id, DATA / "master")
    assert facts.rule_id == "V009" and facts.row["uom"] == e.uom and facts.raw["uom"] is not None
    weight = float(facts.row["ordered_weight_lb"])  # type: ignore[arg-type]
    convert = ProposedFix(
        kind="uom_kg_to_lb",
        retain_original=True,
        changes=[
            FieldChange(field="ordered_weight_lb", original=weight, proposed=round(weight * 2.20462, 3))
        ],
    )
    customer = ProposedFix(
        kind="uom_kg_to_lb",
        retain_original=True,
        changes=[
            FieldChange(field="ordered_weight_lb", original=weight, proposed=round(weight * 2.20462, 3)),
            FieldChange(field="customer_no", original=facts.row["customer_no"], proposed="C000001"),
        ],
    )

    def classify(fix: ProposedFix) -> Resolution:
        return Resolution(
            exception_id=e.exception_id,
            outcome="auto_fixable",
            confidence=0.7,
            proposed_fix=fix,
            evidence_refs=[EvidenceRef(kind="row", ref="x")],
            rationale="weights recorded in kg",
        )

    assert evaluate(classify(convert), facts).applicable
    assert evaluate(classify(customer), facts).disposition == "human_proposal"
