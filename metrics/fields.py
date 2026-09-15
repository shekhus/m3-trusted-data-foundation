"""Field dictionary (docs/plan.md A11): which canonical fields an order line must carry, and from when.

Versioned YAML in metrics/fields/, like the metric dictionary: exactly one version is current. A requirement
without a date is checked by V001 on every row; one with `required_from` is checked by V012 on rows whose
`by` date is on or after it, and rows before it are queued as "not covered" warnings. Making a field required
is a dictionary bump, not a code change.
"""

from __future__ import annotations

from datetime import date
from functools import cache
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from metrics.compiler import CompileError, Identifier
from pipeline.canonical import BY_NAME, REQUIRED

FIELDS_DIR = Path(__file__).resolve().parent / "fields"


class Requirement(BaseModel):
    model_config = ConfigDict(extra="forbid")

    column: Identifier
    required_from: date | None = None
    by: Identifier | None = None  # the row's date column compared with required_from
    owner: str | None = None
    fix: str | None = None
    before: str | None = None  # what a row dated before required_from is told

    @model_validator(mode="after")
    def _dated_has_basis(self) -> Requirement:
        dated = (self.by, self.owner, self.fix, self.before)
        if self.required_from is None and any(v is not None for v in dated):
            raise ValueError(f"{self.column}: by/owner/fix/before only apply to a dated requirement")
        if self.required_from is not None and any(v is None for v in dated):
            raise ValueError(f"{self.column}: a dated requirement needs by, owner, fix and before")
        return self


class FieldDictionary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    table: Literal["silver.order_lines"]
    version: int = Field(gt=0)
    status: Literal["current", "superseded"]
    owner: str
    changed_by: str
    effective_from: date
    change_request: str = ""
    required: list[Requirement] = Field(min_length=1)
    notes: str = ""

    @model_validator(mode="after")
    def _columns_known(self) -> FieldDictionary:
        columns = [r.column for r in self.required]
        if len(set(columns)) != len(columns):
            raise ValueError("a column is listed twice")
        for r in self.required:
            if r.column not in BY_NAME:
                raise ValueError(f"{r.column} is not a canonical column")
            if r.by is not None and (r.by not in BY_NAME or BY_NAME[r.by].kind != "date"):
                raise ValueError(f"{r.column}: by={r.by} is not a canonical date column")
            # every row must carry an undated field, so a mapping must be able to supply it
            if r.required_from is None and r.column not in REQUIRED:
                raise ValueError(f"{r.column} is required on every row but a mapping may leave it unmapped")
        return self

    @property
    def always(self) -> list[str]:
        return [r.column for r in self.required if r.required_from is None]

    @property
    def dated(self) -> list[Requirement]:
        return [r for r in self.required if r.required_from is not None]


def load_fields(directory: Path = FIELDS_DIR) -> list[FieldDictionary]:
    versions = []
    for path in sorted(directory.glob("*.yaml")):
        try:
            d = FieldDictionary.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
        except ValidationError as exc:
            raise CompileError(f"{path.name}: {exc}") from exc
        if path.name != f"order_line.v{d.version}.yaml":
            raise CompileError(f"{path.name}: file name must be order_line.v{d.version}.yaml")
        versions.append(d)
    versions.sort(key=lambda d: d.version)
    if [d.version for d in versions] != list(range(1, len(versions) + 1)):
        raise CompileError("field dictionary versions must run 1..n without gaps")
    if [d.status for d in versions].count("current") != 1 or versions[-1].status != "current":
        raise CompileError("exactly one field dictionary version is current, and it is the latest")
    return versions


@cache
def current_fields() -> FieldDictionary:
    return load_fields()[-1]
