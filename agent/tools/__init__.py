"""The agent's five tools (docs/addendum1.md §2.3). Deterministic code; each returns a Pydantic model.

Every call is logged to ops.tool_calls with its arguments, what it returned, latency and outcome, so each
evidence_ref in a classification can be traced to the call that produced it. Knowledge-base search is
permission-filtered before ranking (retrieval/access.py); the agent searches as an analyst by default, so
restricted commercial terms never reach it. Tools read; none of them writes to silver, gold or any master.
"""

from __future__ import annotations

import difflib
import json
import re
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Literal, TypeVar

import pandas as pd
from pydantic import BaseModel, ConfigDict
from sqlalchemy import Engine, text

from agent.policy_gate import ExceptionFacts
from llm.embeddings import Embedder
from retrieval.access import AccessContext
from retrieval.hybrid import Mode, rank
from retrieval.precedence import retrieve

T = TypeVar("T", bound=BaseModel)
Master = Literal["customers", "items", "lots"]
MASTER_FILES: dict[str, tuple[str, str]] = {
    "customers": ("customers.csv", "customer_no"),
    "items": ("items.csv", "item_no"),
    "lots": ("lot_master.csv", "lot_no"),
}
KEY_FIELDS = ("customer_no", "item_no", "lot_no", "order_no")


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PriorResolution(Model):
    resolution_id: str
    rule_id: str
    plant: str
    pattern: str
    classification: str
    resolution: str
    resolved_by: str
    resolved_on: str


class PriorResolutions(Model):
    results: list[PriorResolution]


class KbChunk(Model):
    chunk_id: str
    doc_id: str
    version: str
    status: str
    notes: list[str]
    text: str


class KbResults(Model):
    results: list[KbChunk]


class MasterMatch(Model):
    candidate: str
    method: str  # exact | normalised:<how> | fuzzy
    score: float


class MasterLookup(Model):
    value: str
    master: Master
    matches: list[MasterMatch]


class RawRow(Model):
    source_row: int
    is_target: bool
    record: dict[str, str | None]


class SourceRows(Model):
    source: str
    row_key: str
    batch_id: str | None
    file_name: str | None
    rows: list[RawRow]


class RowSummary(Model):
    batch_id: str
    source_row: int
    order_no: str | None
    line_no: int | None
    customer_no: str | None
    item_no: str | None
    issue_date: str | None


class OtherSources(Model):
    key: str
    field: str
    by_source: dict[str, list[RowSummary]]


@lru_cache(maxsize=8)
def _master(master_dir: str, master: str) -> tuple[str, ...]:
    file, column = MASTER_FILES[master]
    return tuple(pd.read_csv(Path(master_dir) / file, dtype=str)[column].dropna().unique())


def normalisations(value: str, master: str) -> list[tuple[str, str]]:
    """(how, candidate) for the known formatting differences, most specific first."""
    out = []
    stripped = value.strip()
    if stripped != value:
        out.append(("trim", stripped))
    upper = stripped.upper()
    if upper != stripped:
        out.append(("upper", upper))
    if master == "customers":
        m = re.fullmatch(r"(?:CUST-?|C)?0*(\d{1,6})", upper)
        if m:
            out.append(("customer_prefix_and_padding", f"C{int(m.group(1)):06d}"))
    return out


