-- Request-level idempotency for POST /ingest/{source} (docs/plan.md A7).
-- The key is the client's; the request hash pins what was asked (source + files) so a reused key with a
-- different request is refused. A completed request stores its response and replays it verbatim. A failed
-- request may be retried with the same key; per-file batches are keyed by content hash, so a retry can never
-- duplicate rows.

CREATE TABLE ops.ingest_requests (
    idempotency_key  text        PRIMARY KEY CHECK (idempotency_key ~ '^[A-Za-z0-9_.:-]{8,200}$'),
    source           text        NOT NULL,
    request_hash     char(64)    NOT NULL,
    requested_by     text        NOT NULL,
    status           text        NOT NULL CHECK (status IN ('in_progress', 'completed', 'failed')),
    attempts         integer     NOT NULL DEFAULT 1 CHECK (attempts >= 1),
    response         jsonb,
    error            text,
    created_at       timestamptz NOT NULL DEFAULT now(),
    updated_at       timestamptz NOT NULL DEFAULT now(),
    CHECK (status <> 'completed' OR response IS NOT NULL),
    CHECK (status <> 'failed' OR error IS NOT NULL)
);
CREATE INDEX ingest_requests_source_created_idx ON ops.ingest_requests (source, created_at DESC);
