"""Change request scored from the raw extract (evals/cases/change_request_lot_no.yaml).

Expected V012 exceptions are computed from bronze records and data/ground_truth (drift.json for the effective
date and source, mappings.json for the raw column names) — independently of the rule and of silver. The score
is exact: every expected (batch, row) raised with the expected severity, nothing else, and no row dropped
between bronze and silver.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import pandas as pd
import yaml
from sqlalchemy import Connection, text

CASE = Path(__file__).resolve().parent / "cases" / "change_request_lot_no.yaml"


@dataclass
class ChangeRequestScore:
    source: str
    effective: date
    expected_block: int
    expected_warn: int
    raised_block: int
    raised_warn: int
    missing: int
    unexpected: int
    wrong_severity: int
    bronze_rows: int
    silver_rows: int
    other_sources: dict[str, dict[str, int]] = field(default_factory=dict)

    @property
    def passed(self) -> bool:
        return (
            self.expected_block + self.expected_warn > 0
            and self.missing == self.unexpected == 0
            and self.wrong_severity == 0
            and self.bronze_rows == self.silver_rows
            and all(v["raised"] == v["expected"] for v in self.other_sources.values())
        )


def _source_key(plant: str) -> str:
    return plant.lower().replace("-", "")


def score(conn: Connection, data_dir: Path, case_path: Path = CASE) -> ChangeRequestScore:
    case = yaml.safe_load(case_path.read_text(encoding="utf-8"))
    drift = next(
        e
        for e in json.loads((data_dir / "ground_truth" / "drift.json").read_text(encoding="utf-8"))["events"]
        if e["drift_id"] == case["drift_id"]
    )
    mappings = json.loads((data_dir / "ground_truth" / "mappings.json").read_text(encoding="utf-8"))[
        "sources"
    ]
    column, effective, event_source = case["column"], date.fromisoformat(drift["effective"]), drift["source"]

    raised = pd.DataFrame(
        conn.execute(
            text(
                "SELECT e.source, e.batch_id::text AS batch_id, e.source_row, e.severity "
                "FROM ops.exceptions e JOIN ops.batches b USING (batch_id) "
                "WHERE e.rule_id = :rule AND b.status IN ('validated', 'published') "
                "AND (e.status <> 'resolved' OR e.resolved_by <> 'system:revalidation')"
            ),
            {"rule": case["rule"]},
        )
        .mappings()
        .all(),
        columns=["source", "batch_id", "source_row", "severity"],
    )

    others: dict[str, dict[str, int]] = {}
    result: ChangeRequestScore | None = None
    for plant, spec in mappings.items():
        source = _source_key(plant)
        reverse = {canonical: raw for raw, canonical in spec["column_map"].items()}
        lot_col, issue_col = reverse.get(column, column), reverse["issue_date"]
        bronze = pd.DataFrame(
            conn.execute(
                text(
                    "SELECT r.batch_id::text AS batch_id, r.source_row, r.record->>:lot AS lot, "
                    "r.record->>:issue AS issued "
                    "FROM bronze.raw_order_lines r JOIN ops.batches b USING (batch_id) "
                    "WHERE b.source = :s AND b.status IN ('validated', 'published')"
                ),
                {"lot": lot_col, "issue": issue_col, "s": source},
            )
            .mappings()
            .all(),
            columns=["batch_id", "source_row", "lot", "issued"],
        )
        if bronze.empty:
            continue
        lacking = bronze[bronze["lot"].fillna("").str.strip() == ""]
        mine = raised[raised["source"] == source]
        if plant != event_source:
            others[source] = {"expected": len(lacking), "raised": len(mine)}
            continue
        issued = pd.to_datetime(lacking["issued"], errors="coerce", format="%Y-%m-%d")
        dated = lacking[issued.notna()].assign(
            severity=["block" if d.date() >= effective else "warn" for d in issued.dropna()]
        )
        expected = {(r.batch_id, int(r.source_row)): r.severity for r in dated.itertuples()}
        got = {(r.batch_id, int(r.source_row)): r.severity for r in mine.itertuples()}
        silver_rows = conn.execute(
            text(
                "SELECT count(*) FROM silver.order_lines s JOIN ops.batches b USING (batch_id) "
                "WHERE b.source = :s AND b.status IN ('validated', 'published')"
            ),
            {"s": source},
        ).scalar_one()
        result = ChangeRequestScore(
            source=source,
            effective=effective,
            expected_block=sum(v == "block" for v in expected.values()),
            expected_warn=sum(v == "warn" for v in expected.values()),
            raised_block=sum(v == "block" for v in got.values()),
            raised_warn=sum(v == "warn" for v in got.values()),
            missing=len(expected.keys() - got.keys()),
            unexpected=len(got.keys() - expected.keys()),
            wrong_severity=sum(got[k] != v for k, v in expected.items() if k in got),
            bronze_rows=len(bronze),
            silver_rows=int(silver_rows),
        )
    if result is None:
        raise ValueError(
            f"{event_source} has no validated batches; load it before scoring the change request"
        )
    result.other_sources = others
    return result
