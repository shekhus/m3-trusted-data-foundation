"""A13 exception agent scored against evals/cases/agent_outcomes.yaml (docs/addendum1.md §2.5).

- classification accuracy: outcome == expected, overall and per rule (target ≥ 85% overall, no rule < 70%);
  `also_acceptable` outcomes are reported as lenient accuracy, never in the headline.
- coverage honesty (HARD GATE, 100%): on the Cedar Falls pre-June-2026 lot faults, source_defect with
  not_covered and no fix.
- evidence quality: share of classifications citing a prior resolution or a document (target ≥ 90%).
- tool efficiency: mean tool calls per run, share of runs hitting the 6-call cap.
- escalation precision: escalations on patterns whose expected outcome is auto_fixable (target ≤ 5%).
- policy gate: how the agent's own proposed fixes were disposed of (adversarial suite:
  tests/test_policy_gate.py).
- fallbacks are counted; a model output the provider could not generate counts against the agent. A run whose
  model calls hit transport errors (429 rate limits that outlast the backend's wait budget, 5xx, connection
  failures) measures the provider, not the agent: it is excluded from every rate, and counted and listed as
  `excluded_transport`, never hidden.

Runs are sampled deterministically: per pattern, faults ordered by fault_id and taken round-robin across
plants, each matched to its exception in the queue.
"""

from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path

import yaml
from sqlalchemy import Engine, text

CASES = Path(__file__).resolve().parent / "cases" / "agent_outcomes.yaml"


@dataclass
class Sample:
    case: str  # pattern id, or "coverage_gate"
    rule: str
    expected: str
    also_acceptable: list[str]
    fault_id: str
    plant: str
    row_key: str
    exception_id: int
    coverage: bool = False


@dataclass
class AgentResult:
    sample: Sample
    run_id: str | None
    outcome: str | None
    confidence: float | None
    not_covered: bool | None
    has_fix: bool
    gate: str | None
    tool_calls: int
    evidence_kinds: list[str]
    fallback: bool
    llm_errors: int
    error: str | None = None

    @property
    def correct(self) -> bool:
        if self.sample.coverage:
            return self.outcome == "source_defect" and bool(self.not_covered) and not self.has_fix
        return self.outcome == self.sample.expected

    @property
    def valid(self) -> bool:
        return self.error is None and self.llm_errors == 0

    @property
    def acceptable(self) -> bool:
        return self.correct or (self.outcome in self.sample.also_acceptable)


@dataclass
class AgentScore:
    results: list[AgentResult]
    notes: list[str] = field(default_factory=list)

    def _scored(self, coverage: bool) -> list[AgentResult]:
        return [r for r in self.results if r.sample.coverage == coverage and r.valid]

    def as_dict(self) -> dict:
        main, gate = self._scored(False), self._scored(True)
        per_rule: dict[str, list[AgentResult]] = defaultdict(list)
        per_case: dict[str, list[AgentResult]] = defaultdict(list)
        for r in main:
            per_rule[r.sample.rule[:4]].append(r)
            per_case[r.sample.case].append(r)
        ran = [r for r in self.results if r.valid]
        auto_patterns = [r for r in main if r.sample.expected == "auto_fixable"]
        fixes = [r for r in ran if r.has_fix]
        return {
            "runs": len(self.results),
            "runs_failed": sum(r.error is not None for r in self.results),
            "excluded_transport": sum(r.error is None and r.llm_errors > 0 for r in self.results),
            "accuracy": _rate(main, lambda r: r.correct),
            "lenient_accuracy": _rate(main, lambda r: r.acceptable),
            "meets_accuracy_target": (_rate(main, lambda r: r.correct) or 0) >= 0.85
            and all((_rate(v, lambda r: r.correct) or 0) >= 0.70 for v in per_rule.values()),
            "per_rule": {
                k: {
                    "n": len(v),
                    "accuracy": _rate(v, lambda r: r.correct),
                    "outcomes": dict(Counter(r.outcome for r in v)),
                }
                for k, v in sorted(per_rule.items())
            },
            "per_pattern": {
                k: {
                    "n": len(v),
                    "expected": v[0].sample.expected,
                    "accuracy": _rate(v, lambda r: r.correct),
                    "outcomes": dict(Counter(r.outcome for r in v)),
                }
                for k, v in per_case.items()
            },
            "coverage_gate": {
                "n": len(gate),
                "passed": sum(r.correct for r in gate),
                "hard_gate_met": bool(gate) and all(r.correct for r in gate),
                "outcomes": dict(Counter(f"{r.outcome}/not_covered={r.not_covered}" for r in gate)),
            },
            "evidence_quality": _rate(
                ran, lambda r: bool({"resolution", "document"} & set(r.evidence_kinds))
            ),
            "mean_tool_calls": round(sum(r.tool_calls for r in ran) / len(ran), 2) if ran else None,
            "hit_tool_cap": _rate(ran, lambda r: r.tool_calls >= 6),
            "escalations_on_auto_fixable_patterns": _rate(auto_patterns, lambda r: r.outcome == "escalate"),
            "proposed_fixes": len(fixes),
            "gate_dispositions": dict(Counter(r.gate for r in fixes)),
            "fallbacks": sum(r.fallback for r in ran),
            "notes": self.notes,
            "results": [{**asdict(r), "correct": r.correct} for r in self.results],
        }


