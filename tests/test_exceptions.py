from __future__ import annotations

import pandas as pd
import pytest
from conftest import Loaded
from sqlalchemy import Engine, text

from app.config import REPO_ROOT
from evals.exception_eval import TARGET, score
from pipeline.validate import load_masters, validate_batch
from rules import BY_ID, RULES, RuleContext
from rules.base import Masters

DATA = REPO_ROOT / "data"


def _masters() -> Masters:
    lots = pd.DataFrame({"lot_no": ["L1", "L2"], "item_no": ["IT1", "IT1"], "plant": ["P", "P"],
                         "expiry_date": pd.to_datetime(["2026-01-10", "2026-03-01"])}).set_index("lot_no")
    return Masters(customers=frozenset({"C000001"}), items=frozenset({"IT1"}), item_uom={"IT1": "CASE"},
                   item_lb_per_case={"IT1": 10.0}, lots=lots)


def _row(**overrides: object) -> dict:
    base = {"source_row": 1, "order_no": "SO1", "line_no": 1, "customer_no": "C000001", "item_no": "IT1",
            "plant": "P", "order_date": "2026-01-01", "requested_date": "2026-01-05",
            "confirmed_delivery_date": "2026-01-05", "issue_date": "2026-01-05", "ordered_qty": 2.0,
            "invoiced_qty": 2.0, "ordered_weight_lb": 20.0, "invoiced_weight_lb": 20.0, "uom": "CASE",
            "lot_no": "L1", "parse_errors": {}}
    return {**base, **overrides}


def _frame(*rows: dict) -> pd.DataFrame:
    df = pd.DataFrame([{**r, "source_row": i + 1} for i, r in enumerate(rows)])
    for c in ("order_date", "requested_date", "confirmed_delivery_date", "issue_date"):
        df[c] = pd.to_datetime(df[c])
    return df


CTX = RuleContext(source="plt09", masters=_masters(), mapped=frozenset({"lot_no"}))


def test_a_clean_row_raises_nothing() -> None:
    df = _frame(_row())
    assert {r.id: r.check(df, CTX).rows for r in RULES} == {r.id: [] for r in RULES}


@pytest.mark.parametrize(
    ("rule", "row", "evidence"),
    [
        ("V001", _row(customer_no=None, ordered_qty=None), {"missing": ["customer_no", "ordered_qty"]}),
        ("V002", _row(issue_date="2025-12-30"), {"issue_date": "2025-12-30"}),
        ("V002", _row(confirmed_delivery_date="2025-12-01"), {"confirmed_delivery_date": "2025-12-01"}),
        ("V003", _row(invoiced_weight_lb=21.5), {"invoiced_weight_lb": 21.5}),
        ("V004", _row(customer_no="C999999"), {"customer_no": "C999999"}),
        ("V005", _row(item_no="IT9"), {"item_no": "IT9"}),
        ("V006", _row(issue_date="2026-01-11"),
         {"problem": "shipped after expiry", "expiry_date": "2026-01-10"}),
        ("V006", _row(lot_no="L404"), {"problem": "lot not in the lot master"}),
        ("V006", _row(lot_no=None), {"problem": "lot number blank"}),
        ("V007", _row(invoiced_qty=-2.0), {"invoiced_qty": -2.0}),
        ("V009", _row(uom="LB", ordered_weight_lb=9.07), {"likely_unit": "kg", "master_uom": "CASE"}),
        ("V010", _row(order_date=None, parse_errors={"order_date": "31/31/2026"}),
         {"unparsed": {"order_date": "31/31/2026"}}),
        ("V011", _row(ordered_qty=0.0), {"ordered_qty": 0.0}),
    ],
)
def test_each_rule_rejects_its_seeded_shape(rule: str, row: dict, evidence: dict) -> None:
    violations = BY_ID[rule].check(_frame(row), CTX)
    assert violations.rows == [0]
    assert evidence.items() <= violations.details[0].items()


def test_v001_does_not_double_report_a_value_v010_owns() -> None:
    df = _frame(_row(order_date=None, parse_errors={"order_date": "garbage"}))
    assert BY_ID["V001"].check(df, CTX).rows == []


