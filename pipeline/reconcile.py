"""Reconciliation job (docs/plan.md A10) — fixed sequence, no LLM.

1. Consumer views: for each pair in metrics/consumers/consumers.yaml, roll both current consumer views up to
   the shared grain by summing numerator and denominator, and compare the rates (green when they agree to
   1e-9 — they read one definition, so any difference is a defect). The legacy view's gap is recorded as
   the "before".
2. BI tools: explain why each tool disagrees with governed OTIF, per month, in named causes.
   - Which choices a tool made is identified from its own export, never from the answer key: the legacy report
     is line level, so its on-time and in-full verdicts are matched line by line against the governed and
     reference flags; the dashboard export is month × plant × customer, so every combination of date basis,
     in-full basis and consignment scope is aggregated and the one that reproduces the export is kept.
   - When several definitions reproduce an export equally well, causes they share are "identified" and causes
     only some of them make are "undetermined" — sized all the same, but reported as not confirmed by the data
     (e.g. a 2% case tolerance cannot show up when no line is exactly one case short on 50+ cases).
   - Each identified cause is sized as a single-switch delta from governed (the reference definitions in
     metrics/*.yaml). Switches interact, so contributions are reported side by side, never summed into the
     gap.
   Order type is not in any plant extract; for scope checks it is read from the legacy report's line export,
   used here only and never written to gold.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass, field
from itertools import product
from pathlib import Path

import pandas as pd
from sqlalchemy import Connection, Engine, text

from metrics.compiler import CompiledConsumers, apply, apply_consumers, compile_all, compile_consumers

GAP_THRESHOLD = 0.001
AGREE = 1e-9
TOOL1_FILE = "report_feed/tool1_legacy_delivery_report.csv"
TOOL2_FILE = "report_feed/tool2_dashboard_otif_export.csv"
ON_TIME = {"confirmed": "gov_on_time", "requested": "req_on_time"}
IN_FULL = {"governed": "gov_in_full", "count": "count_in_full", "count_tolerance": "tol_in_full"}


CAUSE_ORDER = ["weight_basis", "date_basis", "case_tolerance", "scope_filter"]


def causes_of(on_time: str, in_full: str, excludes_consignment: bool) -> set[str]:
    causes = set()
    if in_full != "governed":
        causes.add("weight_basis")
    if on_time == "requested":
        causes.add("date_basis")
    if in_full == "count_tolerance":
        causes.add("case_tolerance")
    if excludes_consignment:
        causes.add("scope_filter")
    return causes


@dataclass
class ToolDefinition:
    """The best-matching definitions of a tool: the causes all of them share, and those only some make."""

    candidates: list[tuple[str, str, bool]]
    match_share: float

    def _sets(self) -> list[set[str]]:
        return [causes_of(*c) for c in self.candidates]

    @property
    def causes(self) -> list[str]:
        sets = self._sets()
        common = sets[0].intersection(*sets[1:]) if sets else set()
        return [c for c in CAUSE_ORDER if c in common]

    @property
    def undetermined(self) -> list[str]:
        sets = self._sets()
        some = sets[0].union(*sets[1:]) - sets[0].intersection(*sets[1:]) if sets else set()
        return [c for c in CAUSE_ORDER if c in some]


@dataclass
class RunResult:
    run_id: int
    status: str
    consumer_red: int
    consumer_periods: int
    tools: dict[str, dict] = field(default_factory=dict)


def ensure_views(conn: Connection) -> CompiledConsumers:
    metrics = compile_all()
    apply(conn, metrics)
    consumers = compile_consumers(metrics)
    apply_consumers(conn, consumers)
    return consumers


def _rollup(
    conn: Connection, view: str, group_by: list[str], grain: Sequence[str], num: str, den: str
) -> pd.DataFrame:
    exprs = [
        "to_char(day, 'YYYY-MM') AS month" if g == "month" and "month" not in group_by else g for g in grain
    ]
    rows = (
        conn.execute(
            text(
                f"SELECT {', '.join(exprs)}, sum({num}) AS num, sum({den}) AS den FROM {view} "
                f"GROUP BY {', '.join(str(i) for i in range(1, len(grain) + 1))}"
            )
        )
        .mappings()
        .all()
    )
    return pd.DataFrame([dict(r) for r in rows], columns=[*grain, "num", "den"])


def _compare_consumers(conn: Connection, run_id: int, consumers: CompiledConsumers) -> tuple[int, int]:
    red = total = 0
    for pair in consumers.catalog.reconcile:
        frames = {}
        for side in (pair.a, pair.b, pair.legacy):
            if side is None:
                continue
            c = consumers.consumer(side)
            df = _rollup(conn, c.view, c.group_by, pair.grain, pair.numerator, pair.denominator)
            df[side] = df["num"].astype(float) / df["den"].astype(float).where(df["den"].astype(float) != 0)
            frames[side] = df[[*pair.grain, side]]
        merged = frames[pair.a].merge(frames[pair.b], on=pair.grain, how="outer")
        if pair.legacy:
            merged = merged.merge(frames[pair.legacy], on=pair.grain, how="left")
        rows = []
        for r in merged.to_dict("records"):
            a, b = r[pair.a], r[pair.b]
            delta = None if pd.isna(a) or pd.isna(b) else float(a) - float(b)
            status = "green" if delta is not None and abs(delta) <= AGREE else "red"
            legacy = r.get(pair.legacy) if pair.legacy else None
            rows.append(
                {
                    "run": run_id,
                    "metric": pair.metric,
                    "period": r["month"],
                    "key": ",".join(f"{g}={r[g]}" for g in pair.grain if g != "month") or "all",
                    "a": pair.a,
                    "va": _f(a),
                    "b": pair.b,
                    "vb": _f(b),
                    "delta": delta,
                    "legacy": pair.legacy,
                    "vl": _f(legacy),
                    "ld": None
                    if legacy is None or pd.isna(legacy) or pd.isna(a)
                    else float(legacy) - float(a),
                    "status": status,
                }
            )
            red += status == "red"
            total += 1
        if rows:
            conn.execute(
                text(
                    "INSERT INTO ops.reconciliation (run_id, metric, period, grain_key, consumer_a, value_a, "
                    "consumer_b, value_b, delta, legacy, value_legacy, legacy_delta, status) "
                    "VALUES (:run, :metric, :period, :key, "
                    ":a, :va, :b, :vb, :delta, :legacy, :vl, :ld, :status)"
                ),
                rows,
            )
    return red, total


def _f(value: object) -> float | None:
    return None if value is None or pd.isna(value) else float(value)  # type: ignore[arg-type]


def line_flags(conn: Connection, data_dir: Path) -> pd.DataFrame:
    """Per gold line: governed and reference flags, plus order type from the legacy report's line export."""
    rows = (
        conn.execute(
            text(
                "SELECT v.order_no, v.line_no, to_char(v.metric_date, 'YYYY-MM') AS month, v.plant, "
                "v.customer_no, "
                "v.on_time AS gov_on_time, v.in_full AS gov_in_full, v.in_full_count AS count_in_full, "
                "d.on_time AS req_on_time, t.in_full AS tol_in_full "
                "FROM gold.otif_v3_lines v JOIN gold.otif_date_basis_v1_lines d USING (order_no, line_no) "
                "JOIN gold.otif_case_tolerance_v1_lines t USING (order_no, line_no)"
            )
        )
        .mappings()
        .all()
    )
    flags = pd.DataFrame([dict(r) for r in rows])
    if flags.empty:
        return flags
    feed = pd.read_csv(
        data_dir / TOOL1_FILE,
        dtype=str,
        usecols=["Customer Order Number", "Order Line Number", "Customer Order Type"],
    )
    feed = feed.rename(
        columns={
            "Customer Order Number": "order_no",
            "Order Line Number": "line_no",
            "Customer Order Type": "order_type",
        }
    )
    feed["line_no"] = feed["line_no"].astype(int)
    flags["line_no"] = flags["line_no"].astype(int)
    return flags.merge(feed.drop_duplicates(["order_no", "line_no"]), on=["order_no", "line_no"], how="left")


