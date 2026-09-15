"""Ingest a source's files to bronze and, where the header has a confirmed mapping, to silver.

Equivalent of `make silver SRC=plt01`. Re-running is safe: an unchanged file returns its original batch, and a
previously blocked batch is mapped once its header's mapping has been confirmed.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from sqlalchemy import create_engine  # noqa: E402

from app.config import get_settings  # noqa: E402
from db.migrate import MigrationError, require_postgres  # noqa: E402
from pipeline.ingest import ingest_file  # noqa: E402
from pipeline.profile import discover_sources  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", help="source directory name under data/sources, e.g. plt01")
    args = parser.parse_args()
    settings = get_settings()
    try:
        require_postgres(settings.database_url)
    except MigrationError as exc:
        print(f"silver: {exc}", file=sys.stderr)
        return 1
    files = discover_sources(settings.sources_dir).get(args.source)
    if not files:
        print(f"silver: no files for source '{args.source}' under {settings.sources_dir}", file=sys.stderr)
        return 1
    engine = create_engine(settings.database_url)
    blocked = 0
    for path in files:
        r = ingest_file(engine, args.source, path)
        blocked += r.status == "blocked"
        note = " (replayed)" if r.replayed else ""
        counts = f"{r.silver_rows} silver, {r.parse_errors} parse errors"
        detail = r.error if r.status == "blocked" else counts
        print(f"{r.file_name}: {r.status}{note} - {r.rows} rows; {detail}")
    engine.dispose()
    if blocked:
        print(f"{blocked} file(s) blocked: confirm a mapping for their header "
              "(portal or POST /mappings/<id>/confirm) and run again")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
