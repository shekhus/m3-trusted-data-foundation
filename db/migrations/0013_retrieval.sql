-- Retrieval layer (docs/addendum1.md A14-A16, docs/decisions.md D-027): chunks and embeddings in Postgres.
-- Needs the pgvector extension (docker image pgvector/pgvector:pg18 locally; the pgvector template on Railway).

CREATE EXTENSION IF NOT EXISTS vector;
CREATE SCHEMA IF NOT EXISTS retrieval;

-- One row per chunk of data/kb (retrieval/chunker.py), replaced as a set by each index sync.
-- access_level and plant are what the pre-model access filter (retrieval/access.py) reads, in SQL.
CREATE TABLE retrieval.chunks (
    chunk_id        text        PRIMARY KEY,
    doc_id          text        NOT NULL,
    title           text        NOT NULL,
    section         text        NOT NULL,
    text            text        NOT NULL,
    text_sha256     char(64)    NOT NULL,
    doc_type        text        NOT NULL,
    version         text        NOT NULL,
    effective_date  date        NOT NULL,
    status          text        NOT NULL CHECK (status IN ('current', 'superseded', 'expired')),
    access_level    text        NOT NULL CHECK (access_level IN ('all', 'plant', 'restricted')),
    plant           text,
    supersedes      text,
    superseded_by   text,
    audience        text        NOT NULL,
    source_path     text        NOT NULL,
    CHECK (access_level <> 'plant' OR plant IS NOT NULL)  -- a plant-scoped chunk must name its plant
);
CREATE INDEX chunks_access_idx ON retrieval.chunks (access_level, plant);
CREATE INDEX chunks_doc_idx ON retrieval.chunks (doc_id);

-- Embeddings cached by content: a chunk whose text did not change is never re-embedded, and query embeddings
-- (input_type 'query') are cached the same way so evaluations are reproducible and cheap. No ANN index: an
-- exact scan over a few hundred vectors is faster than building one, and exact is what the eval should see.
CREATE TABLE retrieval.embeddings (
    text_sha256  char(64)    NOT NULL,
    model        text        NOT NULL,
    input_type   text        NOT NULL CHECK (input_type IN ('document', 'query')),
    dimensions   integer     NOT NULL CHECK (dimensions > 0),
    embedding    vector      NOT NULL,
    created_at   timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (text_sha256, model, input_type, dimensions),
    CHECK (vector_dims(embedding) = dimensions)
);

-- What each sync built from: the corpus hash and chunker version, so a stale index is detectable in SQL.
CREATE TABLE retrieval.index_builds (
    build_id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    corpus_sha256     char(64)    NOT NULL,
    chunker_version   integer     NOT NULL,
    embedding_model   text,
    dimensions        integer,
    chunks            integer     NOT NULL CHECK (chunks >= 0),
    embedded          integer     NOT NULL CHECK (embedded >= 0),
    built_at          timestamptz NOT NULL DEFAULT now()
);
