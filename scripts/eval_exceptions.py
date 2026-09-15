"""Score ops.exceptions in DATABASE_URL against faults.json → evals/results/exceptions_<date>.json.

Equivalent of `make eval-exceptions`. Scores only sources with validated batches, and says which ones — a
partial load is reported as partial, not as the pipeline's recall.
"""

from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from sqlalchemy import create_engine  # noqa: E402

from app.config import get_settings  # noqa: E402
from db.migrate import MigrationError, require_postgres  # noqa: E402
from evals.exception_eval import TARGET, score  # noqa: E402


def main() -> int:
    settings = get_settings()
    try:
        require_postgres(settings.database_url)
    except MigrationError as exc:
        print(f"eval-exceptions: {exc}", file=sys.stderr)
        return 1
    engine = create_engine(settings.database_url)
    with engine.connect() as conn:
        result = score(conn, settings.data_dir)
    engine.dispose()
    if not result.sources:
        print("eval-exceptions: no validated batches; run `make silver SRC=<source>` first", file=sys.stderr)
        return 1

    missing = sorted({"plt01", "plt02", "plt03"} - set(result.sources))
    not_loaded = f"  (NOT loaded: {', '.join(missing)})" if missing else ""
    print(f"sources scored: {', '.join(result.sources)}{not_loaded}")
    print(f"{'rule':<6}{'seeded':>8}{'detectable':>12}{'(key says)':>12}{'caught':>8}{'raised':>8}"
          f"{'recall':>9}")
    for r in result.rules:
        key_says = "-" if r.answer_key_detectable is None else r.answer_key_detectable
        print(f"{r.rule:<6}{r.seeded:>8}{r.detectable:>12}{key_says:>12}{r.caught:>8}{r.raised:>8}"
              f"{r.recall:>9.1%}")
    print(f"recall {result.recall:.1%} | row precision {result.row_precision:.1%} | pair precision "
          f"{result.pair_precision:.1%} | target {TARGET:.0%} "
          f"{'MET' if result.as_dict()['meets_target'] else 'NOT MET'}")
    for u in result.unexplained:
        print(f"  unexplained [{u['source']} {u['rule']} {u['row_key']}] {u['reason']}")
    out = REPO_ROOT / "evals" / "results" / f"exceptions_{date.today().isoformat()}.json"
    out.write_text(json.dumps(result.as_dict(), indent=2, default=str) + "\n", encoding="utf-8", newline="\n")
    print(f"wrote {out.relative_to(REPO_ROOT).as_posix()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
