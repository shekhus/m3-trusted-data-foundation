"""Column lineage (docs/plan.md A8): source column → silver column → gold column → compiled metric views.

`record_lineage` writes one ops.lineage row per mapped column of a confirmed mapping version (idempotent; kept
for superseded versions so history stays traceable). `impact` answers the drift question — if this source
column breaks, which gold columns and which metric and consumer views are affected — and `upstream` the
reverse. Views are derived from metrics/*.yaml and metrics/consumers/consumers.yaml via the compiler, never
listed by hand, so a new metric or consumer is covered.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from functools import cache

from sqlalchemy import Connection, text

from metrics.compiler import compile_all, compile_consumers
from pipeline.canonical import ORDER_LINE

SILVER_TABLE = "silver.order_lines"
# silver → gold is one-to-one by name today; publish (week 4) is the only place allowed to change this map
GOLD_COLUMNS: dict[str, tuple[str, str]] = {f.name: ("gold.fact_delivery", f.name) for f in ORDER_LINE}


@dataclass(frozen=True)
class LineageRow:
    mapping_version_id: int
    mapping_version: int
    mapping_status: str
    source: str
    source_col: str
    silver_col: str
    gold_table: str
    gold_col: str
    transforms: list[str]


@dataclass
class Impact:
    source: str
    source_col: str
    lineage: list[LineageRow]
    gold_columns: list[str] = field(default_factory=list)  # "gold.fact_delivery.customer_no"
    views: list[str] = field(default_factory=list)  # "gold.otif_v3_lines"

    @property
    def summary(self) -> str:
        if not self.lineage:
            return f"{self.source} column '{self.source_col}' feeds no confirmed mapping"
        views = ", ".join(self.views) if self.views else "no compiled metric view"
        return (f"{self.source} column '{self.source_col}' feeds {', '.join(self.gold_columns)}; "
                f"affected views: {views}")


def record_lineage(conn: Connection, mapping_version_id: int) -> int:
    """Insert lineage for a mapping version's mapped columns. Returns rows inserted (0 if already there)."""
    row = conn.execute(text("SELECT source, mapping FROM ops.mapping_versions "
                            "WHERE mapping_version_id = :id"), {"id": mapping_version_id}).one()
    params = []
    for column in row.mapping["columns"]:
        canonical = column["canonical_col"]
        if canonical is None or canonical not in GOLD_COLUMNS:
            continue
        gold_table, gold_col = GOLD_COLUMNS[canonical]
        params.append({"id": mapping_version_id, "source": row.source, "source_col": column["source_col"],
                       "silver_table": SILVER_TABLE, "silver_col": canonical, "gold_table": gold_table,
                       "gold_col": gold_col, "transforms": json.dumps(list(column.get("transforms", [])))})
    inserted = 0
    for p in params:
        inserted += conn.execute(text(
            "INSERT INTO ops.lineage (mapping_version_id, source, source_col, silver_table, silver_col, "
            "gold_table, gold_col, transforms) VALUES (:id, :source, :source_col, :silver_table, "
            ":silver_col, :gold_table, :gold_col, CAST(:transforms AS jsonb)) "
            "ON CONFLICT (mapping_version_id, gold_table, gold_col, source_col) DO NOTHING"), p).rowcount
    return inserted


def backfill(conn: Connection) -> int:
    """Record lineage for every confirmed or superseded mapping lacking it. Idempotent; runs after migrate."""
    ids = conn.execute(text("SELECT mapping_version_id FROM ops.mapping_versions "
                            "WHERE status IN ('confirmed', 'superseded') ORDER BY 1")).scalars().all()
    return sum(record_lineage(conn, int(i)) for i in ids)


def _rows(conn: Connection, where: str, params: dict, include_superseded: bool) -> list[LineageRow]:
    statuses = "('confirmed', 'superseded')" if include_superseded else "('confirmed')"
    result = conn.execute(text(
        "SELECT l.mapping_version_id, m.version, m.status, l.source, l.source_col, l.silver_col, "
        "l.gold_table, l.gold_col, l.transforms "
        "FROM ops.lineage l JOIN ops.mapping_versions m USING (mapping_version_id) "
        f"WHERE {where} AND m.status IN {statuses} ORDER BY l.source, m.version, l.source_col"), params)
    return [LineageRow(r.mapping_version_id, r.version, r.status, r.source, r.source_col, r.silver_col,
                       r.gold_table, r.gold_col, list(r.transforms)) for r in result]


@cache
def _view_columns() -> tuple[tuple[str, str, frozenset[str]], ...]:
    """(view, source table, columns it reads) for every compiled metric version."""
    return tuple((c.definition.view, c.definition.source, frozenset(c.source_columns)) for c in compile_all())


@cache
def _consumers_of() -> dict[str, tuple[str, ...]]:
    """Metric view → the consumer views compiled on top of it (metrics/consumers/consumers.yaml)."""
    metrics = compile_all()
    views = {(m.definition.metric, m.definition.version): m.definition.view for m in metrics}
    out: dict[str, list[str]] = {}
    for consumer in compile_consumers(metrics).catalog.consumers:
        out.setdefault(views[(consumer.metric, consumer.version)], []).append(consumer.view)
    return {view: tuple(consumers) for view, consumers in out.items()}


def views_using(gold_table: str, gold_col: str) -> list[str]:
    """Metric views reading the column, and the consumer views built on those metric views."""
    metric_views = {view for view, table, columns in _view_columns()
                    if table == gold_table and gold_col in columns}
    return sorted(metric_views | {c for v in metric_views for c in _consumers_of().get(v, ())})


def impact(conn: Connection, source: str, source_col: str, include_superseded: bool = False) -> Impact:
    rows = _rows(conn, "l.source = :s AND l.source_col = :c", {"s": source, "c": source_col},
                 include_superseded)
    gold = sorted({(r.gold_table, r.gold_col) for r in rows})
    views = sorted({v for table, col in gold for v in views_using(table, col)})
    return Impact(source, source_col, rows, [f"{t}.{c}" for t, c in gold], views)


def upstream(conn: Connection, gold_table: str, gold_col: str,
             include_superseded: bool = False) -> list[LineageRow]:
    return _rows(conn, "l.gold_table = :t AND l.gold_col = :c", {"t": gold_table, "c": gold_col},
                 include_superseded)
