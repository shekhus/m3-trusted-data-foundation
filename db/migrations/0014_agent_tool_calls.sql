-- Exception resolution agent (docs/addendum1.md A13): every tool call is logged with latency and outcome.
-- run_id groups the calls of one agent run; the runs table and checkpoints arrive with the graph (task 21).

CREATE TABLE ops.tool_calls (
    tool_call_id   bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    run_id         uuid,
    exception_id   bigint      REFERENCES ops.exceptions (exception_id),
    tool           text        NOT NULL CHECK (tool IN ('search_prior_resolutions', 'search_knowledge_base',
                                                        'lookup_master', 'inspect_source_rows',
                                                        'check_other_sources')),
    arguments      jsonb       NOT NULL,
    outcome        text        NOT NULL CHECK (outcome IN ('ok', 'empty', 'error')),
    result_count   integer     CHECK (result_count >= 0),
    result         jsonb,      -- what the agent saw, so every evidence_ref can be traced to a call
    latency_ms     integer     NOT NULL CHECK (latency_ms >= 0),
    error          text,
    called_at      timestamptz NOT NULL DEFAULT now(),
    CHECK ((outcome = 'error') = (error IS NOT NULL))
);
CREATE INDEX tool_calls_run_idx ON ops.tool_calls (run_id, tool_call_id);
CREATE INDEX tool_calls_exception_idx ON ops.tool_calls (exception_id);
