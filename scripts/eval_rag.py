"""Score retrieval, and with --answers the generated answers, per challenge against the 53 golden questions.
Equivalent of `make eval-rag` (retrieval) and `make eval-answers` (retrieval + answers).

Needs `make index` first. Retrieval: recall per challenge for every mode beside the TF-IDF floor, forbidden
documents retrieved, exposures as an absolute count (filtered and unfiltered). Answers (LLM_PROVIDER and
EMBEDDING_PROVIDER configured): one verdict per question, leaks as an absolute count. Writes
evals/results/rag_<date>.json and answers_<date>.json.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from sqlalchemy import create_engine  # noqa: E402

from app.config import get_settings  # noqa: E402
from db.migrate import MigrationError, require_postgres  # noqa: E402
from evals import answer_eval  # noqa: E402
from evals.rag_eval import CHALLENGES, score  # noqa: E402
from llm.client import DbRecorder, build_client  # noqa: E402
from llm.embeddings import EmbeddingError, embedder_for  # noqa: E402
from retrieval.hybrid import RetrievalError  # noqa: E402


def _pct(value: float | None) -> str:
    return "   -" if value is None else f"{value:>4.0%}"


def _write(name: str, payload: dict) -> None:
    out = REPO_ROOT / "evals" / "results" / f"{name}_{date.today().isoformat()}.json"
    out.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(f"wrote {out.relative_to(REPO_ROOT).as_posix()}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--answers", action="store_true", help="also generate and score answers (LLM calls)")
    parser.add_argument("--pace", type=float, default=0.0, help="seconds between LLM calls (rate limits)")
    parser.add_argument("--only", nargs="*", help="answer only these question ids (e.g. Q010 Q031)")
    parser.add_argument(
        "--rescore", type=Path, help="re-apply answer verdicts to a saved answers_<date>.json"
    )
    args = parser.parse_args()
    settings = get_settings()
    if args.rescore:
        rescored = answer_eval.rescore(args.rescore, settings.data_dir)
        _report_answers(rescored)
        return 0
    try:
        require_postgres(settings.database_url)
        engine = create_engine(settings.database_url)
        recorder = DbRecorder(engine)
        embedder = embedder_for(settings, recorder)
        result = score(engine, settings.data_dir, embedder)
        answers = None
        if args.answers:
            llm = build_client(settings, recorder)
            if llm is None or embedder is None:
                print(
                    "eval-rag: --answers needs LLM_PROVIDER and EMBEDDING_PROVIDER configured",
                    file=sys.stderr,
                )
                return 1
            answers = answer_eval.score(
                engine, settings.data_dir, embedder, llm, args.pace, set(args.only) if args.only else None
            )
        engine.dispose()
    except (MigrationError, EmbeddingError, RetrievalError, ValueError) as exc:
        print(f"eval-rag: {exc}", file=sys.stderr)
        return 1

    rows = {(c.challenge, c.mode): c for c in result.challenges}
    header = "".join(f"{m:>18}" for m in result.modes)
    print(f"RETRIEVAL top-{result.k}; each cell: doc recall / content recall (n questions)\n{'':<5}{header}")
    for challenge in CHALLENGES:
        cells = []
        for mode in result.modes:
            c = rows[(challenge, mode)]
            cells.append(f"{_pct(c.doc_recall)} / {_pct(c.content_recall)} ({c.questions:>2})")
        print(f"{challenge:<5}" + "".join(f"{cell:>18}" for cell in cells))
    for mode in result.modes:
        t = result.totals(mode)
        print(
            f"{mode:>9}: doc recall {_pct(t['doc_recall'])}, content recall {_pct(t['content_recall'])}, "
            f"forbidden retrieved {t['forbidden_retrieved']}, exposures {t['exposures']}"
        )
    print(
        f"unfiltered TF-IDF exposures on should-refuse questions: {result.unfiltered_exposures} "
        f"(what the pre-model filter prevents); over-restricted expected documents: {result.over_restricted}"
    )
    for note in result.notes:
        print(f"note: {note}")
    _write("rag", result.as_dict())

    if answers is not None:
        _report_answers(answers)
    return 0


def _report_answers(answers: answer_eval.AnswerScore) -> None:
    summary = answers.as_dict()
    print(
        f"\nANSWERS ({len(answers.results)} questions): accuracy {answers.accuracy:.0%}, "
        f"LEAKS {answers.leaks}; verdicts {summary['verdicts']}; outcomes {summary['outcomes']}"
    )
    for challenge, counts in answers.by_challenge().items():
        if counts["questions"]:
            detail = ", ".join(f"{k} {v}" for k, v in counts.items() if k not in ("questions", "good"))
            print(f"  {challenge:<4} {counts['good']}/{counts['questions']} good ({detail})")
    for r in answers.results:
        if r.verdict not in answer_eval.GOOD or r.forbidden_terms:
            print(
                f"  [{r.question_id} {r.challenge} {r.verdict} / {r.outcome}] missing {r.missing_terms} "
                f"forbidden {r.forbidden_terms}: {r.answer[:160]!r}"
            )
    for note in answers.notes:
        print(f"  note: {note}")
    _write("answers", summary)


if __name__ == "__main__":
    raise SystemExit(main())
