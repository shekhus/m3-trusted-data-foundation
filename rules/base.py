"""Deterministic validation rules (CLAUDE.md principle 2). No LLM, no probabilities, no rule logic in prompts.

A rule sees one batch's silver rows as a DataFrame plus the master data, and returns the rows it rejects with
per-row evidence. It never changes a value and never drops a row: the engine turns violations into
ops.exceptions with the rule's severity, owner, message and suggested fix.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import ClassVar, Literal

import pandas as pd

from metrics.fields import FieldDictionary, current_fields

Severity = Literal["block", "warn"]


@dataclass(frozen=True)
class Masters:
    customers: frozenset[str]
    items: frozenset[str]
    item_uom: dict[str, str]
    item_lb_per_case: dict[str, float]
    lots: pd.DataFrame  # lot_no (index), item_no, plant, production_date, expiry_date (datetime64)


@dataclass(frozen=True)
class RuleContext:
    source: str
    masters: Masters
    mapped: frozenset[str]  # canonical columns the batch's confirmed mapping provides
    fields: FieldDictionary = field(default_factory=current_fields)  # metrics/fields/, current version
    notes: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class Violations:
    """Rows a rule rejects: DataFrame index labels in `rows`, one evidence dict per row in `details`.

    A rule whose severity or owner depends on the row (V012: blocked from a date, a warning before it) gives
    `severities` / `owners` per row; otherwise the rule's own apply.
    """

    rows: list[int]
    details: list[dict]
    severities: list[Severity] | None = None
    owners: list[str] | None = None

    @classmethod
    def none(cls) -> Violations:
        return cls([], [])

    @classmethod
    def where(cls, df: pd.DataFrame, mask: pd.Series, columns: list[str]) -> Violations:
        hit = df.loc[mask.fillna(False).astype(bool)]
        return cls(list(hit.index), [_evidence(hit, i, columns) for i in hit.index])


def _evidence(df: pd.DataFrame, i: int, columns: list[str]) -> dict:
    out = {}
    for c in columns:
        v = df.at[i, c]
        out[c] = None if v is None or (not isinstance(v, str) and pd.isna(v)) else (
            v.date().isoformat() if isinstance(v, pd.Timestamp) else v)
    return out


class Rule(ABC):
    id: ClassVar[str]  # "V001"
    name: ClassVar[str]
    severity: ClassVar[Severity]
    applies_to: ClassVar[str] = "silver.order_lines"
    message: ClassVar[str]
    suggested_fix: ClassVar[str]

    def owner(self, ctx: RuleContext) -> str:
        """Who works the exception by default. Source-data problems go to the plant's data owner."""
        return f"source:{ctx.source}"

    @abstractmethod
    def check(self, df: pd.DataFrame, ctx: RuleContext) -> Violations: ...
