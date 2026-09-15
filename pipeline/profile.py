"""Profile raw source extracts before any mapping exists (docs/plan.md A2).

Everything here is inferred from the files and the master data alone. The profiler never reads
ground_truth/ — tests compare its output against the answer key, so reading it would be cheating.

Per column: dtype guess, null %, distinct %, min/max, value shapes, whitespace, date-format detection,
master-data match rate, and lb/kg inference from the header and from magnitude. Per source: header
variants across files, a candidate row key with its duplicate rate, and exact duplicate rows.
The profile is also the mapper's input (week 2), so it carries shapes and a few examples, not raw rows.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import date
from itertools import combinations
from pathlib import Path
from typing import Literal

import pandas as pd
from pydantic import BaseModel

EXCEL_EPOCH = date(1899, 12, 30)
SERIAL_RANGE = (36526, 55153)  # Excel serials for 2000-01-01 .. 2050-12-31
MATCH_SHARE = 0.98  # share of non-null values a pattern must cover to classify a column
LB_PER_KG = 2.2046226218
UNIT_LOG_TOLERANCE = 0.15  # magnitude ratio must be within ~15% of lb (1.0) or kg (1/2.2046)
KEY_MAX_DUPLICATE_PCT = 2.0
TOP_SHAPES = 3
RARE_SHAPE_PCT = 5.0  # a non-dominant shape below this share is reported as a finding

ISO_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
SLASH_RE = re.compile(r"^(\d{1,2})/(\d{1,2})/(\d{4})$")
INT_RE = re.compile(r"^-?\d+(?:\.0+)?$")
NUM_RE = re.compile(r"^-?\d+(?:\.\d+)?$")

DType = Literal["integer", "decimal", "date", "string", "empty"]
DateFormat = Literal["iso", "us", "dmy", "slash_ambiguous", "excel_serial"]
Unit = Literal["lb", "kg"]


class ValueShape(BaseModel):
    shape: str  # digits → 9, letters → A, everything else kept; trailing whitespace shown as ·
    share_pct: float
    example: str


class DateProfile(BaseModel):
    format: DateFormat
    parsed_pct: float  # of non-null values
    min: date | None
    max: date | None


class ReferenceMatch(BaseModel):
    master: str
    exact_pct: float
    trimmed_pct: float


class UnitInference(BaseModel):
    unit: Unit | None
    header_unit: Unit | None
    magnitude_unit: Unit | None
    magnitude_ratio: float | None  # median(value / (qty × item avg lb per case)); ≈1 lb, ≈0.4536 kg
    conflict: bool


class ColumnProfile(BaseModel):
    name: str
    present_in_files: int
    dtype: DType
    null_pct: float  # over rows of the files that contain the column
    distinct_pct: float
    whitespace_pct: float
    negative_count: int | None
    min: str | None
    max: str | None
    shapes: list[ValueShape]
    date: DateProfile | None
    reference: ReferenceMatch | None
    unit: UnitInference | None


class HeaderVariant(BaseModel):
    columns: list[str]
    first_file: str
    last_file: str
    file_count: int
    added: list[str]
    removed: list[str]


class KeyProfile(BaseModel):
    columns: list[str]
    duplicate_rows: int
    duplicate_pct: float


class SourceProfile(BaseModel):
    source: str
    file_count: int
    rows: int
    header_variants: list[HeaderVariant]
    columns: list[ColumnProfile]
    candidate_key: KeyProfile | None
    exact_duplicate_rows: int
    findings: list[str]


@dataclass(frozen=True)
class Masters:
    keys: dict[str, set[str]]  # master name → key values, e.g. "items" → {"IT00001", ...}
    item_lb_per_case: dict[str, float]


def load_masters(master_dir: Path) -> Masters:
    customers = pd.read_csv(master_dir / "customers.csv", dtype=str)
    items = pd.read_csv(master_dir / "items.csv", dtype=str)
    plants = pd.read_csv(master_dir / "plants.csv", dtype=str)
    return Masters(
        keys={
            "customers": set(customers["customer_no"]),
            "items": set(items["item_no"]),
            "plants": set(plants["plant"]),
        },
        item_lb_per_case=dict(zip(items["item_no"], items["avg_lb_per_case"].astype(float), strict=True)),
    )


def _pct(part: float, whole: float) -> float:
    return round(100.0 * part / whole, 3) if whole else 0.0


def _share(mask: pd.Series) -> float:
    return float(mask.mean()) if len(mask) else 0.0


def _fmt_number(value: float, dtype: DType) -> str:
    return str(int(value)) if dtype == "integer" else str(value)


def _header_unit(name: str) -> Unit | None:
    tokens = {t.lower() for t in re.findall(r"[A-Z]?[a-z]+|[A-Z]+(?![a-z])|\d+", name)}
    if tokens & {"kg", "kgs", "kilo", "kilos", "kilogram", "kilograms"}:
        return "kg"
    if tokens & {"lb", "lbs", "pound", "pounds"}:
        return "lb"
    return None


def _shape(values: pd.Series) -> pd.Series:
    trailing = values.str.len() - values.str.rstrip().str.len()
    body = values.str.rstrip().str.replace(r"\d", "9", regex=True).str.replace(r"[A-Za-z]", "A", regex=True)
    return body + trailing.map(lambda n: "·" * int(n))


def _classify(values: pd.Series) -> tuple[DType, DateProfile | None]:
    """values: stripped, non-null strings."""
    if values.empty:
        return "empty", None
    if _share(values.str.match(ISO_RE)) >= MATCH_SHARE:
        parsed = pd.to_datetime(values, format="%Y-%m-%d", errors="coerce")
        return "date", _date_profile("iso", parsed)
    slash = values.str.extract(SLASH_RE)
    if _share(slash[0].notna()) >= MATCH_SHARE:
        first, second = pd.to_numeric(slash[0]), pd.to_numeric(slash[1])
        fmt: DateFormat
        if (second > 12).any() and not (first > 12).any():
            fmt, pattern = "us", "%m/%d/%Y"
        elif (first > 12).any() and not (second > 12).any():
            fmt, pattern = "dmy", "%d/%m/%Y"
        else:
            fmt, pattern = "slash_ambiguous", "%m/%d/%Y"
        return "date", _date_profile(fmt, pd.to_datetime(values, format=pattern, errors="coerce"))
    if _share(values.str.match(INT_RE)) >= MATCH_SHARE:
        numbers = pd.to_numeric(values, errors="coerce")
        in_range = numbers.between(*SERIAL_RANGE)
        if _share(in_range) >= MATCH_SHARE:
            serial = pd.to_datetime(EXCEL_EPOCH) + pd.to_timedelta(numbers.where(in_range), unit="D")
            return "date", _date_profile("excel_serial", serial)
        return "integer", None
    if _share(values.str.match(NUM_RE)) >= MATCH_SHARE:
        return "decimal", None
    return "string", None


def _date_profile(fmt: DateFormat, parsed: pd.Series) -> DateProfile:
    ok = parsed.dropna()
    return DateProfile(
        format=fmt,
        parsed_pct=_pct(len(ok), len(parsed)),
        min=ok.min().date() if len(ok) else None,
        max=ok.max().date() if len(ok) else None,
    )


def _reference(values: pd.Series, masters: Masters) -> ReferenceMatch | None:
    """Best master whose keys cover most of the column after trimming; None below MATCH_SHARE × 90%."""
    best: ReferenceMatch | None = None
    for name, keys in sorted(masters.keys.items()):
        trimmed = _pct(values.str.strip().isin(keys).sum(), len(values))
        if trimmed >= 90.0 * MATCH_SHARE and (best is None or trimmed > best.trimmed_pct):
            best = ReferenceMatch(master=name, exact_pct=_pct(values.isin(keys).sum(), len(values)),
                                  trimmed_pct=trimmed)
    return best


def _magnitude_units(df: pd.DataFrame, columns: list[ColumnProfile], masters: Masters) -> dict[str, float]:
    """For decimal columns: median of value / (qty × item avg lb per case), using the item column the
    master identifies and whichever integer column gives the most unit-like ratio."""
    item_col = next((c.name for c in columns if c.reference and c.reference.master == "items"), None)
    if item_col is None:
        return {}
    lb_per_case = df[item_col].str.strip().map(masters.item_lb_per_case)
    qty_cols = [c.name for c in columns if c.dtype == "integer" and c.reference is None]
    ratios: dict[str, float] = {}
    for col in (c.name for c in columns if c.dtype == "decimal"):
        value = pd.to_numeric(df[col], errors="coerce")
        best: tuple[float, float] | None = None  # (distance to nearest unit, ratio)
        for qty_col in qty_cols:
            qty = pd.to_numeric(df[qty_col], errors="coerce")
            ratio = (value / (qty * lb_per_case)).where((qty > 0) & (value > 0)).dropna()
            if len(ratio) < 30:
                continue
            median = float(ratio.median())
            distance = min(abs(math.log(median)), abs(math.log(median * LB_PER_KG)))
            if best is None or distance < best[0]:
                best = (distance, median)
        if best is not None:
            ratios[col] = round(best[1], 4)
    return ratios


def _unit(name: str, ratio: float | None) -> UnitInference | None:
    header = _header_unit(name)
    magnitude: Unit | None = None
    if ratio is not None and ratio > 0:
        if abs(math.log(ratio)) <= UNIT_LOG_TOLERANCE:
            magnitude = "lb"
        elif abs(math.log(ratio * LB_PER_KG)) <= UNIT_LOG_TOLERANCE:
            magnitude = "kg"
    if header is None and magnitude is None:
        return None
    conflict = header is not None and magnitude is not None and header != magnitude
    return UnitInference(unit=None if conflict else (header or magnitude), header_unit=header,
                         magnitude_unit=magnitude, magnitude_ratio=ratio, conflict=conflict)


def _candidate_key(df: pd.DataFrame, columns: list[ColumnProfile]) -> KeyProfile | None:
    """One- or two-column set with the fewest duplicate rows (at most KEY_MAX_DUPLICATE_PCT).

    Fewest duplicates wins, then fewer columns, then earliest columns. Preferring single columns outright
    would pick a near-unique attribute (a lot number) over the real (order, line) key.
    """
    candidates = [c.name for c in columns
                  if c.dtype in ("string", "integer") and c.null_pct < 1.0 and c.present_in_files > 0]
    stripped = df[candidates].apply(lambda s: s.str.strip())
    best: KeyProfile | None = None
    for size in (1, 2):
        for combo in combinations(candidates, size):
            dups = int(stripped.duplicated(subset=list(combo), keep="first").sum())
            pct = _pct(dups, len(df))
            if pct <= KEY_MAX_DUPLICATE_PCT and (best is None or dups < best.duplicate_rows):
                best = KeyProfile(columns=list(combo), duplicate_rows=dups, duplicate_pct=pct)
    return best


def _header_variants(headers: list[tuple[str, list[str]]]) -> list[HeaderVariant]:
    variants: list[HeaderVariant] = []
    for file_name, cols in headers:
        if variants and variants[-1].columns == cols:
            last = variants[-1]
            variants[-1] = last.model_copy(update={"last_file": file_name, "file_count": last.file_count + 1})
            continue
        previous = variants[-1].columns if variants else cols
        variants.append(HeaderVariant(
            columns=cols, first_file=file_name, last_file=file_name, file_count=1,
            added=[c for c in cols if c not in previous], removed=[c for c in previous if c not in cols]))
    return variants


def profile_source(source: str, files: list[Path], masters: Masters) -> SourceProfile:
    frames: list[pd.DataFrame] = []
    headers: list[tuple[str, list[str]]] = []
    for path in sorted(files):
        frame = pd.read_csv(path, dtype=str, keep_default_na=False, encoding="utf-8")
        headers.append((path.name, list(frame.columns)))
        frames.append(frame)
    df = pd.concat(frames, ignore_index=True)  # a column absent from a file is NaN; blank in a file is ""

    columns: list[ColumnProfile] = []
    for name in df.columns:
        raw = df[name].dropna()
        present_in = sum(1 for _, cols in headers if name in cols)
        blank = raw.str.strip() == ""
        values = raw[~blank]
        stripped = values.str.strip()
        dtype, date_profile = _classify(stripped)
        numeric = pd.to_numeric(stripped, errors="coerce") if dtype in ("integer", "decimal") else None
        shapes = _shape(values).value_counts() if dtype == "string" else pd.Series(dtype=int)
        columns.append(ColumnProfile(
            name=name,
            present_in_files=present_in,
            dtype=dtype,
            null_pct=_pct(blank.sum(), len(raw)),
            distinct_pct=_pct(stripped.nunique(), len(values)),
            whitespace_pct=_pct((values != stripped).sum(), len(values)),
            negative_count=int((numeric < 0).sum()) if numeric is not None else None,
            min=_fmt_number(numeric.min(), dtype) if numeric is not None and numeric.notna().any() else None,
            max=_fmt_number(numeric.max(), dtype) if numeric is not None and numeric.notna().any() else None,
            shapes=[ValueShape(shape=str(s), share_pct=_pct(n, len(values)),
                               example=str(values[_shape(values) == s].iloc[0]))
                    for s, n in sorted(shapes.items(), key=lambda kv: (-kv[1], kv[0]))[:TOP_SHAPES]],
            date=date_profile,
            reference=_reference(values, masters) if dtype in ("string", "integer") else None,
            unit=None,
        ))

    ratios = _magnitude_units(df, columns, masters)
    columns = [c.model_copy(update={"unit": _unit(c.name, ratios.get(c.name))}) if c.dtype == "decimal" else c
               for c in columns]
    key = _candidate_key(df, columns)
    exact_dups = int(df.fillna("").duplicated(keep="first").sum())
    variants = _header_variants(headers)
    profile = SourceProfile(source=source, file_count=len(files), rows=len(df), header_variants=variants,
                            columns=columns, candidate_key=key, exact_duplicate_rows=exact_dups, findings=[])
    return profile.model_copy(update={"findings": findings(profile)})


def findings(p: SourceProfile) -> list[str]:
    """Plain-language observations a reviewer should act on. Derived only from the profile."""
    out: list[str] = []
    for v in p.header_variants[1:]:
        change = "; ".join(filter(None, [
            f"added {', '.join(v.added)}" if v.added else "",
            f"removed {', '.join(v.removed)}" if v.removed else "",
        ]))
        out.append(f"Header changes from {v.first_file}: {change}. "
                   "Possible schema drift — confirm before mapping.")
    dates = [c for c in p.columns if c.date]
    for fmt in sorted({c.date.format for c in dates if c.date}):
        cols = [c.name for c in dates if c.date and c.date.format == fmt]
        out.append(f"Dates are {fmt} in {', '.join(cols)}.")
    for c in dates:
        if c.date and c.date.parsed_pct < 100.0:
            bad = 100 - c.date.parsed_pct
            out.append(f"{c.name}: {bad:.3f}% of non-blank values do not parse as {c.date.format}.")
    for c in p.columns:
        if c.unit and c.unit.conflict:
            u = c.unit
            out.append(f"{c.name}: header says {u.header_unit} but magnitude says {u.magnitude_unit}.")
    units = {c.unit.unit for c in p.columns if c.unit and c.unit.unit}
    for unit in sorted(units):
        cols = [c.name for c in p.columns if c.unit and c.unit.unit == unit]
        note = " Canonical weights are lb: convert and log the original value." if unit == "kg" else ""
        out.append(f"Weights look like {unit} in {', '.join(cols)}.{note}")
    for c in p.columns:
        if c.whitespace_pct > 0:
            out.append(f"{c.name}: {c.whitespace_pct}% of values carry leading/trailing whitespace.")
        top = c.shapes[0].shape.rstrip("·") if c.shapes else ""
        for s in c.shapes[1:]:
            if s.shape.rstrip("·") != top and s.share_pct < RARE_SHAPE_PCT:
                out.append(f"{c.name}: {s.share_pct}% of values have the rare shape `{s.shape}` "
                           f"(e.g. {s.example.strip()!r}).")
        if c.null_pct > 0:
            out.append(f"{c.name}: {c.null_pct}% blank.")
        if c.negative_count:
            out.append(f"{c.name}: {c.negative_count} negative values.")
        if c.reference and c.reference.trimmed_pct < 100.0:
            missing = 100 - c.reference.trimmed_pct
            out.append(f"{c.name}: {missing:.3f}% of values not in the {c.reference.master} master.")
    if not any(c.reference and c.reference.master == "customers" for c in p.columns):
        out.append("No column matches the customer master as-is — customer numbers are likely reformatted.")
    if p.candidate_key:
        k = p.candidate_key
        out.append(f"Candidate row key ({', '.join(k.columns)}): {k.duplicate_rows} duplicate rows "
                   f"({k.duplicate_pct}%).")
    else:
        out.append("No one- or two-column key is unique enough to identify rows.")
    if p.exact_duplicate_rows:
        n = p.exact_duplicate_rows
        out.append(f"{n} rows are exact duplicates of an earlier row." if n > 1
                   else "1 row is an exact duplicate of an earlier row.")
    return out


def discover_sources(sources_dir: Path) -> dict[str, list[Path]]:
    return {d.name: sorted(d.glob("*.csv")) for d in sorted(sources_dir.iterdir()) if d.is_dir()}


def profile_all(sources_dir: Path, master_dir: Path) -> list[SourceProfile]:
    masters = load_masters(master_dir)
    sources = discover_sources(sources_dir)
    return [profile_source(name, files, masters) for name, files in sources.items() if files]


def render_markdown(profiles: list[SourceProfile]) -> str:
    lines = [
        "# Source profiling findings",
        "",
        "_Generated by `make profile` (`pipeline/profile.py`) from `data/sources/` and `data/master/`. "
        "Do not edit by hand._",
        "",
        "Inferred from the files alone — no mapping and no ground truth is used. Shapes: digit → 9, "
        "letter → A, trailing space → ·.",
        "",
    ]
    for p in profiles:
        lines += [f"## {p.source}", "", f"{p.file_count} files, {p.rows:,} rows.", "", "### Findings", ""]
        lines += [f"- {f}" for f in p.findings]
        lines += ["", "### Columns", "",
                  "| Column | Files | Type | Blank % | Distinct % | Space % | Min | Max | Top shapes "
                  "| Detected |",
                  "|---|---|---|---|---|---|---|---|---|---|"]
        for c in p.columns:
            shapes = ", ".join(f"`{s.shape}` {s.share_pct}%" for s in c.shapes)
            detected = []
            if c.date:
                detected.append(f"date {c.date.format} {c.date.min}..{c.date.max}")
            if c.reference:
                detected.append(f"{c.reference.master} {c.reference.trimmed_pct}%")
            if c.unit:
                ratio = f" (ratio {c.unit.magnitude_ratio})" if c.unit.magnitude_ratio is not None else ""
                detected.append(f"{c.unit.unit or 'conflict'}{ratio}")
            lines.append(f"| `{c.name}` | {c.present_in_files}/{p.file_count} | {c.dtype} | {c.null_pct} "
                         f"| {c.distinct_pct} | {c.whitespace_pct} | {c.min or ''} | {c.max or ''} "
                         f"| {shapes} | {'; '.join(detected)} |")
        lines.append("")
    return "\n".join(lines)