def _rate(rows: list[AgentResult], ok: Callable[[AgentResult], bool]) -> float | None:
    return round(sum(ok(r) for r in rows) / len(rows), 4) if rows else None


def load_cases(path: Path = CASES) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _source(plant: str) -> str:
    return plant.lower().replace("-", "")


def samples(
    engine: Engine,
    data_dir: Path,
    per_pattern: int,
    coverage_limit: int | None = None,
    cases: dict | None = None,
) -> list[Sample]:
    cases = cases or load_cases()
    faults = json.loads((data_dir / "ground_truth" / "faults.json").read_text(encoding="utf-8"))["faults"]
    with engine.connect() as conn:
        rows = conn.execute(
            text("SELECT exception_id, source, rule_id, row_key FROM ops.exceptions ORDER BY exception_id")
        ).all()
    by_key: dict[tuple[str, str, str], int] = {}
    for r in rows:
        by_key.setdefault((r.source, r.rule_id, r.row_key), r.exception_id)

    out: list[Sample] = []
    for pattern in cases["patterns"]:
        matching = sorted(
            (
                f
                for f in faults
                if f["rule_id"] == pattern["rule_id"] and re.search(pattern["detail"], f["detail"])
            ),
            key=lambda f: f["fault_id"],
        )
        by_plant: dict[str, list[dict]] = defaultdict(list)
        for f in matching:
            exception_id = by_key.get((_source(f["plant"]), pattern["rule_id"][:4], f["row_key"]))
            if exception_id is not None and not (
                pattern["rule_id"].startswith("V006")
                and f["plant"] == "PLT-01"
                and f["issue_date"] < cases["coverage_gate"]["shipped_before"]
            ):
                by_plant[f["plant"]].append({**f, "exception_id": exception_id})
        chosen: list[dict] = []
        while len(chosen) < per_pattern and any(by_plant.values()):
            for plant in sorted(by_plant):
                if by_plant[plant] and len(chosen) < per_pattern:
                    chosen.append(by_plant[plant].pop(0))
        out += [
            Sample(
                pattern["id"],
                pattern["rule_id"],
                pattern["expected"],
                pattern.get("also_acceptable", []),
                f["fault_id"],
                f["plant"],
                f["row_key"],
                f["exception_id"],
            )
            for f in chosen
        ]

    gate = cases["coverage_gate"]
    covered = sorted(
        (
            f
            for f in faults
            if f["rule_id"] == gate["rule_id"]
            and f["plant"] == gate["plant"]
            and f["issue_date"] < gate["shipped_before"]
        ),
        key=lambda f: f["fault_id"],
    )
    for f in covered[:coverage_limit]:
        exception_id = by_key.get((_source(f["plant"]), gate["exception_rule"], f["row_key"]))
        if exception_id is not None:
            out.append(
                Sample(
                    "coverage_gate",
                    gate["rule_id"],
                    gate["expected"],
                    [],
                    f["fault_id"],
                    f["plant"],
                    f["row_key"],
                    exception_id,
                    coverage=True,
                )
            )
    return out
