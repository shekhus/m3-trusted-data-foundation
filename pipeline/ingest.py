"""File → bronze → silver for order-line extracts. Plain Python, fixed sequence (CLAUDE.md principle 8).

One batch per file. The batch's idempotency key is the source, file name and sha256 of the bytes, so running
the same file again returns the original batch and writes nothing (the week-3 /ingest endpoint adds the
Idempotency-Key header on top). Bronze always receives every row as text. Silver is built from bronze only
when the file's exact header has a confirmed mapping; otherwise the batch is `blocked` and names the header,
and a later run after an owner confirms that mapping builds silver from the bronze rows already stored.
"""

from __future__ import annotations

import hashlib
import io
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd
from sqlalchemy import Connection, Engine, text

from pipeline.canonical import BY_NAME, ORDER_LINE
from pipeline.mapper.contract import MappingProposal, header_hash
from pipeline.transforms import apply
from pipeline.validate import load_masters, validate_batch

SILVER_COLUMNS = [f.name for f in ORDER_LINE]


@dataclass(frozen=True)
class BatchResult:
    batch_id: str
    source: str
    file_name: str
    status: str
    rows: int
    silver_rows: int
    parse_errors: int
    replayed: bool
    error: str | None
    blocking_exceptions: int = 0
    warning_exceptions: int = 0


def _driver_cursor(conn: Connection) -> Any:
    """psycopg cursor inside the transaction SQLAlchemy already began on this connection."""
    return conn.connection.driver_connection.cursor()  # type: ignore[union-attr]


def _confirmed_mapping(conn: Connection, source: str, hash_: str) -> tuple[int, MappingProposal] | None:
    row = conn.execute(text(
        "SELECT mapping_version_id, header, mapping, proposed_by FROM ops.mapping_versions "
        "WHERE source = :s AND header_hash = :h AND status = 'confirmed'"), {"s": source, "h": hash_}).first()
    if row is None:
        return None
    proposal = MappingProposal(source=source, header=row.header, proposed_by=row.proposed_by,
                               columns=row.mapping["columns"])
    return row.mapping_version_id, proposal


def _result(conn: Connection, batch_id: str, replayed: bool) -> BatchResult:
    b = conn.execute(text("SELECT batch_id, source, file_name, status, row_count, error FROM ops.batches "
                          "WHERE batch_id = :id"), {"id": batch_id}).one()
    counts = conn.execute(text(
        "SELECT count(*) AS n, count(*) FILTER (WHERE parse_errors <> '{}'::jsonb) AS bad "
        "FROM silver.order_lines WHERE batch_id = :id"), {"id": batch_id}).one()
    exceptions: dict[str, int] = dict(conn.execute(text(
        "SELECT severity, count(*) FROM ops.exceptions WHERE batch_id = :id AND status <> 'resolved' "
        "GROUP BY 1"), {"id": batch_id}).tuples().all())
    return BatchResult(str(b.batch_id), b.source, b.file_name, b.status, b.row_count or 0, counts.n,
                       counts.bad, replayed, b.error, int(exceptions.get("block", 0)),
                       int(exceptions.get("warn", 0)))


