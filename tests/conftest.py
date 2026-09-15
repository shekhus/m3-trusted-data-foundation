from __future__ import annotations

import json
import os
import uuid
from collections.abc import Iterator
from dataclasses import dataclass

import pytest
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.engine import make_url

from app.config import REPO_ROOT
from db.migrate import migrate
from pipeline.ingest import BatchResult, ingest_file
from pipeline.mapper.heuristic import propose
from pipeline.profile import SourceProfile, discover_sources, profile_all

# Compose defaults (docker-compose.yml). Override with TEST_DATABASE_URL.
DEFAULT_PG_URL = "postgresql+psycopg://m3:m3@localhost:5432/m3tdf"


def _server_url() -> str:
    url = os.environ.get("TEST_DATABASE_URL") or os.environ.get("DATABASE_URL") or DEFAULT_PG_URL
    return url if make_url(url).get_backend_name() == "postgresql" else DEFAULT_PG_URL


@pytest.fixture(scope="session")
def pg_admin() -> Iterator[Engine]:
    """AUTOCOMMIT engine on the Postgres server, probed once per session (one timeout when absent).

    Skips when Postgres is unreachable (docs/decisions.md D-009). Set REQUIRE_POSTGRES=1 (CI) to fail instead.
    """
    server = make_url(_server_url())
    where = server.render_as_string(hide_password=True)
    admin = create_engine(server, isolation_level="AUTOCOMMIT", connect_args={"connect_timeout": 3})
    try:
        with admin.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception as exc:
        admin.dispose()
        if os.environ.get("REQUIRE_POSTGRES") == "1":
            pytest.fail(f"Postgres unreachable at {where}: {exc}")
        pytest.skip(f"Postgres unreachable at {where} (start: make up)")
    yield admin
    admin.dispose()


@pytest.fixture
def pg_url(pg_admin: Engine) -> Iterator[str]:
    """A fresh, empty Postgres database per test, dropped afterwards."""
    admin = pg_admin
    server = admin.url
    name = f"m3tdf_test_{uuid.uuid4().hex[:12]}"
    with admin.connect() as conn:
        conn.execute(text(f'CREATE DATABASE "{name}"'))
    try:
        yield server.set(database=name).render_as_string(hide_password=False)
    finally:
        with admin.connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        admin.dispose()


@pytest.fixture(scope="session")
def generated_profiles() -> dict[str, SourceProfile]:
    """Profiles of the generated sources, computed once per test session."""
    data = REPO_ROOT / "data"
    if not (data / "sources").is_dir() or not (data / "ground_truth").is_dir():
        pytest.skip("data/ not generated (run `make synth`)")
    return {p.source: p for p in profile_all(data / "sources", data / "master")}


DATA = REPO_ROOT / "data"


@pytest.fixture(scope="session")
def silver_db(pg_admin: Engine, generated_profiles: dict[str, SourceProfile]) -> Iterator[Engine]:
    name = f"m3tdf_test_{uuid.uuid4().hex[:12]}"
    with pg_admin.connect() as conn:
        conn.execute(text(f'CREATE DATABASE "{name}"'))
    url = pg_admin.url.set(database=name).render_as_string(hide_password=False)
    engine = create_engine(url)
    try:
        migrate(url)
        yield engine
    finally:
        engine.dispose()
        with pg_admin.connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))


def _confirm(engine: Engine, profile: SourceProfile, header: list[str]) -> None:
    proposal = propose(profile, header)
    with engine.begin() as conn:
        conn.execute(text(
            "INSERT INTO ops.mapping_versions (source, version, mapping, proposed_by, status, confirmed_by, "
            "confirmed_at, header, header_hash) SELECT :s, COALESCE(MAX(version), 0) + 1, CAST(:m AS jsonb), "
            "'heuristic', 'confirmed', 'owner', now(), CAST(:hdr AS jsonb), :h FROM ops.mapping_versions "
            "WHERE source = :s"),
            {"s": profile.source, "m": json.dumps({"columns": [c.model_dump() for c in proposal.columns]}),
             "hdr": json.dumps(header), "h": proposal.header_hash})


@dataclass(frozen=True)
class Loaded:
    first_pass: dict[str, list[BatchResult]]
    resumed: list[BatchResult]


@pytest.fixture(scope="session")
def loaded(silver_db: Engine, generated_profiles: dict[str, SourceProfile]) -> Loaded:
    """Ingest every file with PLT-01's second header (lot_no added) unconfirmed, then confirm it and re-run
    the blocked files — so blocking and resuming are both exercised before the content checks."""
    sources = discover_sources(DATA / "sources")
    for source, profile in generated_profiles.items():
        for i, variant in enumerate(profile.header_variants):
            if not (source == "plt01" and i == 1):
                _confirm(silver_db, profile, variant.columns)
    first = {source: [ingest_file(silver_db, source, f, DATA / "master") for f in files]
             for source, files in sources.items()}
    plt01 = generated_profiles["plt01"]
    _confirm(silver_db, plt01, plt01.header_variants[1].columns)
    by_name = {f.name: f for f in sources["plt01"]}
    resumed = [ingest_file(silver_db, "plt01", by_name[b.file_name], DATA / "master")
               for b in first["plt01"] if b.status == "blocked"]
    return Loaded(first, resumed)
