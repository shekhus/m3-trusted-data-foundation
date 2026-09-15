"""Chunk data/kb/ and sync the retrieval index. Equivalent of `make index`.

Writes data/index/ (chunks + manifest, deterministic), then, when DATABASE_URL is Postgres, syncs
retrieval.chunks and embeds every chunk text that has no vector yet for EMBEDDING_MODEL (an unchanged corpus
makes no API call). `--files-only` skips the database; EMBEDDING_PROVIDER=none syncs chunks without vectors.
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
from llm.client import DbRecorder  # noqa: E402
from llm.embeddings import EmbeddingError, embedder_for  # noqa: E402
from retrieval.chunker import ChunkError  # noqa: E402
from retrieval.index import build  # noqa: E402
from retrieval.store import sync  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--files-only", action="store_true", help="write data/index/ only; no database")
    args = parser.parse_args()
    settings = get_settings()
    kb_dir, index_dir = settings.data_dir / "kb", settings.data_dir / "index"
    if not (kb_dir / "docs").is_dir():
        print(f"index: no knowledge base at {kb_dir}; run `make synth`", file=sys.stderr)
        return 1
    try:
        info = build(kb_dir, index_dir)
    except (ChunkError, ValueError) as exc:
        print(f"index: {exc}", file=sys.stderr)
        return 1
    print(f"indexed {info.documents} documents and {info.resolutions} prior resolutions into {info.chunks} "
          f"chunks (chunker v{info.chunker_version}, corpus {info.corpus_sha256[:12]})")
    if args.files_only:
        return 0
    try:
        require_postgres(settings.database_url)
        engine = create_engine(settings.database_url)
        embedder = embedder_for(settings, DbRecorder(engine))
        result = sync(engine, kb_dir, embedder)
        engine.dispose()
    except (MigrationError, EmbeddingError) as exc:
        print(f"index: {exc}", file=sys.stderr)
        return 1
    model = f"{result.model}: {result.embedded} chunks embedded" if result.model else "no embeddings"
    print(f"synced retrieval.chunks: {result.chunks} chunks, {result.removed} removed ({model})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