def test_v006_skips_rows_from_a_header_without_a_lot_column() -> None:
    df = _frame(_row(lot_no=None))
    assert BY_ID["V006"].check(df, RuleContext("plt09", _masters(), mapped=frozenset())).rows == []


def test_v008_queues_the_repeat_not_the_first() -> None:
    violations = BY_ID["V008"].check(_frame(_row(), _row(), _row(line_no=2)), CTX)
    assert violations.rows == [1] and violations.details[0]["first_row"] == 1


def test_quirks_are_not_exceptions() -> None:
    """Missing invoiced weight (PLT-02's ambient quirk) and a missing lot where the header has none."""
    df = _frame(_row(invoiced_weight_lb=None))
    assert all(r.check(df, CTX).rows == [] for r in RULES)


# --- full load of all three plants ------------------------------------------------------


@pytest.mark.postgres
def test_exception_recall_and_precision_meet_target(silver_db: Engine, loaded: Loaded) -> None:
    with silver_db.connect() as conn:
        result = score(conn, DATA)
    by_rule = {r.rule: r for r in result.rules}
    assert result.sources == ["plt01", "plt02", "plt03"]
    missed = [(r.rule, r.caught, r.detectable) for r in result.rules if r.recall < 1.0]
    assert not missed, missed
    v006 = by_rule["V006"]
    assert (v006.seeded, v006.detectable, v006.answer_key_detectable) == (50, 32, 33)
    assert result.recall >= TARGET and result.row_precision >= TARGET
    # the only exceptions on rows without a seeded fault: two lots genuinely shipped after their expiry date
    assert sorted((u["rule"], u["row_key"]) for u in result.unexplained) == [
        ("V006", "SO0009044|1"), ("V006", "SO0012682|1")]


@pytest.mark.postgres
def test_revalidation_adds_nothing_and_resolves_what_no_longer_applies(
        silver_db: Engine, loaded: Loaded) -> None:
    masters = load_masters(DATA / "master")
    with silver_db.begin() as conn:
        batch_id = str(conn.execute(text("SELECT batch_id FROM ops.exceptions WHERE rule_id = 'V007' "
                                         "AND status = 'open' LIMIT 1")).scalar_one())
        before = conn.execute(text("SELECT count(*) FROM ops.exceptions WHERE batch_id = :b"),
                              {"b": batch_id}).scalar_one()
        validate_batch(conn, batch_id, masters)
        assert conn.execute(text("SELECT count(*) FROM ops.exceptions WHERE batch_id = :b"),
                            {"b": batch_id}).scalar_one() == before

        # simulate a corrected source value, then re-validate: that exception closes, it is not deleted
        target = conn.execute(text("SELECT exception_id, source_row FROM ops.exceptions WHERE batch_id = :b "
                                   "AND rule_id = 'V007' AND status = 'open' LIMIT 1"),
                              {"b": batch_id}).one()
        conn.execute(text("UPDATE silver.order_lines SET ordered_qty = abs(ordered_qty), "
                          "invoiced_qty = abs(invoiced_qty) WHERE batch_id = :b AND source_row = :r"),
                     {"b": batch_id, "r": target.source_row})
        validate_batch(conn, batch_id, masters)
        closed = conn.execute(text("SELECT status, resolved_by FROM ops.exceptions WHERE exception_id = :id"),
                              {"id": target.exception_id}).one()
        assert (closed.status, closed.resolved_by) == ("resolved", "system:revalidation")
        conn.rollback()


@pytest.mark.postgres
def test_every_validated_batch_records_all_rules_on_all_rows(silver_db: Engine, loaded: Loaded) -> None:
    with silver_db.connect() as conn:
        rows = conn.execute(text(
            "SELECT b.batch_id, (b.response->'validation'->>'rows_validated')::int AS validated, "
            "b.response->'validation'->'rules_run' AS rules, count(s.*) AS silver "
            "FROM ops.batches b JOIN silver.order_lines s USING (batch_id) GROUP BY 1, 2, 3")).all()
    assert len(rows) == 54
    assert all(r.validated == r.silver and r.rules == [x.id for x in RULES] for r in rows)
