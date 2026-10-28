-- Reusable assertion: a contracted table must never load empty.
-- Usage inside a migration or DQ helper: \ir sql/dq/assert_not_empty.sql
DO $$
BEGIN
    -- placeholder: real assertions live in etl/quality/checks.py and run
    -- INSIDE the load transaction (§7). This file documents the SQL form.
    RAISE NOTICE 'assert_not_empty: enforced via etl.quality.checks.not_empty';
END
$$;