@dataclass
class AgentTools:
    engine: Engine
    master_dir: Path
    embedder: Embedder | None
    access: AccessContext = AccessContext("analyst")
    run_id: str | None = None
    exception_id: int | None = None

    def _call(self, tool: str, arguments: dict, body: Callable[[], T], count: Callable[[T], int]) -> T:
        started = time.monotonic()
        try:
            result = body()
        except Exception as exc:
            self._log(tool, arguments, "error", None, None, started, f"{type(exc).__name__}: {exc}"[:2000])
            raise
        n = count(result)
        self._log(tool, arguments, "ok" if n else "empty", n, result.model_dump(mode="json"), started, None)
        return result

    def _log(
        self,
        tool: str,
        arguments: dict,
        outcome: str,
        n: int | None,
        result: dict | None,
        started: float,
        error: str | None,
    ) -> None:
        with self.engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO ops.tool_calls (run_id, exception_id, tool, arguments, outcome, "
                    "result_count, result, latency_ms, error) "
                    "VALUES (:run, :exc, :tool, CAST(:args AS jsonb), :outcome, :n, "
                    "CAST(:result AS jsonb), :ms, :error)"
                ),
                {
                    "run": self.run_id,
                    "exc": self.exception_id,
                    "tool": tool,
                    "args": json.dumps(arguments),
                    "outcome": outcome,
                    "n": n,
                    "result": None if result is None else json.dumps(result),
                    "ms": int((time.monotonic() - started) * 1000),
                    "error": error,
                },
            )

    # --- tools -------------------------------------------------------------------------------

    def search_prior_resolutions(
        self, symptom: str, rule_id: str | None = None, k: int = 5
    ) -> PriorResolutions:
        def body() -> PriorResolutions:
            mode: Mode = "hybrid" if self.embedder is not None else "bm25"
            hits = rank(self.engine, symptom, self.access, mode, self.embedder).hits
            wanted = rule_id.split("_")[0] if rule_id else None
            ids = [h.chunk.doc_id for h in hits if h.chunk.doc_type == "prior_resolution"]
            records = {r["resolution_id"]: r for r in _resolutions(str(self.master_dir.parent / "kb"))}
            chosen = [
                records[i]
                for i in ids
                if i in records and (wanted is None or records[i]["rule_id"].split("_")[0] == wanted)
            ][:k]
            return PriorResolutions(results=[PriorResolution(**r) for r in chosen])

        return self._call(
            "search_prior_resolutions",
            {"symptom": symptom, "rule_id": rule_id, "k": k},
            body,
            lambda r: len(r.results),
        )

    def search_knowledge_base(self, query: str, k: int = 5) -> KbResults:
        def body() -> KbResults:
            mode: Mode = "hybrid" if self.embedder is not None else "bm25"
            docs = retrieve(
                self.engine,
                query,
                self.access,
                self.embedder,
                k=k,
                mode=mode,
                keep=lambda c: c.doc_type != "prior_resolution",
            ).hits
            return KbResults(
                results=[
                    KbChunk(
                        chunk_id=h.chunk.chunk_id,
                        doc_id=h.chunk.doc_id,
                        version=h.chunk.version,
                        status=h.chunk.status,
                        notes=list(h.notes),
                        text=h.chunk.text,
                    )
                    for h in docs
                ]
            )

        return self._call(
            "search_knowledge_base",
            {"query": query, "k": k, "role": self.access.role, "plant": self.access.plant},
            body,
            lambda r: len(r.results),
        )

    def lookup_master(self, value: str, master: Master, fuzzy: bool = True) -> MasterLookup:
        def body() -> MasterLookup:
            if master not in MASTER_FILES:
                raise ValueError(f"unknown master {master!r}; expected one of {', '.join(MASTER_FILES)}")
            keys = _master(str(self.master_dir), master)
            known = set(keys)
            matches: list[MasterMatch] = []
            if value in known:
                matches.append(MasterMatch(candidate=value, method="exact", score=1.0))
            for how, candidate in normalisations(value, master):
                if candidate in known and all(m.candidate != candidate for m in matches):
                    matches.append(MasterMatch(candidate=candidate, method=f"normalised:{how}", score=1.0))
            if fuzzy and not matches:
                for candidate in difflib.get_close_matches(value.strip().upper(), keys, n=3, cutoff=0.85):
                    ratio = difflib.SequenceMatcher(None, value.strip().upper(), candidate).ratio()
                    matches.append(MasterMatch(candidate=candidate, method="fuzzy", score=round(ratio, 3)))
            return MasterLookup(value=value, master=master, matches=matches)

        return self._call(
            "lookup_master",
            {"value": value, "master": master, "fuzzy": fuzzy},
            body,
            lambda r: len(r.matches),
        )

    def inspect_source_rows(
        self, source: str, row_key: str, window: int = 5, batch_id: str | None = None
    ) -> SourceRows:
        def body() -> SourceRows:
            order_no, _, line = row_key.partition("|")
            with self.engine.connect() as conn:
                target = conn.execute(
                    text(
                        "SELECT s.batch_id::text AS batch_id, s.source_row, b.file_name "
                        "FROM silver.order_lines s "
                        "JOIN ops.batches b USING (batch_id) WHERE s.source = :src AND s.order_no = :o "
                        "AND s.line_no::text = :l AND (CAST(:b AS text) IS NULL OR s.batch_id::text = :b) "
                        "ORDER BY b.received_at DESC, s.source_row LIMIT 1"
                    ),
                    {"src": source, "o": order_no, "l": line, "b": batch_id},
                ).first()
                if target is None:
                    return SourceRows(source=source, row_key=row_key, batch_id=None, file_name=None, rows=[])
                rows = conn.execute(
                    text(
                        "SELECT source_row, record FROM bronze.raw_order_lines "
                        "WHERE batch_id = CAST(:b AS uuid) "
                        "AND source_row BETWEEN :lo AND :hi ORDER BY source_row"
                    ),
                    {
                        "b": target.batch_id,
                        "lo": target.source_row - window,
                        "hi": target.source_row + window,
                    },
                ).all()
            return SourceRows(
                source=source,
                row_key=row_key,
                batch_id=target.batch_id,
                file_name=target.file_name,
                rows=[
                    RawRow(
                        source_row=r.source_row, is_target=r.source_row == target.source_row, record=r.record
                    )
                    for r in rows
                ],
            )

        return self._call(
            "inspect_source_rows",
            {"source": source, "row_key": row_key, "window": window, "batch_id": batch_id},
            body,
            lambda r: len(r.rows),
        )

    def check_other_sources(
        self, key: str, field: str, exclude_source: str | None = None, limit: int = 5
    ) -> OtherSources:
        def body() -> OtherSources:
            if field not in KEY_FIELDS:
                raise ValueError(f"field must be one of {', '.join(KEY_FIELDS)}")
            with self.engine.connect() as conn:
                rows = (
                    conn.execute(
                        text(
                            "SELECT source, batch_id::text AS batch_id, source_row, order_no, line_no, "
                            "customer_no, "
                            "item_no, issue_date::text AS issue_date FROM ("
                            "  SELECT *, row_number() OVER "
                            "  (PARTITION BY source ORDER BY batch_id, source_row) AS n "
                            f"  FROM silver.order_lines WHERE {field} = :key "
                            "   AND (CAST(:ex AS text) IS NULL OR source <> :ex)) s WHERE n <= :limit "
                            "ORDER BY source, batch_id, source_row"
                        ),
                        {"key": key, "ex": exclude_source, "limit": limit},
                    )
                    .mappings()
                    .all()
                )
            by_source: dict[str, list[RowSummary]] = {}
            for r in rows:
                by_source.setdefault(r["source"], []).append(
                    RowSummary(**{k: v for k, v in r.items() if k != "source"})
                )
            return OtherSources(key=key, field=field, by_source=by_source)

        return self._call(
            "check_other_sources",
            {"key": key, "field": field, "exclude_source": exclude_source, "limit": limit},
            body,
            lambda r: sum(len(v) for v in r.by_source.values()),
        )


