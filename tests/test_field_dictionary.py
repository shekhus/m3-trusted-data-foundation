from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest
import yaml
from conftest import Loaded
from sqlalchemy import Engine, text

from app.config import REPO_ROOT
from evals.change_request_eval import score
from metrics.compiler import CompileError
from metrics.fields import FIELDS_DIR, FieldDictionary, current_fields, load_fields
from pipeline.publish import publish_batch
from rules import BY_ID, RULES, RuleContext
from rules.base import Masters

DATA = REPO_ROOT / "data"


def _masters() -> Masters:
    lots = pd.DataFrame({"lot_no": ["L1"], "expiry_date": pd.to_datetime(["2027-01-01"])}).set_index("lot_no")
    return Masters(
        customers=frozenset({"C000001"}),
        items=frozenset({"IT1"}),
        item_uom={"IT1": "CASE"},
        item_lb_per_case={"IT1": 10.0},
        lots=lots,
    )


def _frame(*rows: dict) -> pd.DataFrame:
    base = {
        "order_no": "SO1",
        "line_no": 1,
        "customer_no": "C000001",
        "item_no": "IT1",
        "plant": "P",
        "order_date": "2026-05-20",
        "requested_date": "2026-05-28",
        "confirmed_delivery_date": "2026-05-28",
        "issue_date": "2026-05-28",
        "ordered_qty": 2.0,
        "invoiced_qty": 2.0,
        "ordered_weight_lb": 20.0,
        "invoiced_weight_lb": 20.0,
        "uom": "CASE",
        "lot_no": "L1",
        "parse_errors": {},
    }
    df = pd.DataFrame([{**base, **r, "source_row": i + 1, "line_no": i + 1} for i, r in enumerate(rows)])
    for c in ("order_date", "requested_date", "confirmed_delivery_date", "issue_date"):
        df[c] = pd.to_datetime(df[c])
    return df


WITH_LOT = RuleContext("plt09", _masters(), mapped=frozenset({"lot_no"}))
NO_LOT = RuleContext("plt09", _masters(), mapped=frozenset())


# --- the dictionary -----------------------------------------------------------------------


def test_repo_dictionary_v2_is_current_and_requires_lot_no_from_june() -> None:
    versions = load_fields()
    assert [(d.version, d.status) for d in versions] == [(1, "superseded"), (2, "current")]
    current = current_fields()
    assert [(r.column, str(r.required_from), r.by) for r in current.dated] == [
        ("lot_no", "2026-06-01", "issue_date")
    ]
    # the bump only adds: v1's always-required fields are unchanged, so V001 behaves as before
    assert current.always == versions[0].always and len(current.always) == 10


def _write(tmp_path: Path, name: str, edit: dict) -> None:
    doc = yaml.safe_load((FIELDS_DIR / "order_line.v2.yaml").read_text(encoding="utf-8"))
    doc.update(edit)
    (tmp_path / name).write_text(yaml.safe_dump(doc), encoding="utf-8")


@pytest.mark.parametrize(
    ("edit", "message"),
    [
        ({"required": [{"column": "lot_number"}]}, "not a canonical column"),
        ({"required": [{"column": "lot_no"}]}, "may leave it unmapped"),
        (
            {"required": [{"column": "lot_no", "required_from": "2026-06-01"}]},
            "needs by, owner, fix and before",
        ),
        (
            {
                "required": [
                    {
                        "column": "lot_no",
                        "required_from": "2026-06-01",
                        "by": "plant",
                        "owner": "q",
                        "fix": "f",
                        "before": "b",
                    }
                ]
            },
            "not a canonical date column",
        ),
        ({"required": [{"column": "order_no"}, {"column": "order_no"}]}, "listed twice"),
    ],
)
def test_dictionary_rejects_requirements_that_cannot_be_checked(
    tmp_path: Path, edit: dict, message: str
) -> None:
    _write(tmp_path, "order_line.v1.yaml", {**edit, "version": 1})
    with pytest.raises(CompileError, match=message):
        load_fields(tmp_path)


def test_dictionary_needs_one_current_latest_version(tmp_path: Path) -> None:
    _write(tmp_path, "order_line.v1.yaml", {"version": 1})
    _write(tmp_path, "order_line.v2.yaml", {"version": 2, "status": "superseded"})
    with pytest.raises(CompileError, match="exactly one"):
        load_fields(tmp_path)
    _write(tmp_path, "order_line.v3.yaml", {"version": 4})
    with pytest.raises(CompileError, match="file name"):
        load_fields(tmp_path)


# --- V012 ---------------------------------------------------------------------------------


def test_v012_blocks_a_blank_lot_shipped_on_or_after_the_effective_date() -> None:
    df = _frame({"lot_no": None, "issue_date": "2026-06-01"}, {"issue_date": "2026-06-02"})
    violations = BY_ID["V012"].check(df, WITH_LOT)
    assert violations.rows == [0] and violations.severities == ["block"] and violations.owners == ["quality"]
    details = violations.details[0]
    assert details["coverage"] == "required" and details["in_extract"] and details["dictionary_version"] == 2
    assert details["reason"] == "lot_no required from 2026-06-01 (field dictionary v2) is blank"


