-- Governed delivery fact at order-line grain (docs/plan.md §1.3 customer_orders + deliveries).
-- Filled by publish (week 4). Metric views in metrics/compiled/ are built on it.
-- Deliberately holds NO metric flags (on_time, in_full, otif): those are defined once, in metrics/*.yaml.
-- product_group and catch_weight_flag are denormalised from the item master because metric rules use them.

CREATE TABLE gold.fact_delivery (
    order_no                 text             NOT NULL,
    line_no                  integer          NOT NULL,
    customer_no              text             NOT NULL,
    item_no                  text             NOT NULL,
    plant                    text             NOT NULL,
    product_group            text             NOT NULL,
    catch_weight_flag        boolean          NOT NULL,
    order_date               date             NOT NULL,
    requested_date           date             NOT NULL,
    confirmed_delivery_date  date             NOT NULL,
    issue_date               date             NOT NULL,
    ordered_qty              double precision NOT NULL,
    invoiced_qty             double precision NOT NULL,
    ordered_weight_lb        double precision NOT NULL,
    invoiced_weight_lb       double precision NOT NULL,
    uom                      text             NOT NULL,
    lot_no                   text,
    production_date          date,
    expiry_date              date,
    customer_po              text,
    order_type               text             NOT NULL,
    PRIMARY KEY (order_no, line_no)
);
CREATE INDEX fact_delivery_issue_plant_idx ON gold.fact_delivery (issue_date, plant);
