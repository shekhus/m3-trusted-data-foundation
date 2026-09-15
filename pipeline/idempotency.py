"""Idempotency-Key bookkeeping for ingest requests (ops.ingest_requests).

claim() decides, under a row lock, whether this call should run: a new key runs; a completed key replays its
stored response; a key reused for a different request is refused; a key still in progress is refused unless
its lease expired (the worker died), in which case this call takes over; a failed key runs again.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Literal

from sqlalchemy import Engine, text

LEASE_MINUTES = 15
Decision = Literal["run", "replay", "mismatch", "in_progress"]


@dataclass(frozen=True)
class Claim:
    decision: Decision
    response: dict | None = None
    attempts: int = 1


def request_hash(payload: dict) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()


def claim(engine: Engine, key: str, source: str, hash_: str, requested_by: str) -> Claim:
    with engine.begin() as conn:
        inserted = conn.execute(text(
            "INSERT INTO ops.ingest_requests (idempotency_key, source, request_hash, requested_by, status) "
            "VALUES (:k, :s, :h, :by, 'in_progress') ON CONFLICT (idempotency_key) DO NOTHING "
            "RETURNING idempotency_key"), {"k": key, "s": source, "h": hash_, "by": requested_by}).first()
        if inserted is not None:
            return Claim("run")
        row = conn.execute(text(
            "SELECT request_hash, status, response, attempts, "
            f"updated_at < now() - interval '{LEASE_MINUTES} minutes' AS expired "
            "FROM ops.ingest_requests WHERE idempotency_key = :k FOR UPDATE"), {"k": key}).one()
        if row.request_hash.strip() != hash_:
            return Claim("mismatch")
        if row.status == "completed":
            return Claim("replay", row.response, row.attempts)
        if row.status == "in_progress" and not row.expired:
            return Claim("in_progress")
        conn.execute(text("UPDATE ops.ingest_requests SET status = 'in_progress', attempts = attempts + 1, "
                          "error = NULL, updated_at = now() WHERE idempotency_key = :k"), {"k": key})
        return Claim("run", attempts=row.attempts + 1)


def complete(engine: Engine, key: str, response: dict) -> None:
    with engine.begin() as conn:
        conn.execute(text("UPDATE ops.ingest_requests SET status = 'completed', "
                          "response = CAST(:r AS jsonb), updated_at = now() WHERE idempotency_key = :k"),
                     {"k": key, "r": json.dumps(response, default=str)})


def fail(engine: Engine, key: str, error: str) -> None:
    with engine.begin() as conn:
        conn.execute(text("UPDATE ops.ingest_requests SET status = 'failed', error = :e, updated_at = now() "
                          "WHERE idempotency_key = :k"), {"k": key, "e": error[:2000]})
