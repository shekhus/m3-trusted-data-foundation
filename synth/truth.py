"""
Write the ground_truth/ files, then PROVE they are true.

The self-check is the part that matters. It recomputes each seeded anomaly's
metric at the grain it was planted at, compares the anomaly window against a
baseline period, and asserts the effect is both present and in the expected
direction. If an anomaly does not show up, generation FAILS loudly rather than
shipping a dataset whose ground truth is a lie.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import timedelta
from pathlib import Path
from typing import Any

import pandas as pd

from . import config as C

# ---------------------------------------------------------------------------
# writers
# ---------------------------------------------------------------------------

def write_mappings(out: Path, plant_files: dict[str, list[str]]) -> None:
    """
    The true source-column -> canonical-column mapping per source, including the
    drift variants. This is what Project A's mapping accuracy is scored against.
    """
    payload: dict[str, Any] = {
        "canonical_schema": C.CANONICAL_ORDER_LINE,
        "sources": {},
        "notes": [
            "Column names are those of the synthetic extracts, not Infor M3 "
            "physical field names. Mapping to real M3 columns must be confirmed "
            "with an M3 SME before any client use.",
            "PLT-02 weights are recorded in kilograms across the whole file; the "
            "canonical target is pounds. Conversion is a normalisation, not a fault.",
        ],
    }
    for plant, mapping in C.SOURCE_COLUMN_MAP.items():
        entry: dict[str, Any] = {
            "files": plant_files.get(plant, []),
            "date_format": C.PLANT_QUIRKS[plant]["date_format"],
            "weight_unit": C.PLANT_QUIRKS[plant]["weight_unit"],
            "customer_format": C.PLANT_QUIRKS[plant]["customer_format"],
            "column_map": mapping,
            "drift_variants": [],
        }
        if plant == "PLT-02":
            variant = dict(mapping)
            variant["customer_number"] = variant.pop("cust_no")
            entry["drift_variants"].append(
                {"effective_from": str(C.DRIFT_EVENTS[0].effective),
                 "drift_id": "D1", "column_map": variant}
            )
        if plant == "PLT-01":
            before = {k: v for k, v in mapping.items() if k != "lot_no"}
            entry["column_map"] = before
            entry["drift_variants"].append(
                {"effective_from": str(C.DRIFT_EVENTS[1].effective),
                 "drift_id": "D2", "column_map": mapping}
            )
        payload["sources"][plant] = entry

    (out / "mappings.json").write_text(json.dumps(payload, indent=2), encoding="utf-8", newline="\n")


def _achievable_ceiling(ledger: list[dict]) -> dict[str, dict]:
    """
    Not every seeded fault is detectable from the data the sources actually send.

    PLT-01 does not emit lot_no until drift event D2 (2026-06-01), so an
    expired-lot fault on a PLT-01 row shipped before that date cannot be checked
    by any pipeline — the field is simply absent. Recording the ceiling here
    means recall is scored against what is *possible*, not against what was
    planted. A pipeline reporting 100% on V006 would be reporting a bug.

    This is the honest version of coverage reporting, and it is the same
    conversation an FDE has with a client: "we cannot validate what you do not
    send us, and here is exactly how much that costs you."
    """
    d2 = next(e for e in C.DRIFT_EVENTS if e.drift_id == "D2")
    ceilings: dict[str, dict] = {}

    # sorted: set order depends on PYTHONHASHSEED and would break byte-identical output
    for rule_id in sorted({f["rule_id"] for f in ledger}):
        rows = [f for f in ledger if f["rule_id"] == rule_id]
        if rule_id == "V006_lot_after_expiry":
            blocked = [
                f for f in rows
                if f["plant"] == d2.source and str(f["issue_date"]) < str(d2.effective)
            ]
            ceilings[rule_id] = {
                "seeded": len(rows),
                "detectable": len(rows) - len(blocked),
                "ceiling": round((len(rows) - len(blocked)) / len(rows), 4),
                "blocked_reason": (
                    f"{len(blocked)} faults sit on {d2.source} rows shipped before "
                    f"{d2.effective}, when the source carried no lot_no column. "
                    "Undetectable by design — report coverage, do not claim recall."
                ),
            }
        else:
            ceilings[rule_id] = {
                "seeded": len(rows), "detectable": len(rows), "ceiling": 1.0,
                "blocked_reason": None,
            }
    return ceilings


def write_faults(out: Path, ledger: list[dict]) -> None:
    by_rule: dict[str, int] = {}
    for f in ledger:
        by_rule[f["rule_id"]] = by_rule.get(f["rule_id"], 0) + 1

    ceilings = _achievable_ceiling(ledger)

    payload = {
        "rule_catalogue": {
            rid: {"description": desc, "planned_count": n, "actual_count": by_rule.get(rid, 0)}
            for rid, (desc, n) in C.FAULT_PLAN.items()
        },
        "total_faults": len(ledger),
        "achievable_ceiling": ceilings,
        "scoring": {
            "recall": "seeded faults caught / total seeded faults, per rule_id",
            "V006_note": "detectable only by joining order lines to "
                         "master/lot_master.csv on lot_no and comparing "
                         "expiry_date against issue_date",
            "score_against": "achievable_ceiling.detectable, NOT seeded. See "
                             "achievable_ceiling for rules where the source data "
                             "makes full recall impossible.",
            "precision": "exceptions raised on rows with no seeded fault count as "
                         "false positives, EXCEPT rows carrying ambient quirks "
                         "(kg weights, date formats, trailing spaces, missing "
                         "weights) which a correct pipeline normalises without "
                         "raising an exception",
        },
        "faults": ledger,
    }
    (out / "faults.json").write_text(json.dumps(payload, indent=2), encoding="utf-8", newline="\n")


def write_anomalies(out: Path, evidence: dict[str, dict]) -> None:
    payload = {
        "scoring": {
            "recall": "seeded anomalies with expect_detection=true that the "
                      "detector flags at WARN or above, within the window",
            "precision": "flags outside any seeded window count as false "
                         "positives; A5 flagged at HIGH counts as a false positive",
            "lead_time": "days from anomaly start to first flag",
        },
        "anomalies": [
            {**asdict(a), "start": str(a.start), "end": str(a.end),
             "observed": evidence.get(a.anomaly_id, {})}
            for a in C.ANOMALIES
        ],
    }
    (out / "anomalies.json").write_text(
        json.dumps(payload, indent=2, default=str), encoding="utf-8", newline="\n"
    )


def write_drift(out: Path) -> None:
    payload = {
        "events": [
            {**asdict(e), "effective": str(e.effective)} for e in C.DRIFT_EVENTS
        ],
        "scoring": {
            "detected": "fingerprint change detected in the first batch on or "
                        "after effective date",
            "named_correctly": "alert names the broken source column AND the "
                               "affected canonical column(s) via lineage",
        },
    }
    (out / "drift.json").write_text(json.dumps(payload, indent=2), encoding="utf-8", newline="\n")


# ---------------------------------------------------------------------------
# self-check
# ---------------------------------------------------------------------------

def _window_vs_baseline(
    df: pd.DataFrame, date_col: str, value_col: str, a: C.Anomaly,
    weight_col: str | None = None, baseline_days: int = 56,
) -> dict:
    d = df.copy()
    d[date_col] = pd.to_datetime(d[date_col]).dt.date

    win = d[(d[date_col] >= a.start) & (d[date_col] <= a.end)]
    base = d[
        (d[date_col] >= a.start - timedelta(days=baseline_days))
        & (d[date_col] < a.start)
    ]
    if win.empty or base.empty:
        return {"error": "no rows in window or baseline", "window_rows": len(win),
                "baseline_rows": len(base)}

    def agg(x: pd.DataFrame) -> float:
        if weight_col:
            w = x[weight_col].sum()
            return float((x[value_col] * x[weight_col]).sum() / w) if w else float("nan")
        return float(x[value_col].mean())

    wv, bv = agg(win), agg(base)
    return {
        "window_value": round(wv, 5),
        "baseline_value": round(bv, 5),
        "delta": round(wv - bv, 5),
        "window_rows": int(len(win)),
        "baseline_rows": int(len(base)),
    }


def self_check(metrics: dict[str, pd.DataFrame]) -> tuple[dict[str, dict], list[str]]:
    """
    Verify every seeded anomaly is measurably present. Returns (evidence, failures).
    """
    evidence: dict[str, dict] = {}
    failures: list[str] = []

    def check(aid: str, res: dict, direction: str, min_abs: float) -> None:
        evidence[aid] = res
        if "error" in res:
            failures.append(f"{aid}: {res['error']}")
            return
        delta = res["delta"]
        ok_dir = delta < 0 if direction == "down" else delta > 0
        if not (ok_dir and abs(delta) >= min_abs):
            failures.append(
                f"{aid}: expected {direction} shift of at least {min_abs}, got {delta}"
            )

    a = {x.anomaly_id: x for x in C.ANOMALIES}

    # A1 — OTIF on the PLT-02 > C000031 lane should drop materially
    lane = metrics["daily_otif_lane"]
    lane_sel = lane[
        (lane["plant"] == a["A1"].segment["plant"])
        & (lane["customer_no"] == a["A1"].segment["customer_no"])
    ]
    check("A1", _window_vs_baseline(lane_sel, "metric_date", "otif_rate", a["A1"],
                                    weight_col="lines"), "down", 0.15)

    # A2 — weight fill rate for CASE-READY at PLT-01 should drop
    fpg = metrics["daily_fill_plant_group"]
    fpg_sel = fpg[
        (fpg["plant"] == a["A2"].segment["plant"])
        & (fpg["product_group"] == a["A2"].segment["product_group"])
    ]
    check("A2", _window_vs_baseline(fpg_sel, "metric_date", "fill_rate_weight", a["A2"],
                                    weight_col="ordered_weight_lb"), "down", 0.04)

    # A3 — yield variance on PLT-03 L2 PRIMALS should go sharply negative
    dy = metrics["daily_yield"]
    dy_sel = dy[
        (dy["plant"] == a["A3"].segment["plant"])
        & (dy["line"] == a["A3"].segment["line"])
        & (dy["product_group"] == a["A3"].segment["product_group"])
    ]
    check("A3", _window_vs_baseline(dy_sel, "metric_date", "yield_variance_pct", a["A3"]),
          "down", 3.0)

    # A4 — mean inventory age at DC-EAST should rise
    inv = metrics["daily_inventory_location"]
    inv_sel = inv[inv["location"] == a["A4"].segment["location"]]
    check("A4", _window_vs_baseline(inv_sel, "metric_date", "inventory_age_days", a["A4"]),
          "up", 1.0)

    # A5 — the decoy must be REAL in the data (a genuine dip) so that the
    # detector's seasonality awareness is what distinguishes it, not its absence
    tot = metrics["daily_otif_total"]
    check("A5", _window_vs_baseline(tot, "metric_date", "otif_rate", a["A5"],
                                    weight_col="lines", baseline_days=28), "down", 0.05)

    # A6 — open backlog at PLT-02 should rise
    bl = metrics["daily_backlog_plant"]
    bl_sel = bl[bl["plant"] == a["A6"].segment["plant"]]
    check("A6", _window_vs_baseline(bl_sel, "metric_date", "open_backlog_lines", a["A6"]),
          "up", 20.0)

    return evidence, failures


# ---------------------------------------------------------------------------
# knowledge base ground truth
# ---------------------------------------------------------------------------

CHALLENGE_DESCRIPTIONS = {
    "C1": "Superseded versions — v1 and v2 of the same SOP both sit in the "
          "corpus. Retrieval must prefer the current version, and answering "
          "from the superseded one is a specific, detectable failure.",
    "C2": "Contradictory documents — a source note is factually wrong and a "
          "later memo corrects it, stating that it takes precedence. Recency "
          "alone is not enough; the memo says so explicitly and the system "
          "should surface the conflict rather than silently pick one.",
    "C3": "Access control — restricted and plant-scoped documents. Filtering "
          "must happen before the model call, not after generation. "
          "Over-restriction is also a failure: an authorised asker must get "
          "the answer.",
    "C4": "Stale content — an expired temporary process. Expired does not mean "
          "useless: it is wrong for today and right for the period it covered, "
          "so the system must distinguish a current question from a historical "
          "one.",
    "C5": "Unanswerable questions — the corpus holds definitions and policy, "
          "not metric values or commercial data. The correct behaviour is to "
          "refuse, including for authorised askers, and including when a "
          "topically adjacent document is available to improvise from.",
    "C6": "Terminology mismatch — queries use acronyms and industry synonyms "
          "('OTIF', 'random weight', 'poundage') that the documents spell out "
          "differently. Purely lexical retrieval degrades here; this is where "
          "embeddings earn their place.",
    "C7": "Tables — several answers live inside markdown tables. A chunking "
          "strategy that splits a table from its header, or a row from its "
          "column meaning, produces confidently wrong answers.",
    "C8": "Near-duplicates — three plant profiles share heavy boilerplate and "
          "differ in one paragraph. Returning all three for a "
          "facility-specific question is a failure even though one is right.",
    "C9": "Cross-document synthesis — the answer requires two documents, and "
          "the second one changes the conclusion (a strategic account forcing "
          "a higher escalation tier; a shutdown notice turning a breach into "
          "expected variation).",
    "C10": "Long documents — the answer is one section or one sentence of a "
           "long policy, often the version history at the very end, which is "
           "the part naive chunking most often truncates.",
}


def write_kb_truth(out: Path, docs: list[dict], resolutions: list[dict],
                   questions: list[dict]) -> None:
    by_challenge: dict[str, int] = {}
    by_category: dict[str, int] = {}
    for q in questions:
        by_challenge[q["challenge"]] = by_challenge.get(q["challenge"], 0) + 1
        by_category[q["category"]] = by_category.get(q["category"], 0) + 1

    manifest = {
        "purpose": (
            "A knowledge corpus of clean, consistent documents teaches nothing. "
            "Ten challenges are engineered in, each with golden questions that "
            "can only be answered correctly if the challenge is handled."
        ),
        "challenges": CHALLENGE_DESCRIPTIONS,
        "documents": [
            {k: v for k, v in d.items() if k != "body"} for d in docs
        ],
        "document_count": len(docs),
        "resolution_count": len(resolutions),
        "resolution_classifications": sorted(
            {r["classification"] for r in resolutions}
        ),
    }
    (out / "kb_manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8", newline="\n")

    payload = {
        "scoring": {
            "retrieval_recall": "expected_doc_ids present in the retrieved set, "
                                "reported PER CHALLENGE — a single aggregate "
                                "score hides which failure mode is active",
            "precision": "forbidden_doc_ids must NOT appear in the retrieved "
                         "set used to answer",
            "answer_correctness": "must_contain present and must_not_contain "
                                  "absent in the generated answer",
            "refusal": "should_refuse questions must produce a refusal; "
                       "answering one is counted separately from a wrong "
                       "answer because the failure is different in kind",
            "permission": "a should_refuse question with a forbidden_doc_ids "
                          "entry is a LEAK if answered — the most serious "
                          "failure class in this set",
        },
        "counts": {
            "total": len(questions),
            "by_challenge": by_challenge,
            "by_category": by_category,
            "refusal_cases": sum(1 for q in questions if q["should_refuse"]),
            "permission_cases": sum(
                1 for q in questions if q["category"] == "permission"
            ),
        },
        "questions": questions,
    }
    (out / "kb_questions.json").write_text(json.dumps(payload, indent=2), encoding="utf-8", newline="\n")
