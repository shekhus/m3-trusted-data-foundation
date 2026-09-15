"""LLM-assisted mapping: one call, fixed shape — deliberately not an agent (CLAUDE.md justification test).

The heuristic always runs first. The model sees the canonical schema, the transform vocabulary, the column
profiles (statistics and at most three example values per column — never raw rows), the heuristic proposal,
and optionally mappings an owner confirmed for other sources. Its JSON is parsed with the same contract as
every other proposal. An invalid answer is retried once with the validation error; a second failure, or any
provider error, returns the heuristic proposal flagged for review. The model can never apply a mapping.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from llm.client import InvalidOutput, LLMClient, LLMError, load_prompt
from pipeline.canonical import ORDER_LINE, Transform
from pipeline.mapper.contract import MappingProposal
from pipeline.mapper.heuristic import propose as heuristic_propose
from pipeline.profile import ColumnProfile, SourceProfile

PURPOSE = "mapping_proposal"
MAX_EXAMPLES_PER_COLUMN = 3
TRANSFORM_MEANINGS: dict[Transform, str] = {
    "trim": "strip leading/trailing whitespace",
    "parse_date_iso": "parse YYYY-MM-DD",
    "parse_date_us": "parse MM/DD/YYYY",
    "parse_date_dmy": "parse DD/MM/YYYY",
    "parse_excel_serial": "convert an Excel serial day number to a date",
    "kg_to_lb": "convert kilograms to pounds, keeping the original value",
    "normalize_customer_no": "rewrite a customer number to the master form C+6 digits, keeping the original",
}


@dataclass
class MapperResult:
    proposal: MappingProposal
    heuristic: MappingProposal
    used_llm: bool
    needs_review: bool
    notes: list[str] = field(default_factory=list)


def answer_schema(header: list[str]) -> dict:
    """JSON schema for the model's answer. Enums pin column and canonical names; the contract rechecks all."""
    canonical: list[str | None] = [f.name for f in ORDER_LINE]
    return {
        "type": "object",
        "properties": {
            "columns": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "source_col": {"type": "string", "enum": header},
                        "canonical_col": {"anyOf": [{"type": "string", "enum": canonical[:]},
                                                    {"type": "null"}]},
                        "confidence": {"type": "number"},
                        "rationale": {"type": "string"},
                        "transforms": {"type": "array",
                                       "items": {"type": "string", "enum": list(TRANSFORM_MEANINGS)}},
                    },
                    "required": ["source_col", "canonical_col", "confidence", "rationale", "transforms"],
                    "additionalProperties": False,
                },
            },
        },
        "required": ["columns"],
        "additionalProperties": False,
    }


def _column_facts(col: ColumnProfile) -> dict:
    facts: dict = {"name": col.name, "type": col.dtype, "blank_pct": col.null_pct,
                   "distinct_pct": col.distinct_pct, "whitespace_pct": col.whitespace_pct,
                   "shapes": [{"shape": s.shape, "share_pct": s.share_pct, "example": s.example}
                              for s in col.shapes[:MAX_EXAMPLES_PER_COLUMN]]}
    if col.min is not None:
        facts["min"], facts["max"] = col.min, col.max
    if col.date:
        facts["date"] = {"format": col.date.format, "min": str(col.date.min), "max": str(col.date.max)}
    if col.unit:
        facts["unit"] = col.unit.model_dump()
    if col.reference:
        facts["matches_master"] = {"master": col.reference.master, "pct": col.reference.trimmed_pct}
    return facts


def build_prompt(profile: SourceProfile, header: list[str], heuristic: MappingProposal,
                 confirmed_examples: list[MappingProposal]) -> str:
    by_name = {c.name: c for c in profile.columns}
    payload = {
        "canonical_schema": [{"name": f.name, "description": f.description, "required": f.required}
                             for f in ORDER_LINE],
        "transforms": TRANSFORM_MEANINGS,
        "source": {"header": header, "columns": [_column_facts(by_name[c]) for c in header]},
        "heuristic_proposal": [c.model_dump() for c in heuristic.columns],
        "confirmed_examples": [{"source": e.source, "mapping": e.targets} for e in confirmed_examples],
    }
    return json.dumps(payload, indent=1, default=str)


def propose(profile: SourceProfile, header: list[str], client: LLMClient | None,
            confirmed_examples: list[MappingProposal] | None = None) -> MapperResult:
    heuristic = heuristic_propose(profile, header)
    if client is None:
        return MapperResult(heuristic, heuristic, used_llm=False, needs_review=False,
                            notes=["LLM disabled; heuristic only"])

    system = load_prompt("mapping_system.md")
    user = build_prompt(profile, header, heuristic, confirmed_examples or [])
    schema = answer_schema(header)

    def parse(data: dict) -> MappingProposal:
        columns = data.get("columns") if isinstance(data, dict) else None
        return MappingProposal.model_validate({"source": profile.source, "header": header,
                                               "proposed_by": "llm", "columns": columns})

    notes: list[str] = []
    for attempt in (1, 2):  # one retry at most (CLAUDE.md)
        try:
            answer = client.complete_json(PURPOSE, system, user, schema, parse)
            return MapperResult(answer, heuristic, used_llm=True, needs_review=False, notes=notes)
        except InvalidOutput as exc:
            notes.append(f"attempt {attempt}: invalid output: {exc}"[:500])
            user = (f"{user}\n\nYour previous answer was rejected by validation:\n{exc}\n"
                    "Answer again following every rule.")
        except LLMError as exc:
            notes.append(f"attempt {attempt}: provider error: {exc}"[:500])
            break
    return MapperResult(heuristic, heuristic, used_llm=False, needs_review=True,
                        notes=[*notes, "fell back to the heuristic proposal; needs review"])
