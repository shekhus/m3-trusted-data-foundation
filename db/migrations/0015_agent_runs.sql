-- Exception resolution agent runs (docs/addendum1.md A13, D-030).
-- One row per run (run_id = the LangGraph thread id). LangGraph's own checkpoint tables are created by
-- PostgresSaver.setup() in the agent_checkpoints schema, kept apart from the tables these migrations own.

CREATE SCHEMA IF NOT EXISTS agent_checkpoints;

CREATE TABLE ops.agent_runs (
    run_id          uuid        PRIMARY KEY,
    exception_id    bigint      NOT NULL REFERENCES ops.exceptions (exception_id),
    started_by      text        NOT NULL,
    status          text        NOT NULL DEFAULT 'running'
                    CHECK (status IN ('running', 'awaiting_approval', 'completed', 'failed')),
    iterations      integer     NOT NULL DEFAULT 0 CHECK (iterations BETWEEN 0 AND 6),
    outcome         text        CHECK (outcome IN ('auto_fixable', 'needs_master_data', 'source_defect', 'escalate')),
    confidence      numeric(4, 3) CHECK (confidence BETWEEN 0 AND 1),
    not_covered     boolean,
    fallback        boolean     NOT NULL DEFAULT false,  -- no valid classification after one retry: escalated
    resolution      jsonb,      -- the agent's Resolution contract, as validated
    gate            jsonb,      -- the policy gate's disposition and reasons
    decision        text        CHECK (decision IN ('approve', 'reject')),
    decided_by      text,
    decision_note   text,
    decided_at      timestamptz,
    error           text,
    started_at      timestamptz NOT NULL DEFAULT now(),
    finished_at     timestamptz,
    CHECK (status <> 'completed' OR (decision IS NOT NULL AND decided_by IS NOT NULL)),
    CHECK (status NOT IN ('awaiting_approval', 'completed') OR (outcome IS NOT NULL AND resolution IS NOT NULL))
);
CREATE INDEX agent_runs_exception_idx ON ops.agent_runs (exception_id, started_at DESC);
CREATE INDEX agent_runs_status_idx ON ops.agent_runs (status);

ALTER TABLE ops.tool_calls ADD CONSTRAINT tool_calls_run_fk FOREIGN KEY (run_id) REFERENCES ops.agent_runs (run_id)
    NOT VALID;  -- calls made outside a run (tests, ad-hoc) keep a NULL run_id

ALTER TABLE ops.exception_events DROP CONSTRAINT exception_events_action_check;
ALTER TABLE ops.exception_events ADD CONSTRAINT exception_events_action_check
    CHECK (action IN ('assigned', 'resolved', 'auto_resolved', 'agent_classified', 'agent_rejected'));
