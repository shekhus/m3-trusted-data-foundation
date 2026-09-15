-- Compiled from metrics/otif_case_tolerance.v1.yaml by metrics/compiler.py. Do not edit; edit the YAML.
-- otif_case_tolerance v1 (reference), owner supply_chain_analytics, effective 2026-09-15.
DROP VIEW IF EXISTS gold.otif_case_tolerance_v1_lines CASCADE;
CREATE VIEW gold.otif_case_tolerance_v1_lines AS
WITH step_0 AS (
    SELECT * FROM gold.fact_delivery
),
step_1 AS (
    SELECT step_0.*, COALESCE((issue_date <= confirmed_delivery_date), FALSE) AS on_time
    FROM step_0
),
step_2 AS (
    SELECT step_1.*, COALESCE((invoiced_qty >= (ordered_qty * (1 - 0.02))), FALSE) AS in_full_count_tolerance
    FROM step_1
),
step_3 AS (
    SELECT step_2.*, COALESCE(in_full_count_tolerance, FALSE) AS in_full
    FROM step_2
),
step_4 AS (
    SELECT step_3.*, COALESCE((on_time AND in_full), FALSE) AS otif
    FROM step_3
)
SELECT order_no, line_no, issue_date AS metric_date, plant, customer_no, item_no, product_group, order_type, on_time, in_full_count_tolerance, in_full, otif
FROM step_4;
COMMENT ON VIEW gold.otif_case_tolerance_v1_lines IS 'Compiled from metrics/otif_case_tolerance.v1.yaml: otif_case_tolerance v1 (reference). Do not edit.';
