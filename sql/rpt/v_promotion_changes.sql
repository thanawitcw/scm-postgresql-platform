-- Promotion-level reading of the daily change log (§8.1.2).
-- Aggregated at QUERY time: at load time only the new side exists (M10-adjacent).
-- Flat old_/new_ columns are Power BI-ready; old_state/new_state jsonb kept as forensic backup.
-- Flat promo-window pairs (old_valid_from/old_valid_to vs new_valid_from/new_valid_to)
-- are extracted from the jsonb states: they give the OLD vs NEW promo date range so BI
-- can check change_summary's DATES EXTENDED/CUT against the real windows. dates_verdict
-- states the comparison outright. valid_from is constant per slot_key (it IS the slot
-- key's 3rd part), so the comparison hinges on valid_to only.
-- Two levels: day_agg (one row per promo x upload, all aggregates) -> outer SELECT that
-- composes change_summary and dates_verdict from the aggregates without repeating them.
-- DROP first: CREATE OR REPLACE cannot rename/reorder columns (added flat cols before old_state).
-- change_summary is ASCII-only (no em-dash: Windows-874 mojibake) and compositional:
--   ADDED | PROMO CANCELLED | comma list of PRICE UP/DOWN, TYPE/THEME CHANGED, DATES EXTENDED/CUT.
-- RSP CHANGED dropped from the summary (owner ruling 2026-10-21); the flat
-- old_rsp/new_rsp columns are kept so the underlying data stays available.
DROP VIEW IF EXISTS rpt.v_promotion_changes;
CREATE OR REPLACE VIEW rpt.v_promotion_changes AS
WITH day_agg AS (
    SELECT uploaded_at, previous_uploaded_at, slot_key, cj_item,
           count(*)                          AS days_affected,
           min(sales_date)                   AS first_day,
           max(sales_date)                   AS last_day,
           bool_or(change_type = 'MODIFIED') AS repriced,
           bool_or(change_type = 'REMOVED')  AS window_cut,
           bool_or(change_type = 'ADDED')    AS days_added, 
           bool_or('promo_price_per_piece' = ANY(changed_fields)) AS price_changed,
           bool_or('pro_type'  = ANY(changed_fields))             AS type_changed,
           bool_or('pro_theme' = ANY(changed_fields))             AS theme_changed,
           max(old_pro_type) FILTER (WHERE old_pro_type IS NOT NULL) AS old_pro_type,
           max(new_pro_type) FILTER (WHERE new_pro_type IS NOT NULL) AS new_pro_type,
           max(old_pro_theme) FILTER (WHERE old_pro_theme IS NOT NULL) AS old_pro_theme,
           max(new_pro_theme) FILTER (WHERE new_pro_theme IS NOT NULL) AS new_pro_theme,
           max(old_promo_price_per_piece) FILTER (WHERE old_promo_price_per_piece IS NOT NULL) AS old_promo_price_per_piece,
           max(new_promo_price_per_piece) FILTER (WHERE new_promo_price_per_piece IS NOT NULL) AS new_promo_price_per_piece,
           max(old_rsp) FILTER (WHERE old_rsp IS NOT NULL) AS old_rsp,
           max(new_rsp) FILTER (WHERE new_rsp IS NOT NULL) AS new_rsp,
           max((old_state->>'valid_from')::date) FILTER (WHERE old_state IS NOT NULL) AS old_valid_from,
           max((old_state->>'valid_to')::date)   FILTER (WHERE old_state IS NOT NULL) AS old_valid_to,
           max((new_state->>'valid_from')::date) FILTER (WHERE new_state IS NOT NULL) AS new_valid_from,
           max((new_state->>'valid_to')::date)   FILTER (WHERE new_state IS NOT NULL) AS new_valid_to,
           CAST(max(old_state::text) FILTER (WHERE old_state IS NOT NULL) AS jsonb) AS old_state,
           CAST(max(new_state::text) FILTER (WHERE new_state IS NOT NULL) AS jsonb) AS new_state
    FROM   ops.promotion_change_log
    GROUP  BY uploaded_at, previous_uploaded_at, slot_key, cj_item
)
SELECT uploaded_at, previous_uploaded_at, slot_key, cj_item,
       days_affected, first_day, last_day,
       repriced, window_cut,
       CASE
         WHEN days_added AND NOT repriced AND NOT window_cut THEN 'ADDED'
         WHEN window_cut AND NOT repriced AND NOT days_added THEN 'PROMO CANCELLED'
         ELSE COALESCE(array_to_string(ARRAY_REMOVE(ARRAY[
              CASE WHEN price_changed THEN
                   CASE WHEN new_promo_price_per_piece > old_promo_price_per_piece
                        THEN 'PRICE UP'
                        WHEN new_promo_price_per_piece < old_promo_price_per_piece
                        THEN 'PRICE DOWN'
                        ELSE 'PRICE CHANGED' END
              END,
              CASE WHEN type_changed   THEN 'TYPE CHANGED'  END,
              CASE WHEN theme_changed  THEN 'THEME CHANGED' END,
              CASE WHEN days_added     THEN 'DATES EXTENDED' END,
              CASE WHEN window_cut     THEN 'DATES CUT'      END
            ], NULL), ', '), 'REVISED')
       END                               AS change_summary,
       old_pro_type, new_pro_type,
       old_pro_theme, new_pro_theme,
       old_promo_price_per_piece, new_promo_price_per_piece,
       old_rsp, new_rsp,
       old_valid_from, old_valid_to,
       new_valid_from, new_valid_to,
       old_valid_to - old_valid_from + 1 AS old_window_days,
       new_valid_to - new_valid_from + 1 AS new_window_days,
       CASE
         WHEN old_valid_to IS NULL AND new_valid_to IS NULL THEN NULL
         WHEN old_valid_to IS NULL THEN 'NEW PROMO'
         WHEN new_valid_to IS NULL THEN 'PROMO CANCELLED'
         WHEN new_valid_to > old_valid_to THEN 'DATES EXTENDED'
         WHEN new_valid_to < old_valid_to THEN 'DATES CUT'
         ELSE 'DATES UNCHANGED'
       END                               AS dates_verdict,
       old_state, new_state
FROM   day_agg;
