"""
Tests for the synthetic data generator.

These guard the property that everything downstream depends on: the ground
truth must actually be true. A generator whose anomalies aren't in the data
would silently invalidate every evaluation in both projects.
"""
import json
from datetime import timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from banned_terms import BANNED_RE

from synth import config as C
from synth import world

DATA = Path(__file__).resolve().parents[1] / "data"
pytestmark = pytest.mark.skipif(
    not (DATA / "ground_truth" / "manifest.json").exists(),
    reason="run `python -m synth.generate --out data` first",
)


@pytest.fixture(scope="module")
def manifest():
    return json.loads((DATA / "ground_truth" / "manifest.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def anomalies():
    return json.loads((DATA / "ground_truth" / "anomalies.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def faults():
    return json.loads((DATA / "ground_truth" / "faults.json").read_text(encoding="utf-8"))


# --- ground truth integrity -------------------------------------------------

def test_self_check_passed(manifest):
    assert manifest["self_check"] == "passed"


def test_every_expected_anomaly_has_observed_effect(anomalies):
    for a in anomalies["anomalies"]:
        assert a["observed"], f"{a['anomaly_id']} has no observed evidence"
        assert "error" not in a["observed"], a["observed"]


def test_anomaly_directions(anomalies):
    """Rate metrics fall, age and backlog rise."""
    expect_down = {"A1", "A2", "A3", "A5"}
    for a in anomalies["anomalies"]:
        delta = a["observed"]["delta"]
        if a["anomaly_id"] in expect_down:
            assert delta < 0, f"{a['anomaly_id']} should fall, got {delta}"
        else:
            assert delta > 0, f"{a['anomaly_id']} should rise, got {delta}"


def test_decoy_is_a_real_dip(anomalies):
    """
    A5 must be genuinely present in the data. A decoy that isn't there tests
    nothing — the detector must distinguish it by seasonality, not by absence.
    """
    a5 = next(a for a in anomalies["anomalies"] if a["anomaly_id"] == "A5")
    assert a5["expect_detection"] is False
    assert a5["observed"]["delta"] < -0.05


def test_decoy_window_is_inside_holiday_blackout():
    """
    A holiday-aware detector must be able to suppress A5 cleanly. If the window
    extends past the known holidays, the decoy becomes unavoidable and unfairly
    penalises a correct detector.
    """
    a5 = next(a for a in C.ANOMALIES if a.anomaly_id == "A5")
    blackout = set()
    for h in C.KNOWN_HOLIDAYS:
        for off in range(-2, 3):
            blackout.add(h + timedelta(days=off))
    d = a5.start
    while d <= a5.end:
        assert d in blackout, f"A5 day {d} is not covered by a holiday blackout"
        d += timedelta(days=1)


def test_fault_row_keys_unique_per_rule(faults):
    pairs = [(f["row_key"], f["rule_id"]) for f in faults["faults"]]
    assert len(pairs) == len(set(pairs))


def test_fault_counts_match_plan(faults):
    for rid, meta in faults["rule_catalogue"].items():
        assert meta["actual_count"] == meta["planned_count"], rid


def test_achievable_ceiling_present_and_honest(faults):
    c = faults["achievable_ceiling"]
    assert c["V006_lot_after_expiry"]["ceiling"] < 1.0
    assert c["V006_lot_after_expiry"]["blocked_reason"]
    for rid, meta in c.items():
        if rid != "V006_lot_after_expiry":
            assert meta["ceiling"] == 1.0, rid


def test_achievable_ceiling_order_is_deterministic(faults):
    """Keys were iterated from a set, so byte order changed with PYTHONHASHSEED."""
    assert list(faults["achievable_ceiling"]) == sorted(faults["achievable_ceiling"])


def test_faults_do_not_collide_with_anomalies(faults):
    """
    Project A faults and Project B anomalies must be disjoint, or each corrupts
    the other's scoring.
    """
    fd = pd.read_parquet(DATA / "gold" / "fact_delivery.parquet")
    fd["_row_key"] = fd["order_no"] + "|" + fd["line_no"].astype(str)
    anomalous = set(fd.loc[fd["_anomaly_id"] != "", "_row_key"])
    faulty = {f["row_key"] for f in faults["faults"]}
    assert not (anomalous & faulty)


# --- metric correctness -----------------------------------------------------

def test_otif_recomputed_by_hand_matches_gold():
    """
    Recompute OTIF for one week from the raw fact table, independently of the
    metric layer, and check the two agree. Catches definition drift.
    """
    fd = pd.read_parquet(DATA / "gold" / "fact_delivery.parquet")
    fd["issue_date"] = pd.to_datetime(fd["issue_date"])
    week = fd[(fd["issue_date"] >= "2026-02-02") & (fd["issue_date"] <= "2026-02-08")]
    assert len(week) > 100

    on_time = week["issue_date"] <= pd.to_datetime(week["confirmed_delivery_date"])
    in_full = np.where(
        week["catch_weight_flag"],
        week["invoiced_weight_lb"] >= week["ordered_weight_lb"] * (1 - C.WEIGHT_TOLERANCE),
        week["invoiced_qty"] >= week["ordered_qty"],
    )
    expected = float((on_time & in_full).mean())
    assert abs(expected - float(week["otif"].mean())) < 1e-9


def test_catch_weight_items_use_weight_basis():
    """The A2 anomaly is only visible on the weight basis — prove the distinction exists."""
    fd = pd.read_parquet(DATA / "gold" / "fact_delivery.parquet")
    cw = fd[fd["catch_weight_flag"]]
    disagree = cw[
        (cw["invoiced_qty"] >= cw["ordered_qty"])
        & (cw["invoiced_weight_lb"] < cw["ordered_weight_lb"] * (1 - C.WEIGHT_TOLERANCE))
    ]
    assert len(disagree) > 0, "no rows where count and weight bases disagree"


def test_baseline_otif_is_plausible(manifest):
    assert 0.85 <= manifest["baseline_otif_rate"] <= 0.95


# --- source extracts --------------------------------------------------------

def test_each_plant_uses_its_own_format():
    p1 = pd.read_csv(DATA / "sources/plt01/plt-01_orderlines_2026-02.csv", dtype=str)
    p2 = pd.read_csv(DATA / "sources/plt02/plt-02_orderlines_2026-02.csv", dtype=str)
    p3 = pd.read_csv(DATA / "sources/plt03/plt-03_orderlines_2026-02.csv", dtype=str)
    assert p1["ord_dt"].iloc[0].count("-") == 2          # ISO
    assert p2["DT_ORDER"].iloc[0].count("/") == 2        # US
    assert p3["OrderDate"].iloc[0].isdigit()             # Excel serial
    assert p1["cust_no"].iloc[0].startswith("C")
    assert p2["cust_no"].iloc[0].isdigit()
    assert p3["Customer"].iloc[0].startswith("CUST-")


def test_plt02_weights_are_in_kg():
    """PLT-02 reports kg; the same item shipped elsewhere should weigh ~2.2x more in lb."""
    p2 = pd.read_csv(DATA / "sources/plt02/plt-02_orderlines_2026-02.csv")
    p1 = pd.read_csv(DATA / "sources/plt01/plt-01_orderlines_2026-02.csv")
    lb_per_case_p1 = (p1["ord_wt"] / p1["ord_qty"]).median()
    kg_per_case_p2 = (p2["WT_ORD_KG"] / p2["QTY_ORD"]).median()
    assert 1.8 < lb_per_case_p1 / kg_per_case_p2 < 2.6


def test_drift_d1_renames_column():
    before = pd.read_csv(DATA / "sources/plt02/plt-02_orderlines_2026-02.csv", nrows=0)
    after = pd.read_csv(DATA / "sources/plt02/plt-02_orderlines_2026-04.csv", nrows=0)
    assert "cust_no" in before.columns and "customer_number" not in before.columns
    assert "customer_number" in after.columns and "cust_no" not in after.columns


def test_drift_d2_adds_column():
    before = pd.read_csv(DATA / "sources/plt01/plt-01_orderlines_2026-05.csv", nrows=0)
    after = pd.read_csv(DATA / "sources/plt01/plt-01_orderlines_2026-07.csv", nrows=0)
    assert "lot_no" not in before.columns
    assert "lot_no" in after.columns


# --- the two BI tools -------------------------------------------------------

@pytest.fixture(scope="module")
def recon():
    return json.loads((DATA / "ground_truth" / "reconciliation.json").read_text(encoding="utf-8"))


def test_tool1_feed_shape():
    feed = pd.read_csv(DATA / "report_feed/tool1_legacy_delivery_report.csv")
    assert list(feed.columns) == list(C.REPORT_FEED_COLUMNS)
    assert set(feed["On Time"].unique()) <= {"Y", "N"}
    assert feed["Case Fill Rate max. 100%"].max() <= 1.0


def test_tool2_feed_is_a_different_shape():
    """A second tool would not hand you the same file. Different grain, columns, rounding."""
    feed = pd.read_csv(DATA / "report_feed/tool2_dashboard_otif_export.csv")
    assert "OTIF_Pct" in feed.columns and "Facility" in feed.columns
    assert "Order Line Number" not in feed.columns
    assert len(feed) < 5000  # aggregated, not line level


def test_tool2_excludes_consignment_orders():
    t1 = pd.read_csv(DATA / "report_feed/tool1_legacy_delivery_report.csv")
    assert "CONS" in set(t1["Customer Order Type"])
    recon_t = json.loads((DATA / "ground_truth" / "reconciliation.json").read_text(encoding="utf-8"))
    dropped = sum(m["cause_contributions"]["scope_filter_lines_dropped"]
                  for m in recon_t["monthly"])
    assert dropped > 1000, "tool2's undocumented scope filter should drop real volume"


def test_tools_disagree_with_governed_and_each_other(recon):
    g1 = recon["overall"]["tool1_gap_vs_governed"]
    g2 = recon["overall"]["tool2_gap_vs_governed"]
    assert abs(g1) > 0.001, "tool1 must differ from governed"
    assert abs(g2) > 0.05, "tool2 must differ materially from governed"
    assert abs(g1 - g2) > 0.05, "the two tools must differ from each other"


def test_date_basis_dominates_tool2_gap(recon):
    """Tool 2's gap must be attributable, and mainly to the requested-date basis."""
    means = {
        k: sum(m["cause_contributions"][k] for m in recon["monthly"]) / len(recon["monthly"])
        for k in ("weight_basis", "date_basis", "case_tolerance")
    }
    assert abs(means["date_basis"]) > abs(means["weight_basis"])
    assert abs(means["date_basis"]) > abs(means["case_tolerance"])


def test_legacy_tool_is_blind_to_the_A2_weight_anomaly(recon):
    """
    The sharpest lesson in the dataset: A2 is a catch-weight shortfall, so a tool
    measuring in-full on CASE COUNT cannot see it. Its gap versus the governed
    definition should widen sharply in the A2 month while the governed number
    drops. This ties Project A's reconciliation to Project B's detection: the
    same event, invisible to one definition.
    """
    by_month = {m["month"]: m for m in recon["monthly"]}
    a2_month, before, after = by_month["2025-11"], by_month["2025-10"], by_month["2025-12"]

    assert a2_month["governed_otif"] < before["governed_otif"] - 0.02, \
        "governed OTIF should fall during A2"
    assert a2_month["tool1_gap"] > 3 * before["tool1_gap"], \
        "the count-basis tool's gap should widen sharply during A2"
    assert a2_month["tool1_gap"] > 3 * after["tool1_gap"]
    assert a2_month["tool1_otif"] > a2_month["governed_otif"], \
        "the count-basis tool should report a rosier number than the truth"


def test_cause_contributions_are_not_additive(recon):
    """
    Guard the note in the ground truth: single-switch deltas interact and must
    not be presented as an additive decomposition.
    """
    m = next(x for x in recon["monthly"] if x["month"] == "2026-01")
    c = m["cause_contributions"]
    naive_sum = c["weight_basis"] + c["date_basis"] + c["case_tolerance"]
    assert abs(naive_sum - m["tool2_gap"]) > 1e-6


# --- determinism ------------------------------------------------------------

def test_masters_are_deterministic():
    rng_a = np.random.default_rng([C.SEED, 1])
    rng_b = np.random.default_rng([C.SEED, 1])
    pd.testing.assert_frame_equal(
        world.build_customers(rng_a), world.build_customers(rng_b)
    )


def test_company_name_is_consistent_everywhere():
    """
    The company name is a single constant. This catches the failure mode where a
    rename leaves a stale hardcoded name in a generated artifact.
    """
    feed = pd.read_csv(DATA / "report_feed/tool1_legacy_delivery_report.csv", nrows=5)
    assert feed["Company Name"].iloc[0] == C.COMPANY_NAME
    dataset_doc = (DATA / "DATASET.md").read_text(encoding="utf-8")
    assert C.COMPANY_NAME in dataset_doc
    assert "fictional" in dataset_doc.lower()


def test_reserved_anomaly_customer_exists():
    cust = pd.read_csv(DATA / "master/customers.csv")
    row = cust[cust["customer_no"] == "C000031"]
    assert len(row) == 1
    assert row["channel"].iloc[0] == "RETAIL"


def test_no_real_company_names_in_output():
    """Guard the IP rule: the fictional customer must be the only company named."""
    files = [DATA / "DATASET.md", *(DATA / "ground_truth").glob("*.json"), *(DATA / "kb").rglob("*.md"),
             *(DATA / "kb" / "resolutions").glob("*.json"), *(DATA / "report_feed").glob("*.csv")]
    for f in files:
        match = BANNED_RE.search(f.read_text(encoding="utf-8"))
        assert match is None, f"{match.group(0)!r} found in {f}"


# --- knowledge base ---------------------------------------------------------

@pytest.fixture(scope="module")
def kb_questions():
    return json.loads((DATA / "ground_truth" / "kb_questions.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def kb_manifest():
    return json.loads((DATA / "ground_truth" / "kb_manifest.json").read_text(encoding="utf-8"))


def test_every_document_file_exists(kb_manifest):
    for d in kb_manifest["documents"]:
        assert (DATA / "kb" / "docs" / f"{d['doc_id']}.md").exists()


def test_corpus_has_distractors(kb_manifest):
    """
    A corpus where every document is relevant makes retrieval succeed by having
    nowhere else to go. Most real documents are plausible and wrong.
    """
    distractors = [d for d in kb_manifest["documents"]
                   if "distractor" in d.get("challenges", [])]
    assert len(distractors) >= 6
    assert len(distractors) / len(kb_manifest["documents"]) > 0.2


def test_superseded_pair_exists(kb_manifest):
    by_id = {d["doc_id"]: d for d in kb_manifest["documents"]}
    old, new = by_id["SOP-DQ-001-v1"], by_id["SOP-DQ-001-v2"]
    assert old["status"] == "superseded"
    assert old["superseded_by"] == new["doc_id"]
    assert new["status"] == "current"


def test_superseded_and_current_actually_conflict():
    """The version pair is worthless unless the two versions give different answers."""
    v1 = (DATA / "kb/docs/SOP-DQ-001-v1.md").read_text(encoding="utf-8")
    v2 = (DATA / "kb/docs/SOP-DQ-001-v2.md").read_text(encoding="utf-8")
    assert "10 business days" in v1 and "written off" in v1
    assert "3 business days" in v2 and "never discarded" in v2


def test_correction_memo_contradicts_source_note():
    """C2 requires a genuine factual contradiction, not a rewording."""
    note = (DATA / "kb/docs/SRC-PLT02-001.md").read_text(encoding="utf-8")
    memo = (DATA / "kb/docs/MEMO-2025-11.md").read_text(encoding="utf-8")
    assert "in pounds" in note
    assert "kilograms" in memo and "incorrect" in memo.lower()
    assert "precedence" in memo  # wrapped across a line in the markdown


def test_access_controlled_documents_exist(kb_manifest):
    levels = {d["access_level"] for d in kb_manifest["documents"]}
    assert "restricted" in levels and "plant" in levels
    plant_docs = [d for d in kb_manifest["documents"] if d["access_level"] == "plant"]
    assert {d["plant"] for d in plant_docs} == {"PLT-01", "PLT-02", "PLT-03"}


def test_permission_questions_come_in_matched_pairs(kb_questions):
    """
    Every permission denial must have an authorised twin. Testing only denials
    rewards a system that refuses everything.
    """
    perms = [q for q in kb_questions["questions"] if q["category"] == "permission"]
    refuse = [q for q in perms if q["should_refuse"]]
    allow = [q for q in perms if not q["should_refuse"]]
    assert refuse and allow, "need both denial and authorised cases"
    texts_refused = {q["question"] for q in refuse}
    texts_allowed = {q["question"] for q in allow}
    assert texts_refused & texts_allowed, \
        "at least one identical question must appear with both askers"


def test_refusal_questions_have_no_expected_docs(kb_questions):
    for q in kb_questions["questions"]:
        if q["should_refuse"] and q["category"] == "refusal":
            assert not q["expected_doc_ids"], \
                f"{q['question_id']} should refuse but names expected docs"


def test_all_ten_challenges_are_exercised(kb_questions, kb_manifest):
    covered = {q["challenge"] for q in kb_questions["questions"]}
    declared = set(kb_manifest["challenges"])
    assert declared <= covered | {"distractor"}, \
        f"declared but untested: {declared - covered}"
    assert len(covered & declared) >= 9


def test_expected_and_forbidden_doc_ids_are_real(kb_questions, kb_manifest):
    known = {d["doc_id"] for d in kb_manifest["documents"]}
    for q in kb_questions["questions"]:
        for d in q["expected_doc_ids"] + q["forbidden_doc_ids"]:
            assert d in known, f"{q['question_id']} references unknown doc {d}"


def test_decoy_shutdown_memo_supports_the_A5_anomaly():
    """
    The RAG counterpart to the A5 decoy: the brief must be able to EXPLAIN the
    July dip as an announced shutdown, not just decline to escalate it.
    """
    memo = (DATA / "kb/docs/MEMO-2026-06.md").read_text(encoding="utf-8")
    a5 = next(a for a in C.ANOMALIES if a.anomaly_id == "A5")
    assert str(a5.start) in memo or "2 July 2026" in memo
    assert "expected and normal" in memo
    policy = (DATA / "kb/docs/SOP-OPS-001.md").read_text(encoding="utf-8")
    assert "not" in policy and "escalation trigger" in policy


def test_prior_resolutions_cover_the_seeded_rules(faults):
    res = json.loads((DATA / "kb/resolutions/prior_resolutions.json").read_text(encoding="utf-8"))
    rules_with_resolutions = {r["rule_id"] for r in res}
    seeded_rules = set(faults["rule_catalogue"])
    missing = seeded_rules - rules_with_resolutions
    assert not missing, f"no prior resolutions for {missing}"


def test_resolution_classifications_match_the_agent_outcomes():
    res = json.loads((DATA / "kb/resolutions/prior_resolutions.json").read_text(encoding="utf-8"))
    got = {r["classification"] for r in res}
    assert got == {"auto_fixable", "needs_master_data", "source_defect", "escalate"}
