"""Re-run every rule on validated/published batches in DATABASE_URL. Equivalent of `make revalidate`.

Use after a rule or the field dictionary changes (e.g. the lot_no change request, V012): new exceptions are
queued, ones that no longer apply are auto-resolved, nothing is deleted. A published batch is withdrawn from
gold by re-validation and must be published again.
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from sqlalchemy import create_engine, text  # noqa: E402

from app.config import get_settings  # noqa: E402
from db.migrate import MigrationError, require_postgres  # noqa: E402
from pipeline.validate import load_masters, validate_batch  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", nargs="?", help="only this source (e.g. plt01); default all")
    args = parser.parse_args()
    settings = get_settings()
    try:
        require_postgres(settings.database_url)
    except MigrationError as exc:
        print(f"revalidate: {exc}", file=sys.stderr)
        return 1
    engine = create_engine(settings.database_url)
    masters = load_masters(settings.data_dir / "master")
    with engine.connect() as conn:
        batches = conn.execute(
            text(
                "SELECT batch_id, source, file_name, status FROM ops.batches "
                "WHERE status IN ('validated', 'published') AND (CAST(:s AS text) IS NULL OR source = :s) "
                "ORDER BY source, file_name"
            ),
            {"s": args.source},
        ).all()
    totals: Counter[str] = Counter()
    withdrawn = 0
    for b in batches:
        with engine.begin() as conn:
            counts = validate_batch(conn, str(b.batch_id), masters)
        totals.update(counts)
        withdrawn += b.status == "published"
        print(f"{b.source} {b.file_name}: " + ", ".join(f"{k} {v}" for k, v in counts.items() if v))
    engine.dispose()
    print(
        f"{len(batches)} batches re-validated; violations "
        + ", ".join(f"{k} {v}" for k, v in sorted(totals.items()))
    )
    if withdrawn:
        print(f"{withdrawn} published batches were withdrawn from gold; publish them again")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
