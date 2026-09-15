-- Reconciliation (docs/plan.md A10). A run compares, per period, the two current consumer views of each governed
-- metric (must agree exactly) and the legacy view (the "before"), and explains each BI tool's gap against the
-- governed definition by named causes identified from the tool's own export.

CREATE TABLE ops.reconciliation_runs (
    run_id       bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    actor        text        NOT NULL,
    status       text        NOT NULL CHECK (status IN ('green', 'red', 'no_data')),
    summary      jsonb       NOT NULL DEFAULT '{}'::jsonb,
    created_at   timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE ops.reconciliation (
    run_id        bigint           NOT NULL REFERENCES ops.reconciliation_runs (run_id),
    metric        text             NOT NULL,
    period        text             NOT NULL,  -- YYYY-MM
    grain_key     text             NOT NULL,  -- e.g. "plant=PLT-01"
    consumer_a    text             NOT NULL,
    value_a       double precision,
    consumer_b    text             NOT NULL,
    value_b       double precision,
    delta         double precision,           -- a − b; 0 when both views read the same definition
    legacy        text,
    value_legacy  double precision,
    legacy_delta  double precision,           -- legacy − a: the gap the old definition would report
    status        text             NOT NULL CHECK (status IN ('green', 'red')),
    PRIMARY KEY (run_id, metric, period, grain_key)
);

CREATE TABLE ops.reconciliation_tools (
    run_id           bigint           NOT NULL REFERENCES ops.reconciliation_runs (run_id),
    tool             text             NOT NULL,
    period           text             NOT NULL,
    lines_governed   integer          NOT NULL,
    lines_tool       integer          NOT NULL,
    governed         double precision,
    measured         double precision,
    gap              double precision,
    gap_flagged      boolean          NOT NULL,
    contributions    jsonb            NOT NULL,  -- cause → single-switch delta from governed (not additive)
    dominant_cause   text,
    PRIMARY KEY (run_id, tool, period)
);
