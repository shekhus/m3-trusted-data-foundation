-- Compiled from metrics/otif.v2.yaml by metrics/compiler.py. Do not edit; edit the YAML.
-- otif v2 (legacy), owner supply_chain_analytics, effective 2024-01-01.
DROP VIEW IF EXISTS gold.otif_v2_lines CASCADE;
CREATE VIEW gold.otif_v2_lines AS
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
    SELECT step_2.*, COALESCE(in_full_count, FALSE) AS in_full
    FROM step_2
),
step_4 AS (
    SELECT step_3.*, COALESCE((on_time AND in_full), FALSE) AS otif
    FROM step_3
)
SELECT order_no, line_no, issue_date AS metric_date, plant, customer_no, item_no, product_group, order_type, on_time, in_full_count, in_full, otif
FROM step_4;
COMMENT ON VIEW gold.otif_v2_lines IS 'Compiled from metrics/otif.v2.yaml: otif v2 (legacy). Do not edit.';
