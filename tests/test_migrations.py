from __future__ import annotations

import shutil
from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.exc import IntegrityError

from db.migrate import MIGRATIONS_DIR, MigrationError, discover, migrate, require_postgres

OPS_TABLES = {"batches", "fingerprints", "mapping_versions", "exceptions", "lineage", "llm_calls"}


def _copy_migrations(tmp_path: Path) -> Path:
    target = tmp_path / "migrations"
    shutil.copytree(MIGRATIONS_DIR, target)
    return target


@pytest.fixture
def engine(pg_url: str) -> Iterator[Engine]:
    migrate(pg_url)
    eng = create_engine(pg_url)
    yield eng
    eng.dispose()


# --- no database needed -------------------------------------------------------


def test_repo_migrations_are_well_formed() -> None:
    migrations = discover()
    assert [m.version for m in migrations] == list(range(1, len(migrations) + 1))


def test_bad_filename_rejected(tmp_path: Path) -> None:
    (tmp_path / "1_schemas.sql").write_text("SELECT 1;", encoding="utf-8")
    with pytest.raises(MigrationError, match="NNNN_snake_case"):
        discover(tmp_path)


def test_duplicate_version_rejected(tmp_path: Path) -> None:
    (tmp_path / "0001_a.sql").write_text("SELECT 1;", encoding="utf-8")
    (tmp_path / "0001_b.sql").write_text("SELECT 1;", encoding="utf-8")
    with pytest.raises(MigrationError, match="already used"):
        discover(tmp_path)


def test_checksum_ignores_line_endings(tmp_path: Path) -> None:
    (tmp_path / "0001_a.sql").write_bytes(b"SELECT 1;\r\nSELECT 2;\r\n")
    lf = tmp_path / "lf"
    lf.mkdir()
    (lf / "0001_a.sql").write_bytes(b"SELECT 1;\nSELECT 2;\n")
    assert discover(tmp_path)[0].checksum == discover(lf)[0].checksum


@pytest.mark.parametrize("url", ["", "sqlite:///data/m3tdf.sqlite"])
def test_non_postgres_url_rejected(url: str) -> None:
    with pytest.raises(MigrationError):
        require_postgres(url)


# --- Postgres ------------------------------------------------------------------


@pytest.mark.postgres
def test_fresh_database_gets_schemas_and_ops_tables(engine: Engine) -> None:
    with engine.connect() as conn:
        schemas = set(conn.execute(text("SELECT schema_name FROM information_schema.schemata")).scalars())
        ops = set(conn.execute(text(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = 'ops'")).scalars())
        recorded = conn.execute(
            text("SELECT name FROM public.schema_migrations ORDER BY version")).scalars().all()
    assert {"bronze", "silver", "gold", "ops"} <= schemas
    assert ops == OPS_TABLES
    assert recorded == [m.name for m in discover()]


@pytest.mark.postgres
def test_rerun_is_a_no_op(engine: Engine, pg_url: str) -> None:
    assert migrate(pg_url) == []


@pytest.mark.postgres
def test_edited_applied_migration_is_refused(pg_url: str, tmp_path: Path) -> None:
    directory = _copy_migrations(tmp_path)
    migrate(pg_url, directory)
    first = sorted(directory.glob("*.sql"))[0]
    first.write_text(first.read_text(encoding="utf-8") + "\n-- edited\n", encoding="utf-8")
    with pytest.raises(MigrationError, match="changed after it was applied"):
        migrate(pg_url, directory)


@pytest.mark.postgres
def test_out_of_order_migration_is_refused(pg_url: str, tmp_path: Path) -> None:
    directory = _copy_migrations(tmp_path)
    (directory / "0099_late.sql").write_text("SELECT 1;", encoding="utf-8")
    migrate(pg_url, directory)
    (directory / "0050_backdated.sql").write_text("SELECT 1;", encoding="utf-8")
    with pytest.raises(MigrationError, match="older than the latest"):
        migrate(pg_url, directory)


@pytest.mark.postgres
def test_failing_migration_rolls_back_completely(pg_url: str, tmp_path: Path) -> None:
    directory = _copy_migrations(tmp_path)
    (directory / "0099_broken.sql").write_text(
        "CREATE TABLE ops.half_done (id int);\nSELECT * FROM table_that_does_not_exist;\n", encoding="utf-8"
    )
    with pytest.raises(MigrationError, match="0099_broken.sql failed"):
        migrate(pg_url, directory)
    eng = create_engine(pg_url)
    with eng.connect() as conn:
        assert conn.execute(text("SELECT to_regclass('ops.half_done')")).scalar() is None
        versions = conn.execute(text("SELECT version FROM public.schema_migrations")).scalars().all()
    eng.dispose()
    assert 99 not in versions
    assert len(versions) == len(discover())


@pytest.mark.postgres
def test_exception_rows_enforce_queue_contract(engine: Engine) -> None:
    with engine.begin() as conn:
        batch_id = conn.execute(
            text("INSERT INTO ops.batches (source) VALUES ('plt01') RETURNING batch_id")).scalar()
    row = {"b": batch_id, "rule": "V004", "key": "SO1|1", "reason": "unknown customer"}
    insert = text("INSERT INTO ops.exceptions (batch_id, rule_id, severity, row_key, reason, status) "
                  "VALUES (:b, :rule, 'block', :key, :reason, :status)")
    with engine.begin() as conn:
        conn.execute(insert, {**row, "status": "open"})
    # the same failure queued twice on a re-run, a resolution without who/when/what, a malformed rule id
    for bad in ({**row, "status": "open"}, {**row, "key": "SO1|2", "status": "resolved"},
                {**row, "key": "SO1|3", "rule": "RULE4", "status": "open"}):
        with pytest.raises(IntegrityError), engine.begin() as conn:
            conn.execute(insert, bad)


@pytest.mark.postgres
def test_only_one_confirmed_mapping_per_source(engine: Engine) -> None:
    insert = text("INSERT INTO ops.mapping_versions (source, version, mapping, proposed_by, status, "
                  "confirmed_by, confirmed_at) "
                  "VALUES ('plt02', :v, '{}', 'heuristic', 'confirmed', 'owner', now())")
    with engine.begin() as conn:
        conn.execute(insert, {"v": 1})
    with pytest.raises(IntegrityError), engine.begin() as conn:
        conn.execute(insert, {"v": 2})
