-- When was a promotion actually the price that applied (§8.1.3)?
-- Gaps-and-islands over the RESOLVED grid. A promotion that lost 30 days to
-- MAIL did not drive those 30 days of sellout: islands split it, and
-- back-to-back same-price chunks merge. PARTITION BY chosen deliberately.
CREATE OR REPLACE VIEW rpt.v_promotion_effective_periods AS
WITH d AS (
    SELECT cj_item, rsp_unit, pro_type, pro_theme, promo_price_per_piece,
           sales_date,
           sales_date - (row_number() OVER (
               PARTITION BY cj_item, rsp_unit, pro_type, pro_theme,
                            promo_price_per_piece
               ORDER BY sales_date))::int AS island
    FROM   rpt.v_fact_progrid_bestprice
)
SELECT cj_item, rsp_unit, pro_type, pro_theme, promo_price_per_piece,
       min(sales_date) AS effective_from,
       max(sales_date) AS effective_to,
       count(*)        AS days_effective
FROM   d
GROUP BY cj_item, rsp_unit, pro_type, pro_theme, promo_price_per_piece, island
ORDER BY cj_item, effective_from;
