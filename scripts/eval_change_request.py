"""Score the lot_no change request (V012) in DATABASE_URL from the raw extract. Equivalent of
`make eval-change-request`; writes evals/results/change_request_<date>.json.
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
from evals.change_request_eval import score  # noqa: E402


def main() -> int:
    settings = get_settings()
    try:
        require_postgres(settings.database_url)
        engine = create_engine(settings.database_url)
        with engine.connect() as conn:
            result = score(conn, settings.data_dir)
        engine.dispose()
    except (MigrationError, ValueError) as exc:
        print(f"eval-change-request: {exc}", file=sys.stderr)
        return 1
    print(
        f"{result.source} from {result.effective}: expected block {result.expected_block}, warn "
        f"{result.expected_warn} | raised block {result.raised_block}, warn {result.raised_warn}"
    )
    print(
        f"missing {result.missing}, unexpected {result.unexpected}, wrong severity {result.wrong_severity}, "
        f"bronze rows {result.bronze_rows} = silver rows {result.silver_rows}"
    )
    for source, counts in result.other_sources.items():
        print(f"{source}: expected {counts['expected']}, raised {counts['raised']}")
    print(f"overall: {'PASS' if result.passed else 'FAIL'}")
    out = REPO_ROOT / "evals" / "results" / f"change_request_{date.today().isoformat()}.json"
    payload = {**asdict(result), "passed": result.passed}
    out.write_text(json.dumps(payload, indent=2, default=str) + "\n", encoding="utf-8", newline="\n")
    print(f"wrote {out.relative_to(REPO_ROOT).as_posix()}")
    return 0 if result.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
