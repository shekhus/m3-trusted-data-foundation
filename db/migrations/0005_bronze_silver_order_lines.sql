-- Bronze: every source row exactly as received (text values keyed by the file's own header), one table for all
-- sources so a header change never needs DDL. Silver: canonical, typed order lines derived from bronze through
-- the header's confirmed mapping. Originals stay in bronze; silver keeps the link (batch_id, source_row).
-- Validation rules (week 3) read silver; parse failures are kept per row, never dropped.

ALTER TABLE ops.batches ADD COLUMN header_hash char(64);

CREATE TABLE bronze.raw_order_lines (
    batch_id    uuid    NOT NULL REFERENCES ops.batches (batch_id),
    source_row  integer NOT NULL CHECK (source_row >= 1),  -- 1 = first data row after the header
    record      jsonb   NOT NULL,
    PRIMARY KEY (batch_id, source_row)
);

CREATE TABLE silver.order_lines (
    batch_id                 uuid             NOT NULL,
    source_row               integer          NOT NULL,
    source                   text             NOT NULL,
    mapping_version_id       bigint           NOT NULL REFERENCES ops.mapping_versions (mapping_version_id),
    order_no                 text,
    line_no                  integer,
    customer_no              text,
    item_no                  text,
    plant                    text,
    order_date               date,
    requested_date           date,
    confirmed_delivery_date  date,
    issue_date               date,
    ordered_qty              double precision,
    invoiced_qty             double precision,
    ordered_weight_lb        double precision,
    invoiced_weight_lb       double precision,
    uom                      text,
    lot_no                   text,
    parse_errors             jsonb            NOT NULL DEFAULT '{}'::jsonb,  -- canonical column → raw value
    PRIMARY KEY (batch_id, source_row),
    FOREIGN KEY (batch_id, source_row) REFERENCES bronze.raw_order_lines (batch_id, source_row)
);
CREATE INDEX order_lines_key_idx ON silver.order_lines (source, order_no, line_no);
