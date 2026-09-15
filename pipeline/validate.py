"""Run every rule over a batch's silver rows and keep ops.exceptions in step with the result.

Deterministic and re-runnable: exceptions are keyed (batch, rule, source_row), so a second run inserts nothing
new; an open exception whose row no longer violates its rule (e.g. after a corrected mapping rebuilt silver)
is resolved by the system with that reason — never deleted. Nothing is changed or dropped in silver. The batch
records which rules ran on how many rows, so validation coverage is measured, not assumed.
"""

from __future__ import annotations

import json
import string
from functools import lru_cache
from pathlib import Path

import pandas as pd
from sqlalchemy import Connection, text

from pipeline.canonical import ORDER_LINE
from rules import RULES, Masters, RuleContext

SYSTEM = "system:revalidation"
DATE_COLUMNS = [f.name for f in ORDER_LINE if f.kind == "date"]
NUMBER_COLUMNS = [f.name for f in ORDER_LINE if f.kind in ("qty", "weight")]


@lru_cache(maxsize=4)
def _masters_cached(master_dir: str, stamp: tuple[int, ...]) -> Masters:
    root = Path(master_dir)
    customers = pd.read_csv(root / "customers.csv", dtype=str)
    items = pd.read_csv(root / "items.csv", dtype=str)
    lots = pd.read_csv(root / "lot_master.csv", dtype=str).drop_duplicates("lot_no").set_index("lot_no")
    lots["expiry_date"] = pd.to_datetime(lots["expiry_date"])
    return Masters(
        customers=frozenset(customers["customer_no"]), items=frozenset(items["item_no"]),
        item_uom=dict(zip(items["item_no"], items["uom"].str.upper(), strict=True)),
        item_lb_per_case=dict(zip(items["item_no"], items["avg_lb_per_case"].astype(float), strict=True)),
        lots=lots)


def load_masters(master_dir: Path) -> Masters:
    """Cached per master directory, refreshed when any master file changes."""
    names = ("customers.csv", "items.csv", "lot_master.csv")
    return _masters_cached(str(master_dir), tuple((master_dir / n).stat().st_mtime_ns for n in names))


class _Defaulting(dict):
    def __missing__(self, key: str) -> str:
        return "?"


def _format(template: str, values: dict) -> str:
    try:
        return string.Formatter().vformat(template, (), _Defaulting(values))
    except (ValueError, IndexError):
        return template


def silver_frame(conn: Connection, batch_id: str) -> pd.DataFrame:
    rows = conn.execute(text("SELECT * FROM silver.order_lines WHERE batch_id = :id ORDER BY source_row"),
                        {"id": batch_id}).mappings().all()
    df = pd.DataFrame([dict(r) for r in rows])
    if df.empty:
        return df
    for c in DATE_COLUMNS:
        df[c] = pd.to_datetime(df[c])
    for c in NUMBER_COLUMNS:
        df[c] = pd.to_numeric(df[c]).astype("float64")
    df["line_no"] = pd.to_numeric(df["line_no"]).astype("Int64")
    return df


def validate_batch(conn: Connection, batch_id: str, masters: Masters) -> dict[str, int]:
    """Validate a mapped batch. Returns {rule_id: open or newly raised exception count} for this batch."""
    batch = conn.execute(text(
        "SELECT b.source, b.status, m.mapping FROM ops.batches b JOIN LATERAL ("
        "  SELECT mapping FROM ops.mapping_versions WHERE source = b.source AND header_hash = b.header_hash "
        "  AND status = 'confirmed') m ON true WHERE b.batch_id = :id FOR UPDATE OF b"),
        {"id": batch_id}).first()
    if batch is None or batch.status not in ("mapped", "validated", "published"):
        raise ValueError(f"batch {batch_id} is not mapped (status {batch.status if batch else 'missing'})")
    if batch.status == "published":  # gold must not keep rows a new exception picture might hold back
        from pipeline.publish import withdraw

        withdraw(conn, batch_id, SYSTEM, "re-validated; publish again to restore")
    df = silver_frame(conn, batch_id)
    mapped = frozenset(c["canonical_col"] for c in batch.mapping["columns"] if c["canonical_col"])
    ctx = RuleContext(source=batch.source, masters=masters, mapped=mapped)

    found: list[dict] = []
    counts: dict[str, int] = {}
    for rule in RULES:
        violations = rule.check(df, ctx) if not df.empty else None
        counts[rule.id] = len(violations.rows) if violations else 0
        if not violations:
            continue
        for i, details in zip(violations.rows, violations.details, strict=True):
            row = df.loc[i]
            order_no = row["order_no"] if pd.notna(row["order_no"]) else "?"
            line_no = int(row["line_no"]) if pd.notna(row["line_no"]) else "?"
            values = {**{k: v for k, v in row.items() if k != "parse_errors"}, **details}
            found.append({
                "b": batch_id, "source": batch.source, "rule": rule.id, "severity": rule.severity,
                "row": int(row["source_row"]), "key": f"{order_no}|{line_no}",
                "reason": _format(rule.message, values), "fix": _format(rule.suggested_fix, values),
                "owner": rule.owner(ctx), "details": json.dumps(details, default=str),
            })

    if found:
        conn.execute(text(
            "INSERT INTO ops.exceptions (batch_id, source, rule_id, severity, source_row, row_key, reason, "
            "suggested_fix, owner, details) VALUES (:b, :source, :rule, :severity, :row, :key, :reason, "
            ":fix, :owner, CAST(:details AS jsonb)) ON CONFLICT (batch_id, rule_id, source_row) DO NOTHING"),
            found)
    current = json.dumps([[f["rule"], f["row"]] for f in found])
    conn.execute(text(
        "WITH closed AS ("
        "  UPDATE ops.exceptions SET status = 'resolved', resolved_by = :who, resolved_at = now(), "
        "  resolution_kind = 'no_longer_violated', "
        "  resolution = 'no longer violated when the batch was re-validated' "
        "  WHERE batch_id = :b AND status <> 'resolved' AND (rule_id, source_row) NOT IN ("
        "    SELECT x->>0, (x->>1)::int FROM jsonb_array_elements(CAST(:current AS jsonb)) x) "
        "  RETURNING exception_id) "
        "INSERT INTO ops.exception_events (exception_id, action, actor) "
        "SELECT exception_id, 'auto_resolved', :who FROM closed"),
        {"who": SYSTEM, "b": batch_id, "current": current})
    summary = {"rows_validated": len(df), "rules_run": [r.id for r in RULES], "violations": counts}
    conn.execute(text("UPDATE ops.batches SET status = 'validated', finished_at = now(), "
                      "response = COALESCE(response, '{}'::jsonb) || jsonb_build_object('validation', "
                      "CAST(:s AS jsonb)) WHERE batch_id = :b"), {"s": json.dumps(summary), "b": batch_id})
    return counts
