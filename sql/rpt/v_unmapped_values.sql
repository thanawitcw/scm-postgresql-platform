-- Enum values the business hasn't classified yet (§4.3).
-- Unmapped values load (never defaulted to OTHER) and surface here as DQ WARN.
-- Canonical set ruled 2026-10-06: MAIL, 7-DAYS, EDLP, OTHER, WED, NPD, กระชาก.
-- Anything else (incl. 7-DAYS_*, 3-DAYS, 14-DAYS) is unmapped by definition.
CREATE OR REPLACE VIEW rpt.v_unmapped_values AS
SELECT f.pro_type AS enum_value, 'pro_type' AS value_set, count(*) AS rows_seen,
       max(f.uploaded_at) AS last_seen_at
FROM   core.fact_running_progrid f
WHERE  f.pro_type NOT IN
       ('MAIL', '7-DAYS', 'EDLP', 'OTHER', 'WED', 'NPD', 'กระชาก')
GROUP BY f.pro_type;
