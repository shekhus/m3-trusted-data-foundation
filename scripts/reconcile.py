"""Run the reconciliation job against DATABASE_URL and score it. Equivalent of `make reconcile` (the nightly
job).

Prints consumer-view agreement, each BI tool's identified and undetermined causes, and the score against
data/ground_truth/reconciliation.json; writes evals/results/reconciliation_<date>.json.
"""

from __future__ import annotations

import json
import sys
from dataclasses import asdict
from datetime import date
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from sqlalchemy import create_engine  # noqa: E402

from app.config import get_settings  # noqa: E402
from db.migrate import MigrationError, require_postgres  # noqa: E402
from evals.reconciliation_eval import score  # noqa: E402
from pipeline.reconcile import run  # noqa: E402


def main() -> int:
    settings = get_settings()
    try:
        require_postgres(settings.database_url)
    except MigrationError as exc:
        print(f"reconcile: {exc}", file=sys.stderr)
        return 1
    engine = create_engine(settings.database_url)
    result = run(engine, settings.data_dir, "job:reconcile")
    print(
        f"run {result.run_id}: {result.status} — {result.consumer_periods} consumer comparisons, "
        f"{result.consumer_red} red"
    )
    if result.status == "no_data":
        print("nothing published to gold yet; publish first (portal Batches tab or POST /publish/<source>)")
        engine.dispose()
        return 0
    for tool, s in result.tools.items():
        print(
            f"{tool}: identified {s['identified_causes']}, undetermined {s['undetermined_causes']}, "
            f"dominant {s['dominant_cause']}, export match {s['export_match_share']:.1%}, "
            f"months flagged {s['months_flagged']}/{s['months']}"
        )
    with engine.connect() as conn:
        scored = score(conn, settings.data_dir, result.run_id)
    engine.dispose()
    for t in scored.tools:
        print(
            f"  score {t.tool}: {'PASS' if t.passed else 'FAIL'} — "
            f"causes {'ok' if t.causes_correct else 'WRONG'}, "
            f"gaps {t.gaps_flagged}/{t.gaps_expected}, dominant {t.dominant} (key {t.dominant_expected}), "
            f"worst contribution error {t.worst_contribution_error:.5f}, "
            f"lines dropped exact {t.lines_dropped_exact}"
        )
    print(f"overall: {'PASS' if scored.passed else 'FAIL'}")
    out = REPO_ROOT / "evals" / "results" / f"reconciliation_{date.today().isoformat()}.json"
    payload = {
        "run_id": result.run_id,
        "status": result.status,
        "tools": result.tools,
        "score": {
            "passed": scored.passed,
            "consumer_red": scored.consumer_red,
            "tools": [asdict(t) for t in scored.tools],
        },
    }
    out.write_text(json.dumps(payload, indent=2, default=str) + "\n", encoding="utf-8", newline="\n")
    print(f"wrote {out.relative_to(REPO_ROOT).as_posix()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
