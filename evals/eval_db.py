"""Evaluation databases built from data/ (shared by evals/run_evals.py and scripts/eval_agent.py).

`build_pipeline_db` recreates a database, migrates it, confirms the heuristic mapping for every header (as an
automated owner, recorded as `eval`), ingests every file, and optionally indexes the knowledge base. It times
each source from its first profile to its last validated batch: the onboarding time the plan asks to report,
excluding the human review a real confirmation takes. `build_generator_gold_db` loads the generator's clean
gold, the population the reconciliation answer key was computed on.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd
import psycopg
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.engine import make_url

from app.config import get_settings
from db.migrate import migrate
from llm.client import DbRecorder
from llm.embeddings import embedder_for
from pipeline.ingest import ingest_file
from pipeline.mapper.heuristic import propose
from pipeline.profile import discover_sources, load_masters, profile_source
from retrieval.store import sync


@dataclass
class BuildResult:
    url: str
    onboarding_seconds: dict[str, float] = field(default_factory=dict)
    batches: dict[str, dict[str, int]] = field(default_factory=dict)
    indexed_chunks: int | None = None


def recreate(server_url: str, name: str) -> str:
    admin = create_engine(make_url(server_url).set(database="postgres"), isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        conn.execute(text(f'CREATE DATABASE "{name}"'))
    admin.dispose()
    url = make_url(server_url).set(database=name).render_as_string(hide_password=False)
    migrate(url)
    return url


def confirm_heuristic(engine: Engine, source: str, header: list[str], profile) -> None:  # noqa: ANN001
    proposal = propose(profile, header)
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO ops.mapping_versions (source, version, mapping, proposed_by, status, "
                "confirmed_by, confirmed_at, header, header_hash) "
                "SELECT :s, COALESCE(MAX(version), 0) + 1, CAST(:m AS jsonb), "
                "'heuristic', 'confirmed', 'eval', now(), CAST(:hdr AS jsonb), :h FROM ops.mapping_versions "
                "WHERE source = :s"
            ),
            {
                "s": source,
                "m": json.dumps({"columns": [c.model_dump() for c in proposal.columns]}),
                "hdr": json.dumps(header),
                "h": proposal.header_hash,
            },
        )


def build_pipeline_db(
    server_url: str, name: str, data_dir: Path, index: bool = True, log: bool = True
) -> BuildResult:
    url = recreate(server_url, name)
    engine = create_engine(url)
    result = BuildResult(url)
    masters = load_masters(data_dir / "master")
    for source, files in discover_sources(data_dir / "sources").items():
        if not files:
            continue
        started = time.monotonic()
        profile = profile_source(source, files, masters)
        for variant in profile.header_variants:
            confirm_heuristic(engine, source, variant.columns, profile)
        statuses: dict[str, int] = {}
        for f in files:
            status = ingest_file(engine, source, f, data_dir / "master").status
            statuses[status] = statuses.get(status, 0) + 1
        result.onboarding_seconds[source] = round(time.monotonic() - started, 1)
        result.batches[source] = statuses
        if log:
            print(f"  {source}: {len(files)} files {statuses} in {result.onboarding_seconds[source]} s")
    if index:
        settings = get_settings()
        synced = sync(engine, data_dir / "kb", embedder_for(settings, DbRecorder(engine)))
        result.indexed_chunks = synced.chunks
        if log:
            print(f"  knowledge base: {synced.chunks} chunks, {synced.embedded} embedded")
    engine.dispose()
    return result


def build_generator_gold_db(server_url: str, name: str, data_dir: Path) -> str:
    url = recreate(server_url, name)
    fact = pd.read_parquet(data_dir / "gold" / "fact_delivery.parquet")
    dsn = make_url(url).set(drivername="postgresql").render_as_string(hide_password=False)
    with psycopg.connect(dsn) as pg:
        cols = [
            r[0]
            for r in pg.execute(
                "SELECT column_name FROM information_schema.columns WHERE table_schema = 'gold' "
                "AND table_name = 'fact_delivery' ORDER BY ordinal_position"
            ).fetchall()
        ]
        cols = [c for c in cols if c in fact.columns]
        rows = fact[cols].astype(object).where(fact[cols].notna(), None)
        with pg.cursor().copy(f"COPY gold.fact_delivery ({', '.join(cols)}) FROM STDIN") as copy:
            for row in rows.itertuples(index=False, name=None):
                copy.write_row([None if v == "" else v for v in row])
    return url
