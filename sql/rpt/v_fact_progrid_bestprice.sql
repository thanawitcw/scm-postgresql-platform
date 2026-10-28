-- Resolved promotion price per day (Rule M11: a view, not a table).
-- Tie-break (corrected, §5.1.1): LOWEST price first; MAIL preferred only on
-- EQUAL price; then lowest rsp. Consumers never need to know this rule.
CREATE OR REPLACE VIEW rpt.v_fact_progrid_bestprice AS
SELECT DISTINCT ON (sales_date, cj_item, rsp_unit)
       sales_date, cj_item, rsp_unit, rsp, pro_type, pro_theme,
       promo_price_per_piece, valid_from, valid_to, is_cancelled
FROM   core.fact_running_progrid
WHERE is_cancelled = FALSE
ORDER BY sales_date, cj_item, rsp_unit,
         promo_price_per_piece ASC,
         CASE pro_type WHEN 'MAIL' THEN 1
                       WHEN 'EDLP' THEN 2
                       ELSE 3 END ASC,
         rsp ASC;
