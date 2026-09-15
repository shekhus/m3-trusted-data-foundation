"""Score retrieval per challenge against the 53 golden questions. Equivalent of `make eval-rag`.

Needs `make index` first. Prints recall per challenge for every mode beside the TF-IDF floor, forbidden
documents retrieved, and exposures as an absolute count (filtered and unfiltered); writes
evals/results/rag_<date>.json.
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
from evals.rag_eval import CHALLENGES, score  # noqa: E402
from llm.client import DbRecorder  # noqa: E402
from llm.embeddings import EmbeddingError, embedder_for  # noqa: E402
from retrieval.hybrid import RetrievalError  # noqa: E402


def _pct(value: float | None) -> str:
    return "   -" if value is None else f"{value:>4.0%}"


def main() -> int:
    settings = get_settings()
    try:
        require_postgres(settings.database_url)
        engine = create_engine(settings.database_url)
        result = score(engine, settings.data_dir, embedder_for(settings, DbRecorder(engine)))
        engine.dispose()
    except (MigrationError, EmbeddingError, RetrievalError) as exc:
        print(f"eval-rag: {exc}", file=sys.stderr)
        return 1
    rows = {(c.challenge, c.mode): c for c in result.challenges}
    header = "".join(f"{m:>18}" for m in result.modes)
    print(f"top-{result.k}; each cell: doc recall / content recall (n questions)\n{'':<5}{header}")
    for challenge in CHALLENGES:
        cells = []
        for mode in result.modes:
            c = rows[(challenge, mode)]
            cells.append(f"{_pct(c.doc_recall)} / {_pct(c.content_recall)} ({c.questions:>2})")
        print(f"{challenge:<5}" + "".join(f"{cell:>18}" for cell in cells))
    for mode in result.modes:
        t = result.totals(mode)
        print(
            f"{mode:>7}: doc recall {_pct(t['doc_recall'])}, content recall {_pct(t['content_recall'])}, "
            f"forbidden retrieved {t['forbidden_retrieved']}, exposures {t['exposures']}"
        )
    print(
        f"unfiltered TF-IDF exposures on should-refuse questions: {result.unfiltered_exposures} "
        f"(what the pre-model filter prevents); over-restricted expected documents: {result.over_restricted}"
    )
    for note in result.notes:
        print(f"note: {note}")
    out = REPO_ROOT / "evals" / "results" / f"rag_{date.today().isoformat()}.json"
    out.write_text(json.dumps(result.as_dict(), indent=2) + "\n", encoding="utf-8", newline="\n")
    print(f"wrote {out.relative_to(REPO_ROOT).as_posix()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
