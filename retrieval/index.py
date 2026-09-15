"""The retrieval index on disk: chunks plus a manifest (docs/addendum1.md A14; embeddings are added by A15).

`build` is deterministic (the same corpus gives byte-identical files) and the manifest records the corpus hash
and chunker version, so a stale index is refused instead of silently served.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import Path

from retrieval.chunker import CHUNKER_VERSION, Chunk, chunk_corpus

CHUNKS_FILE = "chunks.jsonl"
MANIFEST_FILE = "manifest.json"


@dataclass(frozen=True)
class IndexInfo:
    corpus_sha256: str
    chunker_version: int
    documents: int
    resolutions: int
    chunks: int


class StaleIndexError(ValueError):
    pass


def corpus_hash(kb_dir: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(p for p in kb_dir.rglob("*") if p.is_file()):
        digest.update(path.relative_to(kb_dir).as_posix().encode())
        digest.update(b"\0")
        digest.update(path.read_bytes().replace(b"\r\n", b"\n"))
    return digest.hexdigest()


def build(kb_dir: Path, index_dir: Path) -> IndexInfo:
    chunks = chunk_corpus(kb_dir)
    index_dir.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps(c.as_dict(), ensure_ascii=False, sort_keys=True) for c in chunks]
    (index_dir / CHUNKS_FILE).write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    info = IndexInfo(
        corpus_sha256=corpus_hash(kb_dir), chunker_version=CHUNKER_VERSION,
        documents=len({c.doc_id for c in chunks if c.doc_type != "prior_resolution"}),
        resolutions=sum(c.doc_type == "prior_resolution" for c in chunks), chunks=len(chunks))
    (index_dir / MANIFEST_FILE).write_text(json.dumps(asdict(info), indent=2) + "\n", encoding="utf-8",
                                           newline="\n")
    return info


def load(index_dir: Path, kb_dir: Path | None = None) -> list[Chunk]:
    """Chunks from disk. With `kb_dir`, refuse an index built from a different corpus or chunker version."""
    manifest_path = index_dir / MANIFEST_FILE
    if not manifest_path.exists():
        raise StaleIndexError(f"no index at {index_dir}; run `make index`")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest["chunker_version"] != CHUNKER_VERSION:
        raise StaleIndexError(f"index built by chunker v{manifest['chunker_version']}; run `make index`")
    if kb_dir is not None and manifest["corpus_sha256"] != corpus_hash(kb_dir):
        raise StaleIndexError("the knowledge base changed since the index was built; run `make index`")
    lines = (index_dir / CHUNKS_FILE).read_text(encoding="utf-8").splitlines()
    return [Chunk(**json.loads(line)) for line in lines if line]
