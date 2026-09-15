-- Core ops tables named in CLAUDE.md: batches, fingerprints, mapping_versions, exceptions, lineage, llm_calls.
-- Columns follow docs/plan.md §2.4 (A4, A6, A7, A8, A9). Later tables (tool_calls, runs, reconciliation)
-- are added in the week their feature lands.

-- One row per ingest run (A7). The Idempotency-Key and request hash make a replay return the original result.
CREATE TABLE ops.batches (
    batch_id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    source           text        NOT NULL,
    file_name        text,
    idempotency_key  text        UNIQUE,
    request_hash     char(64),
    status           text        NOT NULL DEFAULT 'received'
                     CHECK (status IN ('received', 'mapped', 'validated', 'published', 'blocked', 'failed')),
    stale            boolean     NOT NULL DEFAULT false,
    period_end       date,
    row_count        integer     CHECK (row_count >= 0),
    response         jsonb,
    error            text,
    received_at      timestamptz NOT NULL DEFAULT now(),
    finished_at      timestamptz,
    CHECK (idempotency_key IS NULL OR request_hash IS NOT NULL),
    CHECK (status <> 'failed' OR error IS NOT NULL)
);
CREATE INDEX batches_source_received_idx ON ops.batches (source, received_at DESC);

-- Schema fingerprint per batch (A9): sha256 of the sorted (col, dtype, null_bucket, pattern_class) tuples.
CREATE TABLE ops.fingerprints (
    fingerprint_id  bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    batch_id        uuid        NOT NULL REFERENCES ops.batches (batch_id),
    source          text        NOT NULL,
    fingerprint     char(64)    NOT NULL,
    columns         jsonb       NOT NULL,
    created_at      timestamptz NOT NULL DEFAULT now(),
    UNIQUE (batch_id)
);
CREATE INDEX fingerprints_source_created_idx ON ops.fingerprints (source, created_at DESC);

-- Every mapping is versioned (A4). The model or heuristic proposes; only an owner confirms.
-- mapping: {source_col: {canonical_col, confidence, rationale, transform}}.
CREATE TABLE ops.mapping_versions (
    mapping_version_id  bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    source              text        NOT NULL,
    version             integer     NOT NULL CHECK (version > 0),
    mapping             jsonb       NOT NULL,
    proposed_by         text        NOT NULL CHECK (proposed_by IN ('heuristic', 'llm', 'human')),
    status              text        NOT NULL DEFAULT 'proposed'
                        CHECK (status IN ('proposed', 'confirmed', 'rejected', 'superseded')),
    fingerprint         char(64),
    proposed_at         timestamptz NOT NULL DEFAULT now(),
    confirmed_by        text,
    confirmed_at        timestamptz,
    UNIQUE (source, version),
    CHECK (status NOT IN ('confirmed', 'superseded') OR (confirmed_by IS NOT NULL AND confirmed_at IS NOT NULL))
);
-- At most one live confirmed mapping per source.
CREATE UNIQUE INDEX mapping_versions_one_confirmed_idx
    ON ops.mapping_versions (source) WHERE status = 'confirmed';

-- Exception queue (A6). Nothing is dropped silently: a row failing a blocking rule lands here.
-- Unique per (batch, rule, row) so a re-run of validation cannot queue the same failure twice.
CREATE TABLE ops.exceptions (
    exception_id   bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    batch_id       uuid        NOT NULL REFERENCES ops.batches (batch_id),
    rule_id        text        NOT NULL CHECK (rule_id ~ '^V[0-9]{3}$'),
    severity       text        NOT NULL CHECK (severity IN ('block', 'warn')),
    row_key        text        NOT NULL,
    reason         text        NOT NULL,
    suggested_fix  text,
    owner          text,
    status         text        NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'assigned', 'resolved')),
    resolution     text,
    resolved_by    text,
    resolved_at    timestamptz,
    created_at     timestamptz NOT NULL DEFAULT now(),
    UNIQUE (batch_id, rule_id, row_key),
    CHECK (status <> 'assigned' OR owner IS NOT NULL),
    CHECK (status <> 'resolved' OR (resolved_by IS NOT NULL AND resolved_at IS NOT NULL AND resolution IS NOT NULL))
);
CREATE INDEX exceptions_status_rule_idx ON ops.exceptions (status, rule_id);

-- Column lineage (A8): lets drift name exactly which gold column and report a broken source column feeds.
CREATE TABLE ops.lineage (
    lineage_id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    gold_table          text        NOT NULL,
    gold_col            text        NOT NULL,
    silver_col          text        NOT NULL,
    mapping_version_id  bigint      NOT NULL REFERENCES ops.mapping_versions (mapping_version_id),
    source              text        NOT NULL,
    source_col          text        NOT NULL,
    created_at          timestamptz NOT NULL DEFAULT now(),
    UNIQUE (mapping_version_id, gold_table, gold_col, source_col)
);
CREATE INDEX lineage_source_col_idx ON ops.lineage (source, source_col);

-- Every LLM call, from day one (CLAUDE.md coding conventions). The prompt is stored as a hash only.
CREATE TABLE ops.llm_calls (
    llm_call_id    bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    batch_id       uuid        REFERENCES ops.batches (batch_id),
    purpose        text        NOT NULL,
    provider       text        NOT NULL,
    model          text        NOT NULL,
    prompt_hash    char(64)    NOT NULL,
    input_tokens   integer     CHECK (input_tokens >= 0),
    output_tokens  integer     CHECK (output_tokens >= 0),
    latency_ms     integer     CHECK (latency_ms >= 0),
    cost_usd       numeric(12, 6) CHECK (cost_usd >= 0),
    outcome        text        NOT NULL CHECK (outcome IN ('ok', 'invalid_output', 'fallback', 'error')),
    error          text,
    called_at      timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX llm_calls_called_at_idx ON ops.llm_calls (called_at DESC);