def _identify_tool1(flags: pd.DataFrame, feed: pd.DataFrame) -> ToolDefinition:
    joined = flags.merge(feed, on=["order_no", "line_no"], how="inner")
    if joined.empty:
        return ToolDefinition([], 0.0)
    on_time = joined["On Time"] == "Y"
    in_full = joined["Case Fill Rate max. 100%"].astype(float) >= 1.0
    on_agree = {k: float((joined[col] == on_time).mean()) for k, col in ON_TIME.items()}
    full_agree = {k: float((joined[col] == in_full).mean()) for k, col in IN_FULL.items()}
    ons = [k for k, v in on_agree.items() if v == max(on_agree.values())]
    fulls = [k for k, v in full_agree.items() if v == max(full_agree.values())]
    excludes = bool(flags["order_type"].eq("CONS").any() and not joined["order_type"].eq("CONS").any())
    share = min(max(on_agree.values()), max(full_agree.values()))
    return ToolDefinition([(o, f, excludes) for o in ons for f in fulls], share)


def _identify_tool2(flags: pd.DataFrame, export: pd.DataFrame) -> ToolDefinition:
    target = export.rename(columns={"Month": "month", "Facility": "plant", "CustomerID": "customer_no"})
    keys = ["month", "plant", "customer_no"]
    scores: dict[tuple[str, str, bool], float] = {}
    for on, full, cons in product(ON_TIME, IN_FULL, (False, True)):
        scope = flags[~flags["order_type"].eq("CONS")] if cons else flags
        agg = (
            scope.assign(otif=scope[ON_TIME[on]] & scope[IN_FULL[full]])
            .groupby(keys)
            .agg(Lines=("otif", "size"), OTIFLines=("otif", "sum"))
            .reset_index()
        )
        merged = target.merge(agg, on=keys, how="left", suffixes=("", "_ours"))
        share = float(
            (
                (merged["Lines"] == merged["Lines_ours"]) & (merged["OTIFLines"] == merged["OTIFLines_ours"])
            ).mean()
        )
        scores[(on, full, cons)] = share
    best = max(scores.values())
    return ToolDefinition([c for c, s in scores.items() if s == best], best)