def test_v012_queues_history_as_not_covered_warnings_never_blocks_or_drops_it() -> None:
    df = _frame(
        {"lot_no": None, "issue_date": "2026-05-31"},
        {"lot_no": None, "issue_date": "2025-03-04"},
        {"lot_no": None, "issue_date": "2026-06-15"},
    )
    violations = BY_ID["V012"].check(df, NO_LOT)  # an extract without a lot column
    assert violations.rows == [0, 1, 2]
    assert violations.severities == ["warn", "warn", "block"]
    assert [d["coverage"] for d in violations.details] == ["not_covered", "not_covered", "required"]
    assert (
        violations.details[0]["reason"]
        == "lot_no not covered before 2026-06-01: not in this source's extract"
    )
    assert violations.details[0]["fix"].startswith("Not covered")


def test_v012_leaves_undated_and_unparsed_rows_to_v001_and_v010() -> None:
    df = _frame({"lot_no": None, "issue_date": None}, {"lot_no": None, "parse_errors": {"lot_no": "??"}})
    assert BY_ID["V012"].check(df, WITH_LOT).rows == []


def test_v012_follows_the_dictionary_not_code(tmp_path: Path) -> None:
    doc = yaml.safe_load((FIELDS_DIR / "order_line.v2.yaml").read_text(encoding="utf-8"))
    doc["required"][-1]["required_from"] = "2026-07-01"
    moved = FieldDictionary.model_validate(doc)
    df = _frame({"lot_no": None, "issue_date": "2026-06-15"})
    ctx = RuleContext("plt09", _masters(), mapped=frozenset({"lot_no"}), fields=moved)
    assert BY_ID["V012"].check(df, ctx).severities == ["warn"]


def test_a_complete_row_raises_nothing_under_the_bumped_dictionary() -> None:
    df = _frame({"issue_date": "2026-06-10"})
    assert all(r.check(df, WITH_LOT).rows == [] for r in RULES)


# --- full load of all three plants: scored from the raw extract -----------------------------


@pytest.mark.postgres
def test_change_request_exceptions_match_the_raw_extract_exactly(silver_db: Engine, loaded: Loaded) -> None:
    with silver_db.connect() as conn:
        result = score(conn, DATA)
    assert result.passed, result
    # PLT-01 sent no lot column before June 2026: 17,695 of those lines shipped earlier (not covered) and 299
    # May orders shipped from 1 June (required, blocked); PLT-02 and PLT-03 always send a lot
    assert (result.source, result.expected_warn, result.expected_block) == ("plt01", 17695, 299)
    assert (result.raised_warn, result.raised_block) == (17695, 299)
    assert result.other_sources == {
        "plt02": {"expected": 0, "raised": 0},
        "plt03": {"expected": 0, "raised": 0},
    }


@pytest.mark.postgres
def test_publish_holds_required_lines_and_keeps_not_covered_history(
    silver_db: Engine, loaded: Loaded
) -> None:
    with silver_db.connect() as conn:
        batch = conn.execute(
            text(
                "SELECT e.batch_id, count(*) FILTER (WHERE e.severity = 'block') AS blocked, "
                "count(*) FILTER (WHERE e.severity = 'warn') AS warned FROM ops.exceptions e "
                "WHERE e.rule_id = 'V012' GROUP BY 1 HAVING count(*) FILTER (WHERE e.severity = 'block') > 0 "
                "ORDER BY 2 DESC LIMIT 1"
            )
        ).one()
        other_blocking = conn.execute(
            text(
                "SELECT count(DISTINCT source_row) FROM ops.exceptions "
                "WHERE batch_id = :b AND severity = 'block' "
                "AND status <> 'resolved' AND source_row NOT IN (SELECT source_row FROM ops.exceptions "
                "WHERE batch_id = :b AND rule_id = 'V012' AND severity = 'block')"
            ),
            {"b": batch.batch_id},
        ).scalar_one()
        result = publish_batch(conn, str(batch.batch_id), "owner", DATA / "master")
        published_warned = conn.execute(
            text(
                "SELECT count(*) FROM gold.fact_delivery g JOIN ops.exceptions e ON e.batch_id = g.batch_id "
                "AND e.row_key = g.order_no || '|' || g.line_no WHERE g.batch_id = :b AND e.rule_id = 'V012' "
                "AND e.severity = 'warn'"
            ),
            {"b": batch.batch_id},
        ).scalar_one()
        conn.rollback()
    assert result.status == "published"
    assert result.rows_held["open blocking exception"] == batch.blocked + other_blocking
    assert published_warned > 0  # history that predates the requirement still reaches gold
