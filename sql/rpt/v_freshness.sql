-- Freshness contract: one row per loaded table (§9, §10.1).
-- Two-sided per table: coverage_lag_days (today - business through-date)
-- and publish_lag_days (today - last load). is_stale uses a per-source
-- threshold because cadences differ (daily sellout vs annual GR files).
-- data_through meaning varies by table and is stated in through_what:
-- sales/stock = last business date present; progrid = last snapshot date
-- (its sales_dates run into the future by design); plan = last load date
-- (its sales_plan_dates are forecasts, always future).
CREATE OR REPLACE VIEW rpt.v_freshness AS
WITH last_load AS (
    SELECT target_table, max(finished_at) AS last_loaded_at
    FROM   ops.etl_run
    WHERE  status = 'SUCCESS'
    GROUP BY target_table
)
SELECT 'core.fact_daily_sales' AS table_name,
       max(sales_date)::text AS data_through,
       'max sales_date present' AS through_what,
       count(*) AS row_count,
       (SELECT last_loaded_at FROM last_load
         WHERE target_table = 'core.fact_daily_sales') AS last_loaded_at,
       current_date - max(sales_date) AS coverage_lag_days,
       (current_date - max(sales_date)) > 3 AS is_stale
FROM   core.fact_daily_sales
UNION ALL
SELECT 'core.fact_running_progrid',
       max(uploaded_at)::date::text,
       'last progrid snapshot date (sales_dates are future windows)',
       count(*),
       (SELECT last_loaded_at FROM last_load
         WHERE target_table = 'core.fact_running_progrid'),
       current_date - max(uploaded_at)::date,
       (current_date - max(uploaded_at)::date) > 14
FROM   core.fact_running_progrid
UNION ALL
SELECT 'core.fact_goods_receipt',
       max(report_date)::text,
       'max report_date present',
       count(*),
       (SELECT last_loaded_at FROM last_load
         WHERE target_table = 'core.fact_goods_receipt'),
       current_date - max(report_date),
       (current_date - max(report_date)) > 14
FROM   core.fact_goods_receipt
UNION ALL
SELECT 'core.fact_weekly_stock',
       max(stock_date)::text,
       'last Monday snapshot present',
       count(*),
       (SELECT last_loaded_at FROM last_load
         WHERE target_table = 'core.fact_weekly_stock'),
       current_date - max(stock_date),
       (current_date - max(stock_date)) > 9
FROM   core.fact_weekly_stock
UNION ALL
SELECT 'core.fact_daily_dc_stock',
       max(stock_date)::text,
       'max stock_date present',
       count(*),
       (SELECT last_loaded_at FROM last_load
         WHERE target_table = 'core.fact_daily_dc_stock'),
       current_date - max(stock_date),
       (current_date - max(stock_date)) > 3
FROM   core.fact_daily_dc_stock
UNION ALL
SELECT 'core.fact_demandfc_history',
       max(loaded_at)::date::text,
       'last plan load (sales_plan_dates are future forecasts)',
       count(*),
       (SELECT last_loaded_at FROM last_load
         WHERE target_table = 'core.fact_demandfc_history'),
       (current_date - max(loaded_at)::date),
       (current_date - max(loaded_at)::date) > 45
FROM   core.fact_demandfc_history
UNION ALL
SELECT 'core.fact_oos_status',
       max(stock_date)::text,
       'last snapshot present',
       count(*),
       (SELECT last_loaded_at FROM last_load
         WHERE target_table = 'core.fact_oos_status'),
       current_date - max(stock_date),
       (current_date - max(stock_date)) > 9
FROM   core.fact_oos_status
UNION ALL
SELECT 'core.dim_item',
       NULL AS data_through,
       'no business date; full rebuild from master' AS through_what,
       count(*),
       (SELECT last_loaded_at FROM last_load
         WHERE target_table = 'core.dim_item') AS last_loaded_at,
       NULL AS coverage_lag_days,
       (current_date - (SELECT last_loaded_at FROM last_load
         WHERE target_table = 'core.dim_item')::date) > 14 AS is_stale
FROM   core.dim_item;