def _attribute(
    flags: pd.DataFrame, tool: str, definition: ToolDefinition, measured: pd.DataFrame, run_id: int
) -> list[dict]:
    sized = set(definition.causes) | set(definition.undetermined)
    rows = []
    for month, g in flags.groupby("month"):
        governed = float((g["gov_on_time"] & g["gov_in_full"]).mean())
        m = measured.loc[measured["month"] == month]
        tool_lines = int(m["lines"].iloc[0]) if len(m) else 0
        rate = float(m["rate"].iloc[0]) if len(m) else None
        contributions: dict[str, float | int] = {}
        if "weight_basis" in sized:
            contributions["weight_basis"] = round(
                float((g["gov_on_time"] & g["count_in_full"]).mean()) - governed, 5
            )
        if "date_basis" in sized:
            contributions["date_basis"] = round(
                float((g["req_on_time"] & g["gov_in_full"]).mean()) - governed, 5
            )
        if "case_tolerance" in sized:
            contributions["case_tolerance"] = round(
                float((g["gov_on_time"] & g["tol_in_full"]).mean()) - governed, 5
            )
        if "scope_filter" in sized:
            contributions["scope_filter_lines_dropped"] = len(g) - tool_lines
        rates = {
            k: v
            for k, v in contributions.items()
            if k != "scope_filter_lines_dropped" and k in definition.causes
        }
        gap = None if rate is None else rate - governed
        rows.append(
            {
                "run": run_id,
                "tool": tool,
                "period": month,
                "lg": len(g),
                "lt": tool_lines,
                "governed": governed,
                "measured": rate,
                "gap": gap,
                "flagged": gap is not None and abs(gap) > GAP_THRESHOLD,
                "contributions": json.dumps(contributions),
                "dominant": max(rates, key=lambda k: abs(rates[k])) if rates else None,
            }
        )
    return rows


