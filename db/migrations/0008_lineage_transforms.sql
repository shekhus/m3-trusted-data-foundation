-- Column lineage carries the silver table and the transforms applied on the way (e.g. kg_to_lb), so a
-- reviewer can see not just where a gold value came from but what was done to it (docs/decisions.md D-020).

ALTER TABLE ops.lineage
    ADD COLUMN silver_table text  NOT NULL DEFAULT 'silver.order_lines',
    ADD COLUMN transforms   jsonb NOT NULL DEFAULT '[]'::jsonb;

CREATE INDEX lineage_gold_col_idx ON ops.lineage (gold_table, gold_col);