def build_silver(conn: Connection, batch_id: str) -> None:
    """Map a batch's bronze rows to silver with the header's confirmed mapping, or mark the batch blocked."""
    batch = conn.execute(text("SELECT source, header_hash FROM ops.batches WHERE batch_id = :id FOR UPDATE"),
                         {"id": batch_id}).one()
    found = _confirmed_mapping(conn, batch.source, batch.header_hash)
    if found is None:
        conn.execute(text("UPDATE ops.batches SET status = 'blocked', finished_at = now(), error = :e "
                          "WHERE batch_id = :id"),
                     {"id": batch_id, "e": f"no confirmed mapping for header {batch.header_hash.strip()}; "
                                           "propose and confirm one, then run again"})
        return
    mapping_version_id, mapping = found

    records = conn.execute(text("SELECT source_row, record FROM bronze.raw_order_lines WHERE batch_id = :id "
                                "ORDER BY source_row"), {"id": batch_id}).all()
    raw = pd.DataFrame([r.record for r in records], columns=mapping.header, dtype="string")
    silver = pd.DataFrame({"source_row": [r.source_row for r in records]})
    errors: list[dict[str, str]] = [{} for _ in records]
    by_target = {c.canonical_col: c for c in mapping.columns if c.canonical_col}
    for name in SILVER_COLUMNS:
        column = by_target.get(name)
        if column is None:
            silver[name] = None
            continue
        typed, bad = apply(raw[column.source_col], BY_NAME[name], column.transforms)
        silver[name] = typed.to_numpy()
        for i in bad[bad].index:
            errors[i][name] = str(raw.at[i, column.source_col])

    conn.execute(text("DELETE FROM silver.order_lines WHERE batch_id = :id"), {"id": batch_id})
    cols = ["batch_id", "source_row", "source", "mapping_version_id", *SILVER_COLUMNS, "parse_errors"]
    with _driver_cursor(conn).copy(f"COPY silver.order_lines ({', '.join(cols)}) FROM STDIN") as copy:
        for i, row in enumerate(silver.itertuples(index=False, name=None)):
            copy.write_row([batch_id, row[0], batch.source, mapping_version_id,
                            *[None if pd.isna(v) else v for v in row[1:]], json.dumps(errors[i])])
    bad_rows = sum(1 for e in errors if e)
    conn.execute(text("UPDATE ops.batches SET status = 'mapped', finished_at = now(), error = NULL, "
                      "response = CAST(:r AS jsonb) WHERE batch_id = :id"),
                 {"id": batch_id, "r": json.dumps({"silver_rows": len(silver),
                                                   "rows_with_parse_errors": bad_rows,
                                                   "mapping_version_id": mapping_version_id})})


def ingest_file(engine: Engine, source: str, path: Path, master_dir: Path) -> BatchResult:
    """File → bronze → silver → validated. Stops at `blocked` when the header has no confirmed mapping."""
    content = path.read_bytes()
    digest = hashlib.sha256(content).hexdigest()
    key = f"file:{source}:{path.name}:{digest}"
    with engine.begin() as conn:
        conn.execute(text("SELECT pg_advisory_xact_lock(hashtext(:k))"), {"k": key})
        existing = conn.execute(text("SELECT batch_id, status FROM ops.batches WHERE idempotency_key = :k"),
                                {"k": key}).first()
        if existing is not None:
            batch_id = str(existing.batch_id)
            if existing.status == "blocked":  # a mapping may have been confirmed since
                build_silver(conn, batch_id)
            _finish(conn, batch_id, master_dir)
            return _result(conn, batch_id, replayed=True)

        frame = pd.read_csv(io.BytesIO(content), dtype=str, keep_default_na=False, encoding="utf-8")
        header = list(frame.columns)
        batch_id = str(conn.execute(text(
            "INSERT INTO ops.batches (source, file_name, idempotency_key, request_hash, row_count, "
            "header_hash) VALUES (:s, :f, :k, :h, :n, :hh) RETURNING batch_id"),
            {"s": source, "f": path.name, "k": key, "h": digest, "n": len(frame),
             "hh": header_hash(header)}).scalar_one())
        copy_sql = "COPY bronze.raw_order_lines (batch_id, source_row, record) FROM STDIN"
        with _driver_cursor(conn).copy(copy_sql) as copy:
            for i, values in enumerate(frame.itertuples(index=False, name=None), start=1):
                copy.write_row([batch_id, i, json.dumps(dict(zip(header, values, strict=True)))])
        build_silver(conn, batch_id)
        _finish(conn, batch_id, master_dir)
        return _result(conn, batch_id, replayed=False)


def _finish(conn: Connection, batch_id: str, master_dir: Path) -> None:
    """Validate a batch that reached silver but has not been validated yet (new, resumed, or pre-rules)."""
    status = conn.execute(text("SELECT status FROM ops.batches WHERE batch_id = :id"),
                          {"id": batch_id}).scalar_one()
    if status == "mapped":
        validate_batch(conn, batch_id, load_masters(master_dir))