def _tools(conn: Connection, run_id: int, data_dir: Path) -> dict[str, dict]:
    flags = line_flags(conn, data_dir)
    if flags.empty:
        return {}
    feed = pd.read_csv(data_dir / TOOL1_FILE, dtype=str)
    feed = feed.rename(columns={"Customer Order Number": "order_no", "Order Line Number": "line_no"})
    feed["line_no"] = feed["line_no"].astype(int)
    feed["month"] = feed["Issue Date"].str[:7]
    feed["otif"] = (feed["On Time"] == "Y") & (feed["Case Fill Rate max. 100%"].astype(float) >= 1.0)
    tool1_measured = feed.groupby("month").agg(lines=("otif", "size"), rate=("otif", "mean")).reset_index()
    export = pd.read_csv(data_dir / TOOL2_FILE)
    sums = export.groupby("Month")[["Lines", "OTIFLines"]].sum()
    tool2_measured = pd.DataFrame(
        {
            "month": sums.index,
            "lines": sums["Lines"].to_numpy(),
            "rate": (sums["OTIFLines"] / sums["Lines"]).to_numpy(),
        }
    )

    definitions = {
        "tool1_legacy_report_suite": _identify_tool1(
            flags, feed[["order_no", "line_no", "On Time", "Case Fill Rate max. 100%"]]
        ),
        "tool2_dashboard_tool": _identify_tool2(flags, export),
    }
    measured = {"tool1_legacy_report_suite": tool1_measured, "tool2_dashboard_tool": tool2_measured}
    summary = {}
    for tool, definition in definitions.items():
        rows = _attribute(flags, tool, definition, measured[tool], run_id)
        if rows:
            conn.execute(
                text(
                    "INSERT INTO ops.reconciliation_tools (run_id, tool, period, lines_governed, lines_tool, "
                    "governed, measured, gap, gap_flagged, contributions, dominant_cause) "
                    "VALUES (:run, :tool, :period, :lg, :lt, "
                    ":governed, :measured, :gap, :flagged, CAST(:contributions AS jsonb), :dominant)"
                ),
                rows,
            )
        dominant = pd.Series([r["dominant"] for r in rows]).mode()
        summary[tool] = {
            "identified_causes": definition.causes,
            "undetermined_causes": definition.undetermined,
            "matching_definitions": [
                {"on_time": o, "in_full": f, "excludes_consignment": x} for o, f, x in definition.candidates
            ],
            "export_match_share": round(definition.match_share, 4),
            "dominant_cause": None if dominant.empty else dominant.iloc[0],
            "months_flagged": sum(r["flagged"] for r in rows),
            "months": len(rows),
        }
    return summary


def run(engine: Engine, data_dir: Path, actor: str) -> RunResult:
    with engine.begin() as conn:
        consumers = ensure_views(conn)
        run_id = int(
            conn.execute(
                text(
                    "INSERT INTO ops.reconciliation_runs (actor, status) VALUES (:a, 'no_data') "
                    "RETURNING run_id"
                ),
                {"a": actor},
            ).scalar_one()
        )
        red, total = _compare_consumers(conn, run_id, consumers)
        tools = _tools(conn, run_id, data_dir) if total else {}
        status = "no_data" if total == 0 else ("red" if red else "green")
        summary = {"consumer_periods_compared": total, "consumer_red": red, "tools": tools}
        conn.execute(
            text(
                "UPDATE ops.reconciliation_runs SET status = :s, summary = CAST(:sum AS jsonb) "
                "WHERE run_id = :id"
            ),
            {"s": status, "sum": json.dumps(summary), "id": run_id},
        )
    return RunResult(run_id, status, red, total, tools)
