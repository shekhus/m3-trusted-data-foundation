"""Silver → gold.fact_delivery (fixed sequence, CLAUDE.md principle 8).

A batch publishes only when it is `validated` (or already `published`), not `stale`, and its schema shape
has no open drift alert; otherwise it is refused with the reasons and nothing in gold changes. Within a
publishable batch a row is held back when it has an open or assigned blocking exception, or any exception
resolved as `exclude` or `fixed_at_source`; when a required canonical field is still blank (an `accept`
cannot make a blank customer publishable); or when it repeats an order line already taken from the batch.
Warnings never hold a row. Everything held is counted by reason.

Publishing replaces the batch's rows in gold, so it can be re-run. When two batches carry the same order line,
the later-received batch wins. Product group and the catch-weight flag come from the item master, production
and expiry dates from the lot master.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

import pandas as pd
from sqlalchemy import Connection, Engine, text

from pipeline.canonical import REQUIRED
from pipeline.validate import DATE_COLUMNS as SILVER_DATES
from pipeline.validate import silver_frame

GOLD_COLUMNS = ["order_no", "line_no", "customer_no", "item_no", "plant", "product_group",
                "catch_weight_flag", "order_date", "requested_date", "confirmed_delivery_date", "issue_date",
                "ordered_qty", "invoiced_qty", "ordered_weight_lb", "invoiced_weight_lb", "uom", "lot_no",
                "production_date", "expiry_date", "customer_po", "order_type", "batch_id", "source",
                "source_row", "published_at"]

DATE_COLUMNS = [*SILVER_DATES, "production_date", "expiry_date"]


@dataclass
class PublishResult:
    batch_id: str
    source: str
    file_name: str | None
    status: str  # published | refused
    rows_published: int = 0
    rows_held: dict[str, int] = field(default_factory=dict)
    reasons: list[str] = field(default_factory=list)


@lru_cache(maxsize=4)
def _enrichment(master_dir: str, stamp: tuple[int, int]) -> tuple[pd.DataFrame, pd.DataFrame]:
    root = Path(master_dir)
    items = pd.read_csv(root / "items.csv", dtype=str)[["item_no", "product_group", "catch_weight_flag"]]
    items["catch_weight_flag"] = items["catch_weight_flag"].str.lower().map({"true": True, "false": False})
    lots = pd.read_csv(root / "lot_master.csv", dtype=str)[["lot_no", "production_date", "expiry_date"]]
    return items.drop_duplicates("item_no"), lots.drop_duplicates("lot_no")


def _masters(master_dir: Path) -> tuple[pd.DataFrame, pd.DataFrame]:
    stamp = tuple((master_dir / n).stat().st_mtime_ns for n in ("items.csv", "lot_master.csv"))
    return _enrichment(str(master_dir), stamp)  # type: ignore[arg-type]


def refusal_reasons(conn: Connection, batch_id: str) -> tuple[list[str], Any]:
    batch = conn.execute(text(
        "SELECT b.source, b.file_name, b.status, b.stale, a.alert_id FROM ops.batches b "
        "LEFT JOIN ops.fingerprints f USING (batch_id) "
        "LEFT JOIN ops.drift_alerts a ON a.source = f.source AND a.to_fingerprint = f.fingerprint "
        "AND a.status = 'open' WHERE b.batch_id = :b FOR UPDATE OF b"), {"b": batch_id}).one()
    reasons = []
    if batch.status not in ("validated", "published"):
        reasons.append(f"batch is {batch.status}, not validated")
    if batch.stale:
        reasons.append("extract is stale (older than the source SLA)")
    if batch.alert_id is not None:
        reasons.append(f"open drift alert {batch.alert_id} on this schema shape")
    return reasons, batch


def publish_batch(conn: Connection, batch_id: str, actor: str, master_dir: Path) -> PublishResult:
    reasons, batch = refusal_reasons(conn, batch_id)
    result = PublishResult(batch_id, batch.source, batch.file_name, "refused", reasons=reasons)
    if reasons:
        _record(conn, result, actor)
        return result

    df = silver_frame(conn, batch_id)
    exceptions = pd.DataFrame(conn.execute(text(
        "SELECT source_row, severity, status, resolution_kind FROM ops.exceptions WHERE batch_id = :b"),
        {"b": batch_id}).all(), columns=["source_row", "severity", "status", "resolution_kind"])
    blocking = set(exceptions.loc[(exceptions["status"] != "resolved") & (exceptions["severity"] == "block"),
                                  "source_row"])
    kept_out = exceptions["resolution_kind"].isin(["exclude", "fixed_at_source"])
    excluded = set(exceptions.loc[kept_out, "source_row"])
    held: dict[str, int] = {}
    rows = df
    for reason, rule in (
            ("resolved as exclude or fixed_at_source", lambda d: d["source_row"].isin(excluded)),
            ("open blocking exception", lambda d: d["source_row"].isin(blocking)),
            ("required field blank", lambda d: d[sorted(REQUIRED)].isna().any(axis=1)),
            ("repeats an order line in the batch", lambda d: d.duplicated(subset=["order_no", "line_no"]))):
        mask = rule(rows)
        if mask.any():
            held[reason] = int(mask.sum())
            rows = rows[~mask]

    items, lots = _masters(master_dir)
    gold = rows.merge(items, on="item_no", how="left").merge(lots, on="lot_no", how="left")
    for column in DATE_COLUMNS:
        gold[column] = pd.to_datetime(gold[column]).dt.date
    gold["customer_po"], gold["order_type"] = None, None
    gold["batch_id"], gold["published_at"] = batch_id, pd.Timestamp.now(tz="UTC")

    conn.execute(text("DELETE FROM gold.fact_delivery WHERE batch_id = :b"), {"b": batch_id})
    if not gold.empty:
        conn.execute(text("CREATE TEMP TABLE publish_stage (LIKE gold.fact_delivery INCLUDING DEFAULTS) "
                          "ON COMMIT DROP"))
        cursor = conn.connection.driver_connection.cursor()  # type: ignore[union-attr]
        with cursor.copy(f"COPY publish_stage ({', '.join(GOLD_COLUMNS)}) FROM STDIN") as copy:
            for row in gold[GOLD_COLUMNS].itertuples(index=False, name=None):
                copy.write_row([_native(v) for v in row])
        updates = ", ".join(f"{c} = EXCLUDED.{c}" for c in GOLD_COLUMNS if c not in ("order_no", "line_no"))
        conn.execute(text(
            f"INSERT INTO gold.fact_delivery ({', '.join(GOLD_COLUMNS)}) SELECT {', '.join(GOLD_COLUMNS)} "
            f"FROM publish_stage ON CONFLICT (order_no, line_no) DO UPDATE SET {updates} "
            "WHERE (SELECT received_at FROM ops.batches WHERE batch_id = gold.fact_delivery.batch_id) "
            "     <= (SELECT received_at FROM ops.batches WHERE batch_id = EXCLUDED.batch_id)"))
        conn.execute(text("DROP TABLE publish_stage"))
    published = conn.execute(text("SELECT count(*) FROM gold.fact_delivery WHERE batch_id = :b"),
                             {"b": batch_id}).scalar_one()
    newer = len(gold) - published
    if newer:
        held["a later batch already published this order line"] = newer
    result.status, result.rows_published, result.rows_held = "published", published, held
    conn.execute(text("UPDATE ops.batches SET status = 'published' WHERE batch_id = :b"), {"b": batch_id})
    _record(conn, result, actor)
    return result


def _native(value: Any) -> Any:  # noqa: ANN401 - any cell value
    """pandas/NumPy scalars → values psycopg can COPY; NaN/NaT/NA → NULL."""
    if value is None or (not isinstance(value, (str, bool)) and pd.isna(value)):
        return None
    if isinstance(value, pd.Timestamp):
        return value.to_pydatetime()
    return value.item() if hasattr(value, "item") else value


def withdraw(conn: Connection, batch_id: str, actor: str, reason: str) -> int:
    """Remove a batch's rows from gold (e.g. it is being re-validated). Returns rows removed."""
    key = {"b": batch_id}
    removed = conn.execute(text("DELETE FROM gold.fact_delivery WHERE batch_id = :b"), key).rowcount
    source = conn.execute(text("SELECT source FROM ops.batches WHERE batch_id = :b"), key).scalar_one()
    conn.execute(text("INSERT INTO ops.publishes (batch_id, source, status, reasons, actor) "
                      "VALUES (:b, :s, 'withdrawn', CAST(:r AS jsonb), :a)"),
                 {"b": batch_id, "s": source, "r": json.dumps([reason]), "a": actor})
    return removed