@lru_cache(maxsize=2)
def _resolutions(kb_dir: str) -> tuple[dict, ...]:
    return tuple(
        json.loads((Path(kb_dir) / "resolutions" / "prior_resolutions.json").read_text(encoding="utf-8"))
    )


def exception_facts(engine: Engine, exception_id: int, master_dir: Path) -> ExceptionFacts:
    """The row as it is in the database, and each canonical field's raw source text, for the policy gate."""
    with engine.connect() as conn:
        e = conn.execute(
            text(
                "SELECT e.exception_id, e.rule_id, e.batch_id, e.source_row, s.mapping_version_id, r.record, "
                "to_jsonb(s) AS row FROM ops.exceptions e "
                "JOIN silver.order_lines s ON s.batch_id = e.batch_id AND s.source_row = e.source_row "
                "JOIN bronze.raw_order_lines r ON r.batch_id = e.batch_id AND r.source_row = e.source_row "
                "WHERE e.exception_id = :id"
            ),
            {"id": exception_id},
        ).one()
        mapping = conn.execute(
            text("SELECT mapping FROM ops.mapping_versions WHERE mapping_version_id = :m"),
            {"m": e.mapping_version_id},
        ).scalar_one()
    raw = {
        c["canonical_col"]: e.record.get(c["source_col"]) for c in mapping["columns"] if c["canonical_col"]
    }
    row = {k: v for k, v in e.row.items() if k not in ("parse_errors",)}
    items = pd.read_csv(master_dir / "items.csv", dtype=str).set_index("item_no")["uom"]
    item = row.get("item_no")
    master_uom = str(items[item]).upper() if isinstance(item, str) and item in items.index else None
    return ExceptionFacts(
        exception_id=e.exception_id, rule_id=e.rule_id, row=row, raw=raw, master_uom=master_uom
    )


def new_run_id() -> str:
    return str(uuid.uuid4())
