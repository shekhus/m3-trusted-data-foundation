"""Compile metrics/*.yaml → metrics/compiled/*.sql; with --apply, create the views in DATABASE_URL.

Equivalent of `make metrics`. Compiling needs no database; --apply needs Postgres with migrations applied.
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
from metrics.compiler import CompileError, apply, compile_all, write_compiled  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="create the views in DATABASE_URL")
    args = parser.parse_args()
    try:
        compiled = compile_all()
        for path in write_compiled(compiled):
            print(f"compiled {path.relative_to(REPO_ROOT).as_posix()}")
        if args.apply:
            url = get_settings().database_url
            require_postgres(url)
            engine = create_engine(url)
            with engine.begin() as conn:
                apply(conn, compiled)
            engine.dispose()
            print(f"applied {len(compiled)} views")
    except (CompileError, MigrationError) as exc:
        print(f"metrics: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
