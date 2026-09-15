"""Policy gate (docs/addendum1.md §2.4): decides, in code, whether a proposed fix may ever be applied.

The allowlist is policy/autofix.yaml, sourced from SOP-DQ-001-v2. The model never decides this, and the file
cannot widen the SOP: `load_policy` refuses a policy that allows a kind the SOP does not name, or that stops
protecting a field the SOP protects (customer number, item number, quantity, weight), because those floors
are also written here.

A fix passes only if every check holds: its kind is allowed and matches the exception's rule; it changes only
that kind's fields; the original values it claims are the values actually in the row; each proposed value is
exactly what the normalisation produces (kg × 2.20462; the same calendar date re-read in an allowed format);
and the original is retained. A fix that fails is not discarded: it becomes a proposal for a human, with the
reasons. A fix that passes still waits for an owner's approval before anything is applied (A13 interrupt).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field

from agent.contracts import FieldChange, Resolution, Value

POLICY_FILE = Path(__file__).resolve().parent.parent / "policy" / "autofix.yaml"

# Floors from SOP-DQ-001-v2 "Auto-fix policy", kept in code so no edit to the YAML can loosen them.
SOP_KINDS = frozenset({"uom_kg_to_lb", "date_format"})
SOP_PROTECTED = frozenset(
    {"customer_no", "item_no", "ordered_qty", "invoiced_qty", "ordered_weight_lb", "invoiced_weight_lb"}
)
KG_TO_LB = 2.20462
WEIGHT_FIELDS = frozenset({"ordered_weight_lb", "invoiced_weight_lb"})
EXCEL_EPOCH = date(1899, 12, 30)

Disposition = Literal["no_fix", "apply_after_approval", "human_proposal"]


class PolicyError(ValueError):
    pass


class AllowedKind(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: str
    sop_text: str
    rules: list[str] = Field(min_length=1)
    fields: list[str] = []
    converts: list[str] = []
    factor: float | None = None
    tolerance_lb: float | None = None
    formats: list[str] = []


class Source(BaseModel):
    model_config = ConfigDict(extra="forbid")

    doc_id: str
    section: str
    quote: str


class Policy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: Source
    retain_original: bool
    never_changed: list[str]
    allowed: list[AllowedKind]

    def kind(self, name: str) -> AllowedKind | None:
        return next((k for k in self.allowed if k.kind == name), None)


def load_policy(path: Path = POLICY_FILE) -> Policy:
    policy = Policy.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
    problems = []
    if not policy.retain_original:
        problems.append("the SOP requires the original value to be retained")
    if missing := SOP_PROTECTED - set(policy.never_changed):
        problems.append(
            f"stops protecting {sorted(missing)}, which the SOP forbids any automated process to change"
        )
    for k in policy.allowed:
        if k.kind not in SOP_KINDS:
            problems.append(f"allows '{k.kind}', which SOP-DQ-001-v2 does not name")
        if protected := set(k.fields) & SOP_PROTECTED:
            problems.append(f"'{k.kind}' may change protected fields {sorted(protected)}")
        if k.converts and (
            k.kind != "uom_kg_to_lb"
            or not set(k.converts) <= WEIGHT_FIELDS
            or k.factor is None
            or not math.isclose(k.factor, KG_TO_LB, rel_tol=1e-6)
        ):
            problems.append(f"'{k.kind}' converts values other than weights by the kg→lb factor")
    if problems:
        raise PolicyError(f"{path.name} would widen SOP-DQ-001-v2: " + "; ".join(problems))
    return policy


@dataclass(frozen=True)
class ExceptionFacts:
    """What the gate checks a fix against, read from the database, never from the model."""

    exception_id: int
    rule_id: str
    row: dict[str, Value]  # current silver values (dates as ISO strings)
    raw: dict[str, str | None] = field(default_factory=dict)  # canonical field -> raw source text
    master_uom: str | None = None


@dataclass(frozen=True)
class GateVerdict:
    disposition: Disposition
    reasons: list[str]
    resolution: Resolution

    @property
    def applicable(self) -> bool:
        return self.disposition == "apply_after_approval"


def _same_number(a: Value, b: Value, tolerance: float = 1e-6) -> bool:
    try:
        return a is not None and b is not None and math.isclose(float(a), float(b), abs_tol=tolerance)
    except (TypeError, ValueError):
        return False


def _parse(raw: str, fmt: str) -> date | None:
    raw = raw.strip()
    try:
        if fmt == "excel_serial":
            serial = float(raw)
            return EXCEL_EPOCH + timedelta(days=int(serial)) if serial.is_integer() and serial > 0 else None
        return datetime.strptime(raw, fmt).date()
    except ValueError:
        return None


def _check_date(change: FieldChange, kind: AllowedKind, facts: ExceptionFacts) -> list[str]:
    raw = facts.raw.get(change.field)
    if raw is None or str(change.original).strip() != raw.strip():
        return [f"{change.field}: original {change.original!r} is not the source value {raw!r}"]
    readings = {d.isoformat() for fmt in kind.formats if (d := _parse(raw, fmt)) is not None}
    if str(change.proposed) not in readings:
        return [
            f"{change.field}: {change.proposed!r} is not {raw!r} re-read in an allowed date format "
            f"(allowed readings: {sorted(readings) or 'none'})"
        ]
    return []


def _check_weight(change: FieldChange, kind: AllowedKind, facts: ExceptionFacts) -> list[str]:
    current = facts.row.get(change.field)
    if not _same_number(change.original, current):
        return [f"{change.field}: original {change.original!r} is not the row's value {current!r}"]
    expected = float(current) * (kind.factor or KG_TO_LB)  # type: ignore[arg-type]
    if not _same_number(change.proposed, expected, kind.tolerance_lb or 0.01):
        return [f"{change.field}: {change.proposed!r} is not {current!r} kg in lb ({expected:.3f})"]
    return []


def evaluate(resolution: Resolution, facts: ExceptionFacts, policy: Policy | None = None) -> GateVerdict:
    policy = policy or load_policy()
    fix = resolution.proposed_fix
    if fix is None:
        return GateVerdict("no_fix", [], resolution)
    reasons: list[str] = []
    if resolution.exception_id != facts.exception_id:
        reasons.append("the classification is for a different exception")
    kind = policy.kind(fix.kind)
    if kind is None:
        reasons.append(
            f"'{fix.kind}' is not an allowed auto-fix: SOP-DQ-001-v2 permits only "
            f"{', '.join(k.kind for k in policy.allowed)}; everything else requires a human decision"
        )
        protected = sorted({c.field for c in fix.changes} & set(policy.never_changed))
        if protected:
            reasons.append(f"it changes {protected}: {policy.source.quote}")
        return GateVerdict("human_proposal", reasons, resolution)
    if policy.retain_original and not fix.retain_original:
        reasons.append("the original value must be retained")
    if facts.rule_id not in kind.rules:
        reasons.append(f"'{fix.kind}' fixes {', '.join(kind.rules)} exceptions, not {facts.rule_id}")
    for change in fix.changes:
        if change.field in kind.converts:
            reasons += _check_weight(change, kind, facts)
        elif change.field in policy.never_changed:
            reasons.append(f"{change.field} may not be changed automatically: {policy.source.quote}")
        elif change.field not in kind.fields:
            reasons.append(f"'{fix.kind}' may not change {change.field}")
        elif kind.kind == "date_format":
            reasons += _check_date(change, kind, facts)
        elif change.field == "uom":
            if str(change.original) != str(facts.row.get("uom")):
                reasons.append(
                    f"uom: original {change.original!r} is not the row's value {facts.row.get('uom')!r}"
                )
            if facts.master_uom is not None and str(change.proposed).upper() != facts.master_uom.upper():
                reasons.append(f"uom: {change.proposed!r} is not the item master basis {facts.master_uom!r}")
    if kind.kind == "uom_kg_to_lb" and not any(c.field in kind.converts for c in fix.changes):
        reasons.append("a kg→lb normalisation must convert the weight, not only relabel the unit")
    return GateVerdict("human_proposal" if reasons else "apply_after_approval", reasons, resolution)
