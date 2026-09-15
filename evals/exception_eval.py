"""Exception recall and precision against data/ground_truth/faults.json (plan §2.5: both ≥ 95%).

Recall is per rule, over the faults a pipeline can detect. The answer key's `achievable_ceiling` says 33 of 50
V006 faults are detectable; it decides by ship date, but extracts are split by order month, so one fault
ordered in May and shipped in June sits in a file with no lot column. Detectability is therefore computed from
the loaded data — a V006 fault is detectable when its row came from a header whose mapping supplies lot_no —
and the answer key's figure is reported beside it (docs/decisions.md D-018).

Precision is reported two ways: by row, the answer key's definition (an exception on a row with no seeded
fault is a false positive), and by (row, rule). Every unexplained exception is listed with its evidence.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd
from sqlalchemy import Connection, text

TARGET = 0.95


@dataclass
class RuleScore:
    rule: str
    seeded: int
    detectable: int
    answer_key_detectable: int | None  # the key's figure covers all plants: None for a partial load
    caught: int
    raised: int

    @property
    def recall(self) -> float:
        return self.caught / self.detectable if self.detectable else 1.0


@dataclass
class ExceptionScore:
    rules: list[RuleScore]
    exceptions: int
    rows_with_exceptions: int
    false_positive_rows: int
    matched_pairs: int
    unexplained: list[dict] = field(default_factory=list)
    sources: list[str] = field(default_factory=list)

    @property
    def recall(self) -> float:
        detectable = sum(r.detectable for r in self.rules)
        return sum(r.caught for r in self.rules) / detectable if detectable else 1.0

    @property
    def row_precision(self) -> float:
        rows = self.rows_with_exceptions
        return (rows - self.false_positive_rows) / rows if rows else 1.0

    @property
    def pair_precision(self) -> float:
        return self.matched_pairs / self.exceptions if self.exceptions else 1.0

    def as_dict(self) -> dict:
        return {
            "sources": self.sources, "recall": round(self.recall, 4),
            "row_precision": round(self.row_precision, 4), "pair_precision": round(self.pair_precision, 4),
            "exceptions": self.exceptions, "rows_with_exceptions": self.rows_with_exceptions,
            "false_positive_rows": self.false_positive_rows,
            "meets_target": self.recall >= TARGET and self.row_precision >= TARGET,
            "rules": [{**r.__dict__, "recall": round(r.recall, 4)} for r in self.rules],
            "unexplained": self.unexplained,
        }


def score(conn: Connection, data_dir: Path) -> ExceptionScore:
    truth = json.loads((data_dir / "ground_truth" / "faults.json").read_text(encoding="utf-8"))
    faults = pd.DataFrame(truth["faults"])
    faults["rule"] = faults["rule_id"].str[:4]
    faults["source"] = faults["plant"].str.lower().str.replace("-", "", regex=False)
    all_sources = set(faults["source"])

    loaded = [r[0] for r in conn.execute(text(
        "SELECT DISTINCT source FROM ops.batches WHERE status IN ('validated', 'published') "
        "ORDER BY 1")).all()]
    faults = faults[faults["source"].isin(loaded)]
    exceptions = pd.DataFrame(conn.execute(text(
        "SELECT e.source, e.rule_id AS rule, e.row_key, e.source_row, e.batch_id, e.reason, e.details "
        "FROM ops.exceptions e JOIN ops.batches b USING (batch_id) "
        "WHERE b.status IN ('validated', 'published') "
        "AND (e.status <> 'resolved' OR e.resolved_by <> 'system:revalidation')")).mappings().all(),
        columns=["source", "rule", "row_key", "source_row", "batch_id", "reason", "details"])
    with_lot = {r[0] for r in conn.execute(text(
        "SELECT DISTINCT s.order_no || '|' || s.line_no FROM silver.order_lines s "
        "JOIN ops.mapping_versions m USING (mapping_version_id) "
        "WHERE EXISTS (SELECT 1 FROM jsonb_array_elements(m.mapping->'columns') c "
        "              WHERE c->>'canonical_col' = 'lot_no')")).all()}

    ceiling = truth["achievable_ceiling"]
    complete = set(loaded) >= all_sources
    rules: list[RuleScore] = []
    for rule_id, group in faults.groupby("rule_id"):
        rule = rule_id[:4]
        keys = set(group["row_key"])
        detectable = keys & with_lot if rule == "V006" else keys
        raised = set(exceptions.loc[exceptions["rule"] == rule, "row_key"])
        key_says = int(ceiling[rule_id]["detectable"]) if complete else None
        rules.append(RuleScore(rule, len(keys), len(detectable), key_says,
                               len(detectable & raised), len(raised)))

    seeded_rows = set(faults["row_key"])
    seeded_pairs = set(zip(faults["row_key"], faults["rule"], strict=True))
    row_keys = set(exceptions["row_key"])
    unexplained = exceptions[~exceptions["row_key"].isin(seeded_rows)]
    pairs = list(zip(exceptions["row_key"], exceptions["rule"], strict=True))
    return ExceptionScore(
        rules=sorted(rules, key=lambda r: r.rule), exceptions=len(exceptions),
        rows_with_exceptions=len(row_keys), false_positive_rows=unexplained["row_key"].nunique(),
        matched_pairs=sum(p in seeded_pairs for p in pairs),
        unexplained=[{"source": u.source, "rule": u.rule, "row_key": u.row_key, "reason": u.reason,
                      "details": u.details} for u in unexplained.itertuples()],
        sources=loaded)
