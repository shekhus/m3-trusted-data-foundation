-- Gold publish (docs/decisions.md D-023). Gold nullability now follows the canonical schema: fields a mapping
-- must supply stay NOT NULL; optional ones (weights, invoiced qty, UOM, lot) and attributes the plant extracts
-- do not carry (order type, customer PO) may be NULL. Every published row names its batch and source row.

ALTER TABLE gold.fact_delivery
    ALTER COLUMN product_group      DROP NOT NULL,  -- from the item master; unknown for an accepted unknown item
    ALTER COLUMN catch_weight_flag  DROP NOT NULL,
    ALTER COLUMN invoiced_qty       DROP NOT NULL,
    ALTER COLUMN ordered_weight_lb  DROP NOT NULL,
    ALTER COLUMN invoiced_weight_lb DROP NOT NULL,  -- PLT-02 sends ~1% of lines without a weight
    ALTER COLUMN uom                DROP NOT NULL,
    ALTER COLUMN order_type         DROP NOT NULL,  -- not in any plant extract
    ADD COLUMN batch_id     uuid REFERENCES ops.batches (batch_id),
    ADD COLUMN source       text,
    ADD COLUMN source_row   integer,
    ADD COLUMN published_at timestamptz;
CREATE INDEX fact_delivery_batch_idx ON gold.fact_delivery (batch_id);

CREATE TABLE ops.publishes (
    publish_id     bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    batch_id       uuid        NOT NULL REFERENCES ops.batches (batch_id),
    source         text        NOT NULL,
    status         text        NOT NULL CHECK (status IN ('published', 'refused', 'withdrawn')),
    rows_published integer     NOT NULL DEFAULT 0 CHECK (rows_published >= 0),
    rows_held      jsonb       NOT NULL DEFAULT '{}'::jsonb,  -- reason → row count
    reasons        jsonb       NOT NULL DEFAULT '[]'::jsonb,  -- why a batch was refused or withdrawn
    actor          text        NOT NULL,
    created_at     timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX publishes_batch_idx ON ops.publishes (batch_id, created_at DESC);
