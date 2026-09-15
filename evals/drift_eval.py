"""Drift detection scored against data/ground_truth/drift.json (plan §2.5: 2/2 detected, correct lineage).

detected — an alert exists for the event's source, raised by the first file of the effective month.
named    — the alert names the broken or new source column and every canonical column the event affects.
false alerts — alerts matching no event (month-to-month noise read as drift) are counted and listed.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy import Connection, text


@dataclass
class EventScore:
    drift_id: str
    source: str
    effective: str
    detected: bool
    named: bool
    alert_id: int | None
    file_name: str | None
    message: str | None
    loaded: bool = True  # False when the event's source has no batch on/after the effective month


@dataclass
class DriftScore:
    events: list[EventScore]
    false_alerts: list[dict] = field(default_factory=list)

    @property
    def scored(self) -> list[EventScore]:
        return [e for e in self.events if e.loaded]

    @property
    def detected(self) -> int:
        return sum(e.detected for e in self.events)

    @property
    def named(self) -> int:
        return sum(e.named for e in self.events)


def _plant(source: str) -> str:
    return f"{source[:3].upper()}-{source[3:]}"


def score(conn: Connection, data_dir: Path) -> DriftScore:
    truth = json.loads((data_dir / "ground_truth" / "drift.json").read_text(encoding="utf-8"))
    alerts = conn.execute(text(
        "SELECT a.alert_id, a.source, b.file_name, a.message, a.diff FROM ops.drift_alerts a "
        "JOIN ops.batches b USING (batch_id) ORDER BY a.alert_id")).all()
    months = {(r.source, r.month) for r in conn.execute(text(
        "SELECT DISTINCT source, substring(file_name from '(\\d{4}-\\d{2})\\.csv$') AS month "
        "FROM ops.batches "
        "WHERE file_name IS NOT NULL")).all()}
    used: set[int] = set()
    events: list[EventScore] = []
    for event in truth["events"]:
        month = event["effective"][:7]
        match = next((a for a in alerts if _plant(a.source) == event["source"] and a.file_name
                      and a.file_name.rsplit("_", 1)[-1].removesuffix(".csv") == month), None)
        if match is None:
            loaded = any(_plant(s) == event["source"] and m is not None and m >= month for s, m in months)
            events.append(EventScore(event["drift_id"], event["source"], event["effective"], False, False,
                                     None, None, None, loaded))
            continue
        used.add(match.alert_id)
        changed = (c["column"] for c in match.diff["changed"])
        columns = {*match.diff["removed"], *match.diff["added"], *changed}
        names_column = any(f"'{c}'" in match.message for c in columns) and bool(columns)
        names_canonical = set(event["affected_canonical"]) <= set(match.diff["affected_canonical"])
        events.append(EventScore(event["drift_id"], event["source"], event["effective"], True,
                                 names_column and names_canonical, match.alert_id, match.file_name,
                                 match.message))
    false_alerts = [{"alert_id": a.alert_id, "source": a.source, "file_name": a.file_name,
                     "message": a.message} for a in alerts if a.alert_id not in used]
    return DriftScore(events, false_alerts)
