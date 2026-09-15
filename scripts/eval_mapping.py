"""Score mapping proposers against the answer key → evals/results/mapping_<date>.json. `make eval-mapping`.

`--llm` also scores the LLM-assisted mapper (costs real API calls; every call is logged to ops.llm_calls when
DATABASE_URL reaches Postgres, otherwise kept in the results file). Headers where the LLM fell back to the
heuristic are counted separately, so a provider outage can never be reported as LLM accuracy.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import date
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from sqlalchemy import create_engine, text  # noqa: E402

from app.config import get_settings  # noqa: E402
from evals.mapping_eval import HeaderScore, build_cases, score, summarise  # noqa: E402
from llm.client import DbRecorder, MemoryRecorder, Recorder, build_client  # noqa: E402
from pipeline.mapper import llm as llm_mapper  # noqa: E402
from pipeline.mapper.heuristic import propose  # noqa: E402


def _recorder(database_url: str) -> Recorder:
    if database_url.startswith("postgresql"):
        try:
            engine = create_engine(database_url, connect_args={"connect_timeout": 3})
            with engine.connect() as conn:
                conn.execute(text("SELECT 1 FROM ops.llm_calls LIMIT 0"))
            return DbRecorder(engine)
        except Exception as exc:  # the eval still runs; calls are kept in the results file instead
            print(f"eval-mapping: not logging to ops.llm_calls ({exc.__class__.__name__})", file=sys.stderr)
    return MemoryRecorder()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--llm", action="store_true", help="also score the LLM mapper (paid API calls)")
    parser.add_argument("--pace", type=float, default=0.0,
                        help="seconds to wait between LLM headers (e.g. 60 on a tokens-per-minute free tier)")
    parser.add_argument("--case", help="only this case, e.g. mapping_unseen_headers")
    args = parser.parse_args()
    settings = get_settings()
    if not (settings.data_dir / "ground_truth" / "mappings.json").exists():
        print("eval-mapping: data/ not generated; run `make synth` first", file=sys.stderr)
        return 1

    cases = build_cases(settings.data_dir, REPO_ROOT / "evals" / "cases")
    if args.case:
        cases = [c for c in cases if c.name == args.case]
        if not cases:
            print(f"eval-mapping: no case named {args.case}", file=sys.stderr)
            return 1
    heuristic_scores = [score(c, propose(c.profile, c.header)) for c in cases]
    scores: dict[str, list[HeaderScore]] = {"heuristic": heuristic_scores}
    llm_report: dict = {"run": False}
    if args.llm:
        recorder = _recorder(settings.database_url)
        client = build_client(settings, recorder)
        if client is None:
            llm_report = {"run": False, "reason": "LLM_PROVIDER=none"}
        else:
            llm_scores: list[HeaderScore] = []
            fallbacks: list[dict] = []
            for n, case in enumerate(cases):
                if n and args.pace:
                    time.sleep(args.pace)
                # no confirmed examples: the unseen case is PLT-01's data, so examples would leak the answer
                result = llm_mapper.propose(case.profile, case.header, client)
                if result.used_llm:
                    llm_scores.append(score(case, result.proposal))
                else:
                    fallbacks.append({"case": case.name, "source": case.profile.source,
                                      "first_file": case.first_file, "notes": result.notes})
            scores["llm"] = llm_scores
            calls = recorder.calls if isinstance(recorder, MemoryRecorder) else []
            llm_report = {"run": True, "model": settings.llm_model, "headers_answered": len(llm_scores),
                          "headers_fell_back": len(fallbacks), "fallbacks": fallbacks,
                          "calls": [c.__dict__ for c in calls]}

    summary = {name: summarise(s) for name, s in scores.items() if s}
    print(f"{'proposer':<10} {'case':<24} {'headers':>7} {'top-1':>8} {'transforms':>10}")
    for proposer, by_case in summary.items():
        for case_name, row in by_case.items():
            print(f"{proposer:<10} {case_name:<24} {row['headers']:>7} {row['top1_accuracy']:>8.1%} "
                  f"{row['transform_accuracy']:>10.1%}")
    if llm_report.get("run") and llm_report["headers_fell_back"]:
        print(f"llm: {llm_report['headers_fell_back']} of {len(cases)} headers fell back to the heuristic "
              f"and are NOT scored as LLM; first reason: {llm_report['fallbacks'][0]['notes'][0]}")
    for proposer, header_scores in scores.items():
        for hs in header_scores:
            for error in hs.errors:
                print(f"  miss [{proposer} {hs.case} {hs.source} {hs.first_file}] {error}")

    suffix = f"_{args.case}" if args.case else ""
    out = REPO_ROOT / "evals" / "results" / f"mapping_{date.today().isoformat()}{suffix}.json"
    detail = {name: [{"case": s.case, "source": s.source, "first_file": s.first_file, "columns": s.columns,
                      "correct": s.correct, "errors": s.errors} for s in header_scores]
              for name, header_scores in scores.items()}
    body = json.dumps({"summary": summary, "llm": llm_report, "headers": detail}, indent=2, default=str)
    out.write_text(body + "\n", encoding="utf-8", newline="\n")
    print(f"wrote {out.relative_to(REPO_ROOT).as_posix()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
