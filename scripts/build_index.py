"""Chunk data/kb/ into the retrieval index at data/index/. Equivalent of `make index`.

Deterministic: rebuilding an unchanged corpus rewrites identical files. Needs no database and no API key.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from app.config import get_settings  # noqa: E402
from retrieval.chunker import ChunkError  # noqa: E402
from retrieval.index import build  # noqa: E402


def main() -> int:
    data_dir = get_settings().data_dir
    kb_dir, index_dir = data_dir / "kb", data_dir / "index"
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
    shown = index_dir.relative_to(REPO_ROOT).as_posix() if index_dir.is_relative_to(REPO_ROOT) else index_dir
    print(f"wrote {shown}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