def _record(conn: Connection, result: PublishResult, actor: str) -> None:
    conn.execute(text("INSERT INTO ops.publishes (batch_id, source, status, rows_published, rows_held, "
                      "reasons, actor) VALUES (:b, :s, :st, :n, CAST(:held AS jsonb), "
                      "CAST(:reasons AS jsonb), :a)"),
                 {"b": result.batch_id, "s": result.source, "st": result.status, "n": result.rows_published,
                  "held": json.dumps(result.rows_held), "reasons": json.dumps(result.reasons), "a": actor})


def publish_source(engine: Engine, source: str, actor: str, master_dir: Path,
                   batch_ids: list[str] | None = None) -> list[PublishResult]:
    """Publish every batch of a source that holds data (or only the given ones), oldest first, one
    transaction per batch so one refusal does not undo the others. Blocked batches come back refused."""
    with engine.connect() as conn:
        rows = conn.execute(text(
            "SELECT batch_id::text FROM ops.batches WHERE source = :s AND status <> 'failed' "
            "ORDER BY received_at, batch_id"), {"s": source}).scalars().all()
    wanted = [b for b in rows if batch_ids is None or b in set(batch_ids)]
    results = []
    for batch_id in wanted:
        with engine.begin() as conn:
            results.append(publish_batch(conn, batch_id, actor, master_dir))
    return results
