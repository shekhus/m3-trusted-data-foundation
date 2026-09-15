"""Schema drift detection (docs/plan.md A9).

Fingerprint = sha256 of the sorted (column, dtype, null_bucket, pattern_class) tuples of one extract. The null
bucket is deliberately coarse (sparse ≤ 5%, partial ≤ 50%, mostly) and the pattern is the detected date format
or a dominant (≥ 90%) value shape, so month-to-month noise — a few blank customers, a missing weight — never
reads as drift, while a renamed, added, removed or retyped column always does.

On a change from the source's previous fingerprint an alert is raised once per new shape. It names removed and
added columns, likely renames (a removed and an added column with the same dtype/bucket/pattern), and, through
lineage, the canonical and gold columns and compiled views a removed or retyped column feeds; an added column
is named with the canonical column the heuristic mapper would propose for it. Publish refuses batches whose
fingerprint has an open alert.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field

import pandas as pd
from sqlalchemy import Connection, text

from pipeline import lineage
from pipeline.mapper.heuristic import propose
from pipeline.profile import ColumnProfile, Masters, profile_frames

DOMINANT_SHAPE_PCT = 90.0


def null_bucket(null_pct: float) -> str:
    return "sparse" if null_pct <= 5 else "partial" if null_pct <= 50 else "mostly"


def pattern_class(col: ColumnProfile) -> str:
    if col.date:
        return f"date:{col.date.format}"
    if col.dtype == "string" and col.shapes and col.shapes[0].share_pct >= DOMINANT_SHAPE_PCT:
        return f"shape:{col.shapes[0].shape.rstrip('·')}"
    return col.dtype


@dataclass(frozen=True)
class Fingerprint:
    columns: list[list[str]]  # sorted [column, dtype, null_bucket, pattern_class]

    @property
    def digest(self) -> str:
        return hashlib.sha256(json.dumps(self.columns).encode("utf-8")).hexdigest()


def fingerprint(source: str, name: str, frame: pd.DataFrame,
                masters: Masters) -> tuple[Fingerprint, list[str]]:
    profile = profile_frames(source, [(name, frame)], masters)
    cols = sorted([c.name, c.dtype, null_bucket(c.null_pct), pattern_class(c)] for c in profile.columns)
    return Fingerprint(cols), list(frame.columns)


@dataclass
class Diff:
    removed: list[str] = field(default_factory=list)
    added: list[str] = field(default_factory=list)
    changed: list[dict] = field(default_factory=list)  # {"column", "from": [...], "to": [...]}
    renames: list[dict] = field(default_factory=list)  # {"from", "to"}

    @property
    def empty(self) -> bool:
        return not (self.removed or self.added or self.changed)


def diff(before: list[list[str]], after: list[list[str]]) -> Diff:
    old = {c[0]: c[1:] for c in before}
    new = {c[0]: c[1:] for c in after}
    d = Diff(removed=sorted(set(old) - set(new)), added=sorted(set(new) - set(old)),
             changed=[{"column": c, "from": old[c], "to": new[c]} for c in sorted(set(old) & set(new))
                      if old[c] != new[c]])
    unmatched = list(d.added)
    for gone in d.removed:
        twin = next((a for a in unmatched if new[a] == old[gone]), None)
        if twin is not None:
            d.renames.append({"from": gone, "to": twin})
            unmatched.remove(twin)
    return d


def _message(conn: Connection, source: str, d: Diff, header: list[str], frame: pd.DataFrame,
             masters: Masters) -> tuple[str, dict]:
    impacts = {c: lineage.impact(conn, source, c) for c in [*d.removed, *(x["column"] for x in d.changed)]}
    proposal = propose(profile_frames(source, [("new", frame)], masters), header) if d.added else None
    proposed: dict[str, str] = ({c.source_col: c.canonical_col for c in proposal.columns if c.canonical_col}
                                if proposal else {})
    parts: list[str] = []
    for r in d.renames:
        parts.append(f"'{r['from']}' appears renamed to '{r['to']}'")
    for col in d.removed:
        imp = impacts[col]
        if imp.lineage:
            views = ", ".join(imp.views) or "no compiled view"
            gold = ", ".join(imp.gold_columns)
            parts.append(f"Mapping for {source} source column '{col}' is broken; {gold} "
                         f"and views {views} are affected")
        else:
            parts.append(f"Source column '{col}' disappeared (it fed no confirmed mapping)")
    for col in d.added:
        if any(r["to"] == col for r in d.renames):
            continue
        guess = proposed.get(col)
        parts.append(f"New source column '{col}' detected on {source} with no mapping"
                     + (f" (likely canonical column {guess})" if guess else ""))
    for change in d.changed:
        imp = impacts[change["column"]]
        parts.append(f"Column '{change['column']}' changed {change['from']} → {change['to']}"
                     + (f"; feeds {', '.join(imp.gold_columns)}" if imp.gold_columns else ""))
    canonical = sorted({r.silver_col for i in impacts.values() for r in i.lineage}
                       | {proposed[c] for c in d.added if c in proposed})
    evidence = {"removed": d.removed, "added": d.added, "changed": d.changed, "renames": d.renames,
                "affected_canonical": canonical,
                "affected_gold": sorted({g for i in impacts.values() for g in i.gold_columns}),
                "affected_views": sorted({v for i in impacts.values() for v in i.views})}
    return ". ".join(parts) + ". Publish is blocked until an owner resolves this alert.", evidence


def check(conn: Connection, batch_id: str, source: str, name: str, frame: pd.DataFrame,
          masters: Masters) -> int | None:
    """Record the batch's fingerprint; raise an alert if it differs from the source's previous one.

    Returns the alert id when this batch's shape is under an open alert (new or existing), else None.
    """
    fp, header = fingerprint(source, name, frame, masters)
    previous = conn.execute(text(
        "SELECT fingerprint_id, fingerprint, columns FROM ops.fingerprints WHERE source = :s "
        "ORDER BY fingerprint_id DESC LIMIT 1"), {"s": source}).first()
    conn.execute(text("INSERT INTO ops.fingerprints (batch_id, source, fingerprint, columns) "
                      "VALUES (:b, :s, :f, CAST(:c AS jsonb)) ON CONFLICT (batch_id) DO NOTHING"),
                 {"b": batch_id, "s": source, "f": fp.digest, "c": json.dumps(fp.columns)})
    if previous is not None and previous.fingerprint.strip() != fp.digest:
        d = diff(previous.columns, fp.columns)
        message, evidence = _message(conn, source, d, header, frame, masters)
        conn.execute(text(
            "INSERT INTO ops.drift_alerts (source, batch_id, from_fingerprint_id, to_fingerprint, diff, "
            "message) VALUES (:s, :b, :prev, :to, CAST(:diff AS jsonb), :m) "
            "ON CONFLICT (source, to_fingerprint) DO NOTHING"),
            {"s": source, "b": batch_id, "prev": previous.fingerprint_id, "to": fp.digest,
             "diff": json.dumps(evidence), "m": message})
    alert = conn.execute(text("SELECT alert_id FROM ops.drift_alerts WHERE source = :s "
                              "AND to_fingerprint = :f AND status = 'open'"),
                         {"s": source, "f": fp.digest}).scalar()
    return int(alert) if alert is not None else None


def backfill(conn: Connection, masters: Masters) -> int:
    """Fingerprint batches ingested before drift detection existed, in arrival order, from their bronze rows.

    Bronze keeps every value as received; the header order comes from the batch's mapping when one exists
    (the fingerprint itself is order-independent). Returns the number of batches fingerprinted.
    """
    batches = conn.execute(text(
        "SELECT b.batch_id, b.source, b.file_name, m.header FROM ops.batches b "
        "LEFT JOIN ops.mapping_versions m ON m.source = b.source AND m.header_hash = b.header_hash "
        "AND m.status IN ('confirmed', 'superseded') "
        "WHERE b.header_hash IS NOT NULL AND NOT EXISTS "
        "(SELECT 1 FROM ops.fingerprints f WHERE f.batch_id = b.batch_id) "
        "ORDER BY b.received_at, b.batch_id")).all()
    seen: set[str] = set()
    done = 0
    for b in batches:
        batch_id = str(b.batch_id)
        if batch_id in seen:  # a batch can join more than one mapping version of its header
            continue
        seen.add(batch_id)
        records = conn.execute(text("SELECT record FROM bronze.raw_order_lines WHERE batch_id = :b "
                                    "ORDER BY source_row"), {"b": batch_id}).scalars().all()
        if not records:
            continue
        header = list(b.header) if b.header else list(records[0])
        frame = pd.DataFrame(list(records), columns=header, dtype="string").fillna("").astype(str)
        check(conn, batch_id, b.source, b.file_name or batch_id, frame, masters)
        done += 1
    return done
