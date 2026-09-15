"""The index in Postgres (D-027): sync chunks from data/kb, embed what changed, read filtered candidates.

`sync` replaces retrieval.chunks with the current corpus and embeds only texts whose content hash has no
vector yet for the configured model, so an unchanged corpus costs zero API calls. Query vectors are cached the
same way. Candidates are always selected through `AccessContext.sql`: nothing above the asker's level leaves
the database.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import Connection, Engine, text

from llm.embeddings import Embedder, InputType
from retrieval.access import AccessContext
from retrieval.chunker import CHUNKER_VERSION, Chunk, chunk_corpus
from retrieval.index import corpus_hash

CHUNK_COLUMNS = (
    "chunk_id",
    "doc_id",
    "title",
    "section",
    "text",
    "doc_type",
    "version",
    "effective_date",
    "status",
    "access_level",
    "plant",
    "supersedes",
    "superseded_by",
    "audience",
    "source_path",
)


@dataclass(frozen=True)
class SyncResult:
    chunks: int
    removed: int
    embedded: int
    model: str | None


def sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def vector_literal(vector: list[float]) -> str:
    return "[" + ",".join(f"{x:.7g}" for x in vector) + "]"


def _missing(conn: Connection, hashes: list[str], embedder: Embedder, input_type: InputType) -> set[str]:
    have = set(
        conn.execute(
            text(
                "SELECT text_sha256 FROM retrieval.embeddings "
                "WHERE model = :m AND input_type = :t AND dimensions = :d "
                "AND text_sha256 = ANY(:h)"
            ),
            {"m": embedder.model, "t": input_type, "d": embedder.dimensions, "h": hashes},
        ).scalars()
    )
    return set(hashes) - have


def _store(
    conn: Connection,
    texts: dict[str, str],
    vectors: list[list[float]],
    embedder: Embedder,
    input_type: InputType,
) -> None:
    conn.execute(
        text(
            "INSERT INTO retrieval.embeddings (text_sha256, model, input_type, dimensions, embedding) "
            "VALUES (:h, :m, :t, :d, CAST(:v AS vector)) ON CONFLICT DO NOTHING"
        ),
        [
            {"h": h, "m": embedder.model, "t": input_type, "d": embedder.dimensions, "v": vector_literal(v)}
            for h, v in zip(texts, vectors, strict=True)
        ],
    )


def sync(engine: Engine, kb_dir: Path, embedder: Embedder | None) -> SyncResult:
    chunks = chunk_corpus(kb_dir)
    rows = [
        {**{c: getattr(chunk, c) for c in CHUNK_COLUMNS}, "text_sha256": sha256(chunk.text)}
        for chunk in chunks
    ]
    embedded = 0
    if embedder is not None:  # call the API outside the write transaction
        with engine.connect() as conn:
            missing = _missing(conn, [r["text_sha256"] for r in rows], embedder, "document")
        todo = {r["text_sha256"]: r["text"] for r in rows if r["text_sha256"] in missing}
        if todo:
            vectors = embedder.embed(list(todo.values()), "document")
            with engine.begin() as conn:
                _store(conn, todo, vectors, embedder, "document")
            embedded = len(todo)
    columns = [*CHUNK_COLUMNS, "text_sha256"]
    with engine.begin() as conn:
        removed = conn.execute(
            text("DELETE FROM retrieval.chunks WHERE NOT (chunk_id = ANY(:ids))"),
            {"ids": [c.chunk_id for c in chunks]},
        ).rowcount
        updates = ", ".join(f"{c} = EXCLUDED.{c}" for c in columns if c != "chunk_id")
        conn.execute(
            text(
                f"INSERT INTO retrieval.chunks ({', '.join(columns)}) "
                f"VALUES ({', '.join(':' + c for c in columns)}) "
                f"ON CONFLICT (chunk_id) DO UPDATE SET {updates}"
            ),
            rows,
        )
        conn.execute(
            text(
                "INSERT INTO retrieval.index_builds "
                "(corpus_sha256, chunker_version, embedding_model, dimensions, "
                "chunks, embedded) VALUES (:sha, :v, :m, :d, :n, :e)"
            ),
            {
                "sha": corpus_hash(kb_dir),
                "v": CHUNKER_VERSION,
                "m": embedder.model if embedder else None,
                "d": embedder.dimensions if embedder else None,
                "n": len(chunks),
                "e": embedded,
            },
        )
    return SyncResult(len(chunks), removed, embedded, embedder.model if embedder else None)


def query_vector(engine: Engine, embedder: Embedder, query: str) -> str:
    """The query's vector as a pgvector literal, from the cache or one embedding call."""
    h = sha256(query)
    with engine.connect() as conn:
        cached = conn.execute(
            text(
                "SELECT embedding::text FROM retrieval.embeddings WHERE text_sha256 = :h AND model = :m "
                "AND input_type = 'query' AND dimensions = :d"
            ),
            {"h": h, "m": embedder.model, "d": embedder.dimensions},
        ).scalar()
    if cached is not None:
        return str(cached)
    vector = embedder.embed([query], "query")[0]
    with engine.begin() as conn:
        _store(conn, {h: query}, [vector], embedder, "query")
    return vector_literal(vector)


def candidates(conn: Connection, access: AccessContext | None) -> list[Chunk]:
    """Every chunk the asker may see. `access=None` means unfiltered: evaluation baselines only."""
    where, params = access.sql("c") if access is not None else ("true", {})
    rows = (
        conn.execute(
            text(
                f"SELECT {', '.join('c.' + col for col in CHUNK_COLUMNS)} "
                f"FROM retrieval.chunks c WHERE {where} "
                "ORDER BY c.chunk_id"
            ),
            params,
        )
        .mappings()
        .all()
    )
    return [Chunk(**{**dict(r), "effective_date": r["effective_date"].isoformat()}) for r in rows]


def vector_ranking(
    conn: Connection, access: AccessContext | None, embedder: Embedder, query_literal: str
) -> list[tuple[str, float]]:
    """(chunk_id, cosine similarity), best first, over the permitted chunks only; the filter runs in SQL."""
    where, params = access.sql("c") if access is not None else ("true", {})
    rows = conn.execute(
        text(
            "SELECT c.chunk_id, 1 - (e.embedding <=> CAST(:q AS vector)) AS similarity "
            "FROM retrieval.chunks c JOIN retrieval.embeddings e ON e.text_sha256 = c.text_sha256 "
            "AND e.model = :m AND e.input_type = 'document' AND e.dimensions = :d "
            f"WHERE {where} ORDER BY e.embedding <=> CAST(:q AS vector), c.chunk_id"
        ),
        {**params, "q": query_literal, "m": embedder.model, "d": embedder.dimensions},
    ).all()
    return [(r.chunk_id, float(r.similarity)) for r in rows]
