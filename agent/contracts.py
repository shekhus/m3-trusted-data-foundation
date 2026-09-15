"""The agent's output contract (docs/addendum1.md §2.4; CLAUDE.md "Agentic conventions").

Exactly four outcomes, no fifth and no "unsure": low confidence is a field, not an outcome. Every
classification cites evidence (resolution ids, document ids, row keys, master matches); one without evidence
fails the contract. A proposed fix is a list of field changes that each carry the original value, and only an
`auto_fixable` classification may carry one. Whether the fix may actually be applied is not decided here: that
is agent/policy_gate.py, in code.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Outcome = Literal["auto_fixable", "needs_master_data", "source_defect", "escalate"]
OUTCOMES: tuple[str, ...] = ("auto_fixable", "needs_master_data", "source_defect", "escalate")
EvidenceKind = Literal["resolution", "document", "row", "master", "other_source"]
Value = str | float | int | None


class EvidenceRef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: EvidenceKind
    ref: str = Field(min_length=1)  # RES-0001, SOP-DQ-001-v2#5, SO0001234|2, customers:C000044, plt02


class FieldChange(BaseModel):
    model_config = ConfigDict(extra="forbid")

    field: str = Field(min_length=1)
    original: Value
    proposed: Value


class ProposedFix(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: str = Field(min_length=1)  # the gate decides whether this kind is allowed; the contract does not
    changes: list[FieldChange] = []  # empty only for a fix that changes no value (e.g. exclude a duplicate)
    retain_original: bool
    description: str = ""

    @model_validator(mode="after")
    def _one_change_per_field(self) -> ProposedFix:
        fields = [c.field for c in self.changes]
        if len(set(fields)) != len(fields):
            raise ValueError("a field is changed twice")
        if not self.changes and not self.description.strip():
            raise ValueError("a fix that changes no field must describe what it does")
        return self


class Resolution(BaseModel):
    model_config = ConfigDict(extra="forbid")

    exception_id: int
    outcome: Outcome
    confidence: float = Field(ge=0.0, le=1.0)
    evidence_refs: list[EvidenceRef] = Field(min_length=1)
    proposed_fix: ProposedFix | None = None
    not_covered: bool = False  # source_defect only: the source never carried the field for this period
    rationale: str = Field(min_length=10)

    @model_validator(mode="after")
    def _consistent(self) -> Resolution:
        if self.proposed_fix is not None and self.outcome != "auto_fixable":
            raise ValueError(f"only an auto_fixable classification may propose a fix, not {self.outcome}")
        if self.outcome == "auto_fixable" and self.proposed_fix is None:
            raise ValueError("an auto_fixable classification must say which fix")
        if self.not_covered and self.outcome != "source_defect":
            raise ValueError("not_covered is a source_defect finding")
        return self
