-- Exception workflow (docs/plan.md A6): assign, resolve with a kind that publish can act on, and an append-only
-- event trail. Resolving records a decision; it never edits bronze or silver (CLAUDE.md principles 1 and 3).
--   accept              the row is correct as sent: publish it
--   exclude             never publish this row
--   fixed_at_source     a corrected extract will replace it; keep it out until then
--   no_longer_violated  system: re-validation found the rule passes now

ALTER TABLE ops.exceptions
    ADD COLUMN resolution_kind text CHECK (resolution_kind IN ('accept', 'exclude', 'fixed_at_source',
                                                               'no_longer_violated')),
    ADD COLUMN assigned_by     text,
    ADD COLUMN assigned_at     timestamptz;

UPDATE ops.exceptions SET resolution_kind = 'no_longer_violated'
WHERE status = 'resolved' AND resolved_by = 'system:revalidation';
UPDATE ops.exceptions SET resolution_kind = 'accept' WHERE status = 'resolved' AND resolution_kind IS NULL;

ALTER TABLE ops.exceptions
    ADD CONSTRAINT exceptions_resolved_has_kind CHECK (status <> 'resolved' OR resolution_kind IS NOT NULL),
    ADD CONSTRAINT exceptions_open_has_no_kind CHECK (status = 'resolved' OR resolution_kind IS NULL);

CREATE TABLE ops.exception_events (
    event_id      bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    exception_id  bigint      NOT NULL REFERENCES ops.exceptions (exception_id),
    action        text        NOT NULL CHECK (action IN ('assigned', 'resolved', 'auto_resolved')),
    actor         text        NOT NULL,
    details       jsonb       NOT NULL DEFAULT '{}'::jsonb,
    created_at    timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX exception_events_exception_idx ON ops.exception_events (exception_id, created_at);
