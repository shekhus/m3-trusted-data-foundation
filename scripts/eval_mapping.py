"""Score mapping proposers against the answer key → evals/results/mapping_<date>.json. `make eval-mapping`."""

from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from app.config import get_settings  # noqa: E402
from evals.mapping_eval import run, summarise  # noqa: E402
from pipeline.mapper.heuristic import propose  # noqa: E402


def main() -> int:
    settings = get_settings()
    if not (settings.data_dir / "ground_truth" / "mappings.json").exists():
        print("eval-mapping: data/ not generated; run `make synth` first", file=sys.stderr)
        return 1
    scores = run(propose, settings.data_dir, REPO_ROOT / "evals" / "cases")
    summary = {"heuristic": summarise(scores)}
    print(f"{'proposer':<10} {'case':<24} {'headers':>7} {'top-1':>8} {'transforms':>10}")
    for proposer, cases in summary.items():
        for case, row in cases.items():
            print(f"{proposer:<10} {case:<24} {row['headers']:>7} {row['top1_accuracy']:>8.1%} "
                  f"{row['transform_accuracy']:>10.1%}")
    for header_score in scores:
        for error in header_score.errors:
            print(f"  miss [{header_score.case} {header_score.source} {header_score.first_file}] {error}")
    out = REPO_ROOT / "evals" / "results" / f"mapping_{date.today().isoformat()}.json"
    detail = [{"case": s.case, "source": s.source, "first_file": s.first_file, "columns": s.columns,
               "correct": s.correct, "errors": s.errors} for s in scores]
    out.write_text(json.dumps({"summary": summary, "headers": {"heuristic": detail}}, indent=2) + "\n",
                   encoding="utf-8", newline="\n")
    print(f"wrote {out.relative_to(REPO_ROOT).as_posix()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
