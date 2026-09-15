"""Score ops.drift_alerts in DATABASE_URL against drift.json → evals/results/drift_<date>.json.

Equivalent of `make eval-drift`.
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
from evals.drift_eval import score  # noqa: E402


def main() -> int:
    settings = get_settings()
    try:
        require_postgres(settings.database_url)
    except MigrationError as exc:
        print(f"eval-drift: {exc}", file=sys.stderr)
        return 1
    engine = create_engine(settings.database_url)
    with engine.connect() as conn:
        result = score(conn, settings.data_dir)
    engine.dispose()
    for e in result.events:
        if not e.loaded:
            print(f"{e.drift_id} {e.source} {e.effective}: not scored (no batch from that month on)")
            continue
        mark = "detected" if e.detected else "NOT detected"
        named = "named correctly" if e.named else "NOT named correctly"
        print(f"{e.drift_id} {e.source} {e.effective}: {mark}, {named}"
              + (f" (alert {e.alert_id}, {e.file_name})" if e.alert_id else ""))
        if e.message:
            print(f"    {e.message}")
    n = len(result.scored)
    unscored = f" ({len(result.events) - n} not scored)" if n < len(result.events) else ""
    print(f"detected {result.detected}/{n}, named {result.named}/{n}{unscored}, "
          f"false alerts {len(result.false_alerts)}")
    for a in result.false_alerts:
        print(f"  false alert {a['alert_id']} [{a['source']} {a['file_name']}] {a['message']}")
    out = REPO_ROOT / "evals" / "results" / f"drift_{date.today().isoformat()}.json"
    payload = {"detected": result.detected, "named": result.named,
               "events": [e.__dict__ for e in result.events], "false_alerts": result.false_alerts}
    out.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(f"wrote {out.relative_to(REPO_ROOT).as_posix()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
