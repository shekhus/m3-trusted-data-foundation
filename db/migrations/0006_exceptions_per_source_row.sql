-- Exceptions point at the exact silver/bronze row. (order_no, line_no) is not unique inside a batch — that is
-- what V008 finds — so the queue's identity is (batch, rule, source_row); row_key stays for people and evals.
-- `details` holds the evidence a reviewer (or the week-6 agent) needs: offending values, master lookups.

ALTER TABLE ops.exceptions
    ADD COLUMN source      text,
    ADD COLUMN source_row  integer,
    ADD COLUMN details     jsonb NOT NULL DEFAULT '{}'::jsonb,
    DROP CONSTRAINT exceptions_batch_id_rule_id_row_key_key;

UPDATE ops.exceptions e SET source = b.source FROM ops.batches b WHERE b.batch_id = e.batch_id;

ALTER TABLE ops.exceptions
    ALTER COLUMN source SET NOT NULL,
    ALTER COLUMN source_row SET NOT NULL,
    ADD CONSTRAINT exceptions_one_per_batch_rule_row UNIQUE (batch_id, rule_id, source_row),
    ADD CONSTRAINT exceptions_row_exists FOREIGN KEY (batch_id, source_row)
        REFERENCES bronze.raw_order_lines (batch_id, source_row);

CREATE INDEX exceptions_source_status_idx ON ops.exceptions (source, status, severity);
