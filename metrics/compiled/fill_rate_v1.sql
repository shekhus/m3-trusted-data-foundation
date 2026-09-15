-- Compiled from metrics/fill_rate.v1.yaml by metrics/compiler.py. Do not edit; edit the YAML.
-- fill_rate v1 (current), owner supply_chain_analytics, effective 2025-03-01.
DROP VIEW IF EXISTS gold.fill_rate_v1_lines;
CREATE VIEW gold.fill_rate_v1_lines AS
WITH step_0 AS (
    SELECT * FROM gold.fact_delivery
)
SELECT order_no, line_no, issue_date AS metric_date, plant, customer_no, item_no, product_group, order_type, invoiced_qty, ordered_qty, invoiced_weight_lb, ordered_weight_lb
FROM step_0;
COMMENT ON VIEW gold.fill_rate_v1_lines IS 'Compiled from metrics/fill_rate.v1.yaml: fill_rate v1 (current). Do not edit.';
