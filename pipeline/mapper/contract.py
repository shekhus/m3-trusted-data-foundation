"""The mapping contract shared by the heuristic, the LLM, the API and the portal.

A mapping binds one exact source header (its column list) to the canonical schema. Every header column appears
exactly once, mapped or explicitly unmapped; no canonical column is targeted twice; only known canonical names
and transforms are accepted. An LLM answer that names a column the source does not have fails here.
"""

from __future__ import annotations

import hashlib
import json
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from pipeline.canonical import BY_NAME, REQUIRED, Transform


class ColumnMapping(BaseModel):
    source_col: str
    canonical_col: str | None
    confidence: float = Field(ge=0.0, le=1.0)
    rationale: str
    transforms: list[Transform] = []

    @field_validator("canonical_col")
    @classmethod
    def _known_canonical(cls, value: str | None) -> str | None:
        if value is not None and value not in BY_NAME:
            raise ValueError(f"unknown canonical column '{value}'")
        return value

    @model_validator(mode="after")
    def _unmapped_has_no_transforms(self) -> ColumnMapping:
        if self.canonical_col is None and self.transforms:
            raise ValueError(f"{self.source_col}: an unmapped column cannot carry transforms")
        return self


class MappingProposal(BaseModel):
    source: str
    header: list[str] = Field(min_length=1)
    columns: list[ColumnMapping]
    proposed_by: Literal["heuristic", "llm", "human"]

    @model_validator(mode="after")
    def _covers_header_exactly(self) -> MappingProposal:
        cols = [c.source_col for c in self.columns]
        if sorted(cols) != sorted(self.header) or len(set(cols)) != len(cols):
            missing = sorted(set(self.header) - set(cols))
            extra = sorted(set(cols) - set(self.header))
            raise ValueError(f"columns must list each header column once "
                             f"(missing {missing}, not in header {extra})")
        targets = [c.canonical_col for c in self.columns if c.canonical_col]
        duplicates = sorted({t for t in targets if targets.count(t) > 1})
        if duplicates:
            raise ValueError(f"canonical columns mapped more than once: {duplicates}")
        return self

    @property
    def header_hash(self) -> str:
        return header_hash(self.header)

    @property
    def targets(self) -> dict[str, str]:
        """canonical column → source column."""
        return {c.canonical_col: c.source_col for c in self.columns if c.canonical_col}

    @property
    def unmapped_required(self) -> list[str]:
        return sorted(REQUIRED - set(self.targets))


def header_hash(header: list[str]) -> str:
    """Order-sensitive: a reordered extract is a different header and needs its own confirmed mapping."""
    return hashlib.sha256(json.dumps(header).encode("utf-8")).hexdigest()
