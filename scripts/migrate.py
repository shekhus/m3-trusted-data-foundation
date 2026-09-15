"""Apply db/migrations to DATABASE_URL (Postgres only). Equivalent of `make migrate`."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import create_engine  # noqa: E402

from app.config import get_settings  # noqa: E402
from db.migrate import MigrationError, migrate  # noqa: E402
from pipeline.lineage import backfill  # noqa: E402


def main() -> int:
    url = get_settings().database_url
    try:
        applied = migrate(url)
    except MigrationError as exc:
        print(f"migrate: {exc}", file=sys.stderr)
        return 1
    # data step after schema: lineage for mappings confirmed before lineage existed (idempotent, cheap)
    engine = create_engine(url)
    with engine.begin() as conn:
        backfilled = backfill(conn)
    engine.dispose()
    if backfilled:
        print(f"lineage backfilled: {backfilled} rows")
    if applied:
        for name in applied:
            print(f"applied {name}")
    else:
        print("database is up to date")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
