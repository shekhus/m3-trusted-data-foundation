-- Compiled from metrics/consumers/consumers.yaml by metrics/compiler.py. Do not edit.;
DROP VIEW IF EXISTS gold.consumer_ops_otif_monthly_plant;
CREATE VIEW gold.consumer_ops_otif_monthly_plant AS
SELECT to_char(metric_date, 'YYYY-MM') AS month, plant, COUNT(*) AS lines, SUM(otif::int) AS otif_lines, SUM(on_time::int) AS on_time_lines, SUM(in_full::int) AS in_full_lines, ((SUM(otif::int))::double precision / NULLIF(COUNT(*), 0)) AS otif_rate, ((SUM(on_time::int))::double precision / NULLIF(COUNT(*), 0)) AS on_time_rate, ((SUM(in_full::int))::double precision / NULLIF(COUNT(*), 0)) AS in_full_rate
FROM gold.otif_v3_lines
GROUP BY 1, 2;
COMMENT ON VIEW gold.consumer_ops_otif_monthly_plant IS 'Consumer ops_otif_monthly_plant (plant operations monthly review): otif v3. Compiled; do not edit.';
DROP VIEW IF EXISTS gold.consumer_sales_otif_daily_customer;
CREATE VIEW gold.consumer_sales_otif_daily_customer AS
SELECT metric_date AS day, plant, customer_no, COUNT(*) AS lines, SUM(otif::int) AS otif_lines, SUM(on_time::int) AS on_time_lines, SUM(in_full::int) AS in_full_lines, ((SUM(otif::int))::double precision / NULLIF(COUNT(*), 0)) AS otif_rate, ((SUM(on_time::int))::double precision / NULLIF(COUNT(*), 0)) AS on_time_rate, ((SUM(in_full::int))::double precision / NULLIF(COUNT(*), 0)) AS in_full_rate
FROM gold.otif_v3_lines
GROUP BY 1, 2, 3;
COMMENT ON VIEW gold.consumer_sales_otif_daily_customer IS 'Consumer sales_otif_daily_customer (customer service daily lane report): otif v3. Compiled; do not edit.';
DROP VIEW IF EXISTS gold.consumer_legacy_otif_monthly_plant;
CREATE VIEW gold.consumer_legacy_otif_monthly_plant AS
SELECT to_char(metric_date, 'YYYY-MM') AS month, plant, COUNT(*) AS lines, SUM(otif::int) AS otif_lines, SUM(on_time::int) AS on_time_lines, SUM(in_full::int) AS in_full_lines, ((SUM(otif::int))::double precision / NULLIF(COUNT(*), 0)) AS otif_rate, ((SUM(on_time::int))::double precision / NULLIF(COUNT(*), 0)) AS on_time_rate, ((SUM(in_full::int))::double precision / NULLIF(COUNT(*), 0)) AS in_full_rate
FROM gold.otif_v2_lines
GROUP BY 1, 2;
COMMENT ON VIEW gold.consumer_legacy_otif_monthly_plant IS 'Consumer legacy_otif_monthly_plant (legacy report suite (count basis), kept for comparison): otif v2. Compiled; do not edit.';
DROP VIEW IF EXISTS gold.consumer_ops_fill_rate_monthly_plant;
CREATE VIEW gold.consumer_ops_fill_rate_monthly_plant AS
SELECT to_char(metric_date, 'YYYY-MM') AS month, plant, COUNT(*) AS lines, ((SUM(invoiced_qty))::double precision / NULLIF(SUM(ordered_qty), 0)) AS fill_rate_count, ((SUM(invoiced_weight_lb))::double precision / NULLIF(SUM(ordered_weight_lb), 0)) AS fill_rate_weight, SUM(ordered_weight_lb) AS ordered_weight_lb, SUM(invoiced_weight_lb) AS invoiced_weight_lb, SUM(ordered_qty) AS ordered_qty, SUM(invoiced_qty) AS invoiced_qty
FROM gold.fill_rate_v1_lines
GROUP BY 1, 2;
COMMENT ON VIEW gold.consumer_ops_fill_rate_monthly_plant IS 'Consumer ops_fill_rate_monthly_plant (plant operations monthly review): fill_rate v1. Compiled; do not edit.';
DROP VIEW IF EXISTS gold.consumer_sales_fill_rate_daily_customer;
CREATE VIEW gold.consumer_sales_fill_rate_daily_customer AS
SELECT metric_date AS day, plant, customer_no, COUNT(*) AS lines, ((SUM(invoiced_qty))::double precision / NULLIF(SUM(ordered_qty), 0)) AS fill_rate_count, ((SUM(invoiced_weight_lb))::double precision / NULLIF(SUM(ordered_weight_lb), 0)) AS fill_rate_weight, SUM(ordered_weight_lb) AS ordered_weight_lb, SUM(invoiced_weight_lb) AS invoiced_weight_lb, SUM(ordered_qty) AS ordered_qty, SUM(invoiced_qty) AS invoiced_qty
FROM gold.fill_rate_v1_lines
GROUP BY 1, 2, 3;
COMMENT ON VIEW gold.consumer_sales_fill_rate_daily_customer IS 'Consumer sales_fill_rate_daily_customer (customer service daily lane report): fill_rate v1. Compiled; do not edit.';
