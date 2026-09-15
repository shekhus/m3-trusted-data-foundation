-- Compiled from metrics/otif.v3.yaml by metrics/compiler.py. Do not edit; edit the YAML.
-- otif v3 (current), owner supply_chain_analytics, effective 2026-09-01.
DROP VIEW IF EXISTS gold.otif_v3_lines;
CREATE VIEW gold.otif_v3_lines AS
WITH step_0 AS (
    SELECT * FROM gold.fact_delivery
),
step_1 AS (
    SELECT step_0.*, COALESCE((issue_date <= confirmed_delivery_date), FALSE) AS on_time
    FROM step_0
),
step_2 AS (
    SELECT step_1.*, COALESCE((invoiced_qty >= ordered_qty), FALSE) AS in_full_count
    FROM step_1
),
step_3 AS (
    SELECT step_2.*, COALESCE((invoiced_weight_lb >= (ordered_weight_lb * (1 - 0.02))), FALSE) AS in_full_weight
    FROM step_2
),
step_4 AS (
    SELECT step_3.*, COALESCE((CASE WHEN catch_weight_flag THEN in_full_weight ELSE in_full_count END), FALSE) AS in_full
    FROM step_3
),
step_5 AS (
    SELECT step_4.*, COALESCE((on_time AND in_full), FALSE) AS otif
    FROM step_4
)
SELECT order_no, line_no, issue_date AS metric_date, plant, customer_no, item_no, product_group, order_type, on_time, in_full_count, in_full_weight, in_full, otif
FROM step_5;
COMMENT ON VIEW gold.otif_v3_lines IS 'Compiled from metrics/otif.v3.yaml: otif v3 (current). Do not edit.';
