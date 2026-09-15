"""Reconciliation scored against data/ground_truth/reconciliation.json (plan §2.5 and the file's own scoring).

- consumer views: every compared period green (delta 0 between the two current consumers).
- causes: per tool, identified ⊆ the tool's real causes ⊆ identified ∪ undetermined (a cause the export cannot
  confirm may be reported as undetermined, but never as identified if it is not real, and never missed).
- gap detection: every month whose true gap exceeds 0.001 is flagged.
- dominant cause: the largest |single-switch contribution| among the tool's real rate causes.
- sizing: each real cause's contribution within 0.005 of the key per month; consignment lines dropped exact.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy import Connection, text

TOLERANCE = 0.005
TOOL_GAP = {"tool1_legacy_report_suite": "tool1_gap", "tool2_dashboard_tool": "tool2_gap"}


@dataclass
class ToolScore:
    tool: str
    causes_correct: bool
    identified: list[str]
    undetermined: list[str]
    expected: list[str]
    gaps_flagged: int
    gaps_expected: int
    dominant: str | None
    dominant_expected: str | None
    worst_contribution_error: float
    lines_dropped_exact: bool | None
    notes: list[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return (
            self.causes_correct
            and self.gaps_flagged == self.gaps_expected
            and self.dominant == self.dominant_expected
            and self.worst_contribution_error <= TOLERANCE
            and self.lines_dropped_exact is not False
        )


@dataclass
class ReconciliationScore:
    run_id: int
    consumer_periods: int
    consumer_red: int
    tools: list[ToolScore]

    @property
    def passed(self) -> bool:
        return self.consumer_periods > 0 and self.consumer_red == 0 and all(t.passed for t in self.tools)


def score(conn: Connection, data_dir: Path, run_id: int | None = None) -> ReconciliationScore:
    truth = json.loads((data_dir / "ground_truth" / "reconciliation.json").read_text(encoding="utf-8"))
    monthly = {m["month"]: m for m in truth["monthly"]}
    run = conn.execute(
        text(
            "SELECT run_id, summary FROM ops.reconciliation_runs "
            + ("WHERE run_id = :r" if run_id else "ORDER BY run_id DESC LIMIT 1")
        ),
        {"r": run_id} if run_id else {},
    ).one()
    red, periods = conn.execute(
        text(
            "SELECT count(*) FILTER (WHERE status = 'red'), count(*) "
            "FROM ops.reconciliation WHERE run_id = :r"
        ),
        {"r": run.run_id},
    ).one()
    tools: list[ToolScore] = []
    for tool, spec in truth["tools"].items():
        summary = run.summary.get("tools", {}).get(tool, {})
        expected = set(spec["differs_by"])
        identified, undetermined = (
            set(summary.get("identified_causes", [])),
            set(summary.get("undetermined_causes", [])),
        )
        rows = conn.execute(
            text(
                "SELECT period, gap_flagged, contributions FROM ops.reconciliation_tools "
                "WHERE run_id = :r AND tool = :t"
            ),
            {"r": run.run_id, "t": tool},
        ).all()
        rate_causes = [c for c in expected if c != "scope_filter"]
        errors, dropped_exact, dominants = [], [], []
        for row in rows:
            key = monthly.get(row.period)
            if key is None:
                continue
            contributions = key["cause_contributions"]
            for cause in rate_causes:
                if cause in row.contributions:
                    errors.append(abs(row.contributions[cause] - contributions[cause]))
                else:
                    errors.append(float("inf"))
            if "scope_filter" in expected:
                dropped_exact.append(
                    row.contributions.get("scope_filter_lines_dropped")
                    == contributions["scope_filter_lines_dropped"]
                )
            if rate_causes:
                dominants.append(max(rate_causes, key=lambda c: abs(contributions[c])))
        flagged = {r.period for r in rows if r.gap_flagged}
        should = {m for m, v in monthly.items() if abs(v[TOOL_GAP[tool]]) > 0.001}
        dominant_expected = max(set(dominants), key=dominants.count) if dominants else None
        tools.append(
            ToolScore(
                tool=tool,
                causes_correct=identified <= expected <= identified | undetermined,
                identified=sorted(identified),
                undetermined=sorted(undetermined),
                expected=sorted(expected),
                gaps_flagged=len(flagged & should),
                gaps_expected=len(should),
                dominant=summary.get("dominant_cause"),
                dominant_expected=dominant_expected,
                worst_contribution_error=max(errors, default=0.0),
                lines_dropped_exact=all(dropped_exact) if dropped_exact else None,
            )
        )
    return ReconciliationScore(run.run_id, periods, red, tools)
