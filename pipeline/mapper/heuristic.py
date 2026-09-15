"""Heuristic mapper: name similarity + dtype + value evidence from the profile. No LLM, no ground truth.

Score for (source column, canonical field) = 0.55 × name score + 0.45 × evidence score, where a hard type
mismatch (a date column for a quantity) rules the pair out. Pairs are assigned greedily from the best score
down, one-to-one; anything below MIN_SCORE stays unmapped and is left for the LLM or a person.

The abbreviation vocabulary is ordinary ERP shorthand (ord, qty, wt, cfm, dt, …). It was written while
looking at this project's three sources, so its accuracy on them is optimistic — evals/cases holds an
unseen-header case that measures how it generalises.
"""

from __future__ import annotations

import re
from functools import cache

from pipeline.canonical import BY_NAME, ORDER_LINE, CanonicalField, Transform
from pipeline.mapper.contract import ColumnMapping, MappingProposal
from pipeline.profile import ColumnProfile, SourceProfile

NAME_WEIGHT = 0.55
MIN_SCORE = 0.45
CANONICAL_CUSTOMER_SHAPE = "A999999"
DATE_TRANSFORMS: dict[str, Transform] = {
    "iso": "parse_date_iso", "us": "parse_date_us", "dmy": "parse_date_dmy",
    "excel_serial": "parse_excel_serial",
}

# spelling → concept
VOCABULARY: dict[str, str] = {
    **dict.fromkeys(["order", "orders", "ord", "ordered"], "order"),
    **dict.fromkeys(["line", "ln", "lne"], "line"),
    **dict.fromkeys(["no", "num", "number", "nbr", "nr", "id"], "no"),
    **dict.fromkeys(["customer", "cust", "cus", "client"], "customer"),
    **dict.fromkeys(["item", "itm", "sku", "material", "product", "prod"], "item"),
    **dict.fromkeys(["plant", "fac", "facility", "site"], "plant"),
    **dict.fromkeys(["date", "dt"], "date"),
    **dict.fromkeys(["requested", "request", "req", "wanted"], "requested"),
    **dict.fromkeys(["confirmed", "confirm", "cfm", "promise", "promised", "commit", "committed"],
                    "confirmed"),
    **dict.fromkeys(["delivery", "dlv", "deliv"], "delivery"),
    **dict.fromkeys(["issue", "issued", "iss"], "issue"),
    **dict.fromkeys(["ship", "shipped", "shipping"], "ship"),
    **dict.fromkeys(["invoiced", "invoice", "inv"], "invoiced"),
    **dict.fromkeys(["qty", "quantity", "qnty"], "qty"),
    **dict.fromkeys(["weight", "wt", "wgt"], "weight"),
    **dict.fromkeys(["uom", "unit", "measure"], "uom"),
    **dict.fromkeys(["lot", "batch"], "lot"),
}
IGNORED = {"of", "cd", "code", "kg", "kgs", "lb", "lbs"}  # units are evidence, not names


@cache
def _segment(token: str) -> tuple[str, ...]:
    """Split a run-together token ('ordernum', 'unitofmeasure') into vocabulary words when it fully covers."""
    if token in VOCABULARY or token in IGNORED:
        return (token,)
    for cut in range(len(token) - 1, 1, -1):
        head, tail = token[:cut], token[cut:]
        if head in VOCABULARY or head in IGNORED:
            rest = _segment(tail)
            if all(t in VOCABULARY or t in IGNORED for t in rest):
                return (head, *rest)
    return (token,)


def concepts(name: str) -> set[str]:
    raw = re.findall(r"[A-Z]?[a-z]+|[A-Z]+(?![a-z])|\d+", name)
    words = [w for token in raw for w in _segment(token.lower())]
    return {VOCABULARY.get(w, w) for w in words if w not in IGNORED}


def name_score(column: str, field: CanonicalField) -> float:
    """Dice overlap between the column's concepts and the field's concept groups."""
    found = concepts(column)
    if not found:
        return 0.0
    matched = sum(1 for group in field.concepts if found & set(group))
    return 2 * matched / (len(field.concepts) + len(found))


