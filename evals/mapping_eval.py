"""Mapping accuracy per source header, scored against data/ground_truth/mappings.json (plan §2.5).

A column is correct when the proposed canonical column equals the answer key's (or both leave it unmapped).
Transforms are scored separately: date parsing matches the source's date format, kg→lb appears exactly on kg
weight columns, customer normalisation appears exactly when the source's customer numbers are not canonical.
Proposers are pluggable so the LLM (task 7) is scored on the same cases.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from pipeline.canonical import BY_NAME
from pipeline.mapper.contract import MappingProposal
from pipeline.profile import SourceProfile, profile_all

Proposer = Callable[[SourceProfile, list[str]], MappingProposal]
DATE_TRANSFORM = {"iso": "parse_date_iso", "us": "parse_date_us", "excel_serial": "parse_excel_serial"}


@dataclass
class HeaderScore:
    case: str
    source: str
    first_file: str
    columns: int
    correct: int
    transform_checks: int
    transform_correct: int
    errors: list[str] = field(default_factory=list)

    @property
    def accuracy(self) -> float:
        return self.correct / self.columns if self.columns else 0.0


@dataclass(frozen=True)
class Case:
    name: str
    profile: SourceProfile
    header: list[str]
    first_file: str
    expected: dict[str, str]  # source column → canonical column
    spec: dict  # the source's answer-key entry (date_format, weight_unit, customer_format)


def _plant(source: str) -> str:
    return f"{source[:3].upper()}-{source[3:]}"


def _expected_map(spec: dict, first_file: str) -> dict[str, str]:
    month = first_file.rsplit("_", 1)[-1].removesuffix(".csv")
    column_map: dict[str, str] = spec["column_map"]
    for variant in spec.get("drift_variants", []):
        if month >= variant["effective_from"][:7]:
            column_map = variant["column_map"]
    return column_map


def build_cases(data_dir: Path, cases_dir: Path,
                profiles: dict[str, SourceProfile] | None = None) -> list[Case]:
    truth = json.loads((data_dir / "ground_truth" / "mappings.json").read_text(encoding="utf-8"))
    if profiles is None:
        profiles = {p.source: p for p in profile_all(data_dir / "sources", data_dir / "master")}
    cases: list[Case] = []
    for source, profile in profiles.items():
        spec = truth["sources"][_plant(source)]
        for v in profile.header_variants:
            cases.append(Case("answer_key_headers", profile, v.columns, v.first_file,
                              _expected_map(spec, v.first_file), spec))
    for path in sorted(cases_dir.glob("mapping_*.yaml")):
        cfg = yaml.safe_load(path.read_text(encoding="utf-8"))
        base = profiles[cfg["source"]]
        variant = base.header_variants[cfg["header_variant"]]
        rename: dict[str, str] = cfg["rename"]
        renamed = base.model_copy(update={
            "columns": [c.model_copy(update={"name": rename.get(c.name, c.name)}) for c in base.columns],
        })
        spec = truth["sources"][_plant(cfg["source"])]
        expected = {rename.get(k, k): v for k, v in _expected_map(spec, variant.first_file).items()}
        header = [rename.get(c, c) for c in variant.columns]
        cases.append(Case(cfg["case"], renamed, header, variant.first_file, expected, spec))
    return cases


def score(case: Case, proposal: MappingProposal) -> HeaderScore:
    s = HeaderScore(case.name, case.profile.source, case.first_file, len(case.header), 0, 0, 0)
    for col in proposal.columns:
        want = case.expected.get(col.source_col)
        if col.canonical_col == want:
            s.correct += 1
        else:
            s.errors.append(f"{col.source_col}: proposed {col.canonical_col}, expected {want}")
        if want is None:
            continue
        kind = BY_NAME[want].kind
        checks: list[tuple[bool, str]] = []
        if kind == "date":
            checks.append((DATE_TRANSFORM[case.spec["date_format"]] in col.transforms, "date parse"))
        if kind == "weight":
            checks.append((("kg_to_lb" in col.transforms) == (case.spec["weight_unit"] == "kg"), "kg_to_lb"))
        if kind == "customer":
            checks.append((("normalize_customer_no" in col.transforms)
                           == (case.spec["customer_format"] != "prefixed"), "customer normalisation"))
        for ok, label in checks:
            s.transform_checks += 1
            s.transform_correct += ok
            if not ok:
                s.errors.append(f"{col.source_col}: wrong {label} transform {col.transforms}")
    return s


def run(proposer: Proposer, data_dir: Path, cases_dir: Path,
        profiles: dict[str, SourceProfile] | None = None) -> list[HeaderScore]:
    return [score(c, proposer(c.profile, c.header)) for c in build_cases(data_dir, cases_dir, profiles)]


def summarise(scores: list[HeaderScore]) -> dict[str, dict[str, float | int]]:
    out: dict[str, dict[str, float | int]] = {}
    for name in sorted({s.case for s in scores}):
        group = [s for s in scores if s.case == name]
        cols, correct = sum(s.columns for s in group), sum(s.correct for s in group)
        checks, ok = sum(s.transform_checks for s in group), sum(s.transform_correct for s in group)
        out[name] = {"headers": len(group), "columns": cols, "correct": correct,
                     "top1_accuracy": round(correct / cols, 4) if cols else 0.0,
                     "transform_checks": checks,
                     "transform_accuracy": round(ok / checks, 4) if checks else 0.0}
    return out