def evidence_score(col: ColumnProfile, field: CanonicalField) -> float:
    """0 rules the pair out. Uses only the profile: dtype, unit, master match, shapes, distinctness."""
    ref = col.reference.master if col.reference else None
    match field.kind:
        case "date":
            return 1.0 if col.dtype == "date" else 0.0
        case "weight":
            if col.dtype not in ("decimal", "integer"):
                return 0.0
            return 1.0 if col.unit else 0.3
        case "qty":
            if col.dtype not in ("decimal", "integer") or col.unit:
                return 0.0
            return 1.0 if col.dtype == "integer" else 0.6
        case "line":
            ok = col.dtype == "integer" and col.max is not None and float(col.max) < 1000
            return 1.0 if ok and ref is None else 0.0
        case "order_id":
            if ref or col.dtype not in ("string", "integer"):
                return 0.0
            return 1.0 if col.distinct_pct >= 5.0 else 0.2
        case "customer":
            if ref == "customers":
                return 1.0
            if ref or col.dtype not in ("string", "integer") or col.distinct_pct >= 5.0:
                return 0.0
            return 0.7
        case "item":
            if ref == "items":
                return 1.0
            return 0.4 if ref is None and col.dtype == "string" and col.distinct_pct < 5.0 else 0.0
        case "plant":
            if ref == "plants":
                return 1.0
            return 0.3 if ref is None and col.dtype == "string" and col.distinct_pct < 0.1 else 0.0
        case "uom":
            return 1.0 if ref is None and col.dtype == "string" and col.distinct_pct < 1.0 else 0.0
        case "lot":
            return 1.0 if ref is None and col.dtype == "string" and col.distinct_pct >= 50.0 else 0.0
    return 0.0


def transforms_for(col: ColumnProfile, field: CanonicalField) -> list[Transform]:
    steps: list[Transform] = []
    if col.whitespace_pct > 0:
        steps.append("trim")
    if field.kind == "date" and col.date:
        if col.date.format in DATE_TRANSFORMS:  # slash_ambiguous gets none: a person must pick US or DMY
            steps.append(DATE_TRANSFORMS[col.date.format])
    if field.kind == "weight" and col.unit and col.unit.unit == "kg":
        steps.append("kg_to_lb")
    top_shape = col.shapes[0].shape.rstrip("·") if col.shapes else ""
    if field.kind == "customer" and top_shape != CANONICAL_CUSTOMER_SHAPE:
        steps.append("normalize_customer_no")
    return steps


def _rationale(col: ColumnProfile, field: CanonicalField, name: float, evidence: float) -> str:
    notes = [f"name {name:.2f}", f"evidence {evidence:.2f} ({col.dtype}"]
    if col.reference:
        notes[-1] += f", {col.reference.trimmed_pct}% in {col.reference.master} master"
    if col.unit and col.unit.unit:
        notes[-1] += f", {col.unit.unit}"
    if col.date:
        notes[-1] += f", {col.date.format} dates"
    notes[-1] += ")"
    return f"→ {field.name}: " + "; ".join(notes)


def propose(profile: SourceProfile, header: list[str]) -> MappingProposal:
    """Map one header (a header variant of the profiled source)."""
    by_name = {c.name: c for c in profile.columns}
    scored: list[tuple[float, float, float, str, str]] = []
    for column in header:
        col = by_name[column]
        for field in ORDER_LINE:
            evidence = evidence_score(col, field)
            if evidence == 0.0:
                continue
            name = name_score(column, field)
            score = NAME_WEIGHT * name + (1 - NAME_WEIGHT) * evidence
            if score >= MIN_SCORE:
                scored.append((score, name, evidence, column, field.name))

    chosen: dict[str, ColumnMapping] = {}
    taken: set[str] = set()
    # best score first; ties broken by header order then canonical order, so output is deterministic
    order = {f.name: i for i, f in enumerate(ORDER_LINE)}
    for score, name, evidence, column, target in sorted(
            scored, key=lambda s: (-s[0], header.index(s[3]), order[s[4]])):
        if column in chosen or target in taken:
            continue
        col, field = by_name[column], BY_NAME[target]
        chosen[column] = ColumnMapping(source_col=column, canonical_col=target, confidence=round(score, 3),
                                       rationale=_rationale(col, field, name, evidence),
                                       transforms=transforms_for(col, field))
        taken.add(target)

    columns = [chosen.get(c) or ColumnMapping(source_col=c, canonical_col=None, confidence=0.0,
                                              rationale="no canonical column scored above the threshold")
               for c in header]
    return MappingProposal(source=profile.source, header=header, columns=columns, proposed_by="heuristic")


def propose_all(profile: SourceProfile) -> list[MappingProposal]:
    return [propose(profile, variant.columns) for variant in profile.header_variants]
