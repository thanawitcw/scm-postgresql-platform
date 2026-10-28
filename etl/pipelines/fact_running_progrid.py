"""fact_running_progrid — faithful expansion, not a resolution (§8.1).

Cancellation policy A: only never_ran rows (cancelled AND valid_from on/after
the upload date) are excluded. Cancelled-mid-flight rows keep their FULL
window — the archive + change log preserve both states, so history stays
reproducible. Zero-window rows (valid_to < valid_from) yield no days and are
counted as WARN, not failed.
"""

from __future__ import annotations

from datetime import datetime

import pandas as pd
from sqlalchemy import text

from etl.db.load import copy_df, truncate
from etl.pipelines.runner import warn
from etl.quality import checks as c
from etl.runlog import record_dq

STG = "stg.stg_progrid_raw"
FACT = "core.fact_running_progrid"
STG_COLS = ["source_key", "cj_item",
            "rsp", "rsp_unit", "pro_type", "pro_theme", "pro_price_rsp_gap",
            "promo_price_per_piece", "valid_from", "valid_to", "status_raw",
            "status_norm", "is_cancelled",
            "uploaded_at", "source_file"]

COMPARE_COLS = ["pro_type", "pro_theme", "promo_price_per_piece", "rsp"]


def run(engine, run_id: str, frames: dict[str, pd.DataFrame],
        uploaded_at: datetime, manifest: dict) -> dict:
    rows_in = sum(len(f) for f in frames.values())
    with engine.begin() as conn:  # tx1: staging (every row incl. cancelled)
        truncate(conn, STG)
        for key, df in frames.items():
            staged = df.copy()
            staged["uploaded_at"] = uploaded_at
            staged["source_file"] = manifest["files"][key]["file_name"]
            copy_df(conn, STG, staged, STG_COLS)
        c.not_empty(conn, STG)
    with engine.begin() as conn:  # tx2: ONE transaction (§8.1 steps 4-9)
        conn.execute(text(
            "CREATE TEMPORARY TABLE prev_progrid ON COMMIT DROP "
            f"AS SELECT * FROM {FACT}"))
        prev_n = conn.execute(text("SELECT count(*) FROM prev_progrid")).scalar()
        prev_uploaded = conn.execute(
            text("SELECT max(uploaded_at) FROM prev_progrid")).scalar()

        conn.execute(text(f"DELETE FROM {FACT}"))
        conn.execute(
            text(f"""
                WITH src AS (
                    SELECT s.*,
                           ROW_NUMBER() OVER (
                                ORDER BY source_key, cj_item, rsp_unit,
                                         valid_from, pro_type,
                                         promo_price_per_piece, valid_to
                            ) AS promo_row_id
                    FROM   {STG} s
                    WHERE  NOT (s.is_cancelled
                                 AND s.valid_from >= CAST(:ua AS date))
                       -- Owner ruling 2026-10-06: promos starting before the
                       -- dim_date floor are DROPPED whole (not clipped).
                       -- stg keeps every row; the fact starts at dim_date.
                       AND s.valid_from >= (SELECT min(date) FROM core.dim_date)
                )
                INSERT INTO {FACT}
                    (sales_date, cj_item, rsp_unit, rsp, pro_type, pro_theme,
                     promo_price_per_piece, valid_from, valid_to, slot_key,
                     promo_row_id, is_cancelled, uploaded_at, source_file)
                SELECT d::date, src.cj_item, src.rsp_unit, src.rsp,
                       src.pro_type, src.pro_theme, src.promo_price_per_piece,
                       src.valid_from, src.valid_to,
                       src.cj_item || '|' || COALESCE(src.rsp_unit, '')
                           || '|' || src.valid_from::text,
                       src.promo_row_id, src.is_cancelled,
                       src.uploaded_at, src.source_file
                FROM   src,
                        generate_series(src.valid_from, src.valid_to,
                                        interval '1 day') AS d
                JOIN   core.dim_date dd ON dd.date = d::date
                WHERE  src.valid_to >= src.valid_from
                -- src rows already start at/after the dim floor, so the JOIN
                -- only guards the far tail past dim max. Pre-floor promos are
                -- dropped whole above (owner ruling), counted + WARNed below.
                """),
            {"ua": uploaded_at},
        )
        c.not_empty(conn, FACT)
        c.unique_on(conn, FACT, ["sales_date", "cj_item", "rsp_unit", "promo_row_id"])
        c.measure_range(conn, FACT, "promo_price_per_piece", low=0)
        # Promo above RSP is a source property (77 promos, ≤2.4x, measured
        # 2026-10-06), not a scale error — WARN and surface, never block.
        above_rsp = conn.execute(text(
            f"SELECT count(*) FROM {FACT} "
            f"WHERE promo_price_per_piece IS NOT NULL AND rsp IS NOT NULL "
            f"AND promo_price_per_piece > rsp")).scalar()
        clipped = conn.execute(text(
            f"SELECT COALESCE(sum(src.valid_to - src.valid_from + 1), 0) "
            f"FROM {STG} src "
            f"WHERE NOT (src.is_cancelled AND src.valid_from >= CAST(:ua AS date)) "
            f"AND src.valid_to >= src.valid_from"),
            {"ua": uploaded_at},
        ).scalar()
        rows_out = conn.execute(text(f"SELECT count(*) FROM {FACT}")).scalar()
        clipped = clipped - rows_out  # days past dim_date max, if any
        pre_floor = conn.execute(text(
            f"SELECT count(*) FROM {STG} "
            f"WHERE valid_from < (SELECT min(date) FROM core.dim_date)")).scalar()
        zero_window = conn.execute(text(
            f"SELECT count(*) FROM {STG} WHERE valid_to < valid_from")).scalar()

        if prev_n == 0:
            changed = 0  # first run: everything is ADDED by definition; skip the diff
        else:
            changed = conn.execute(
                text("""
                WITH p AS (
                    SELECT p.*,
                           ROW_NUMBER() OVER (
                               PARTITION BY slot_key, sales_date
                               ORDER BY pro_type, pro_theme,
                                        promo_price_per_piece, rsp, promo_row_id
                           ) AS rn
                    FROM   prev_progrid p
                ),
                n AS (
                    SELECT n.*,
                           ROW_NUMBER() OVER (
                               PARTITION BY slot_key, sales_date
                               ORDER BY pro_type, pro_theme,
                                        promo_price_per_piece, rsp, promo_row_id
                           ) AS rn
                    FROM   core.fact_running_progrid n
                ),
                diff AS (
                    SELECT COALESCE(n.slot_key, p.slot_key) AS slot_key,
                           COALESCE(n.sales_date, p.sales_date) AS sales_date,
                           COALESCE(n.rn, p.rn) AS rn,
                           COALESCE(n.cj_item, p.cj_item) AS cj_item,
                           COALESCE(n.rsp_unit, p.rsp_unit) AS rsp_unit,
                           p IS NULL AS is_added,
                           n IS NULL AS is_removed,
                           p.pro_type IS DISTINCT FROM n.pro_type
                            OR p.pro_theme IS DISTINCT FROM n.pro_theme
                            OR p.promo_price_per_piece IS DISTINCT FROM
                               n.promo_price_per_piece
                            OR p.rsp IS DISTINCT FROM n.rsp AS is_modified,
                           ARRAY_REMOVE(ARRAY[
                               CASE WHEN p.pro_type IS DISTINCT FROM n.pro_type
                                    THEN 'pro_type' END,
                               CASE WHEN p.pro_theme IS DISTINCT FROM n.pro_theme
                                    THEN 'pro_theme' END,
                               CASE WHEN p.promo_price_per_piece IS DISTINCT FROM
                                         n.promo_price_per_piece
                                    THEN 'promo_price_per_piece' END,
                               CASE WHEN p.rsp IS DISTINCT FROM n.rsp
                                    THEN 'rsp' END], NULL) AS changed_fields,
                            to_jsonb(p) AS old_state, to_jsonb(n) AS new_state,
                            p.pro_type AS old_pro_type, n.pro_type AS new_pro_type,
                            p.pro_theme AS old_pro_theme, n.pro_theme AS new_pro_theme,
                            p.promo_price_per_piece AS old_promo_price_per_piece,
                            n.promo_price_per_piece AS new_promo_price_per_piece,
                            p.rsp AS old_rsp, n.rsp AS new_rsp
                    FROM   n
                    FULL OUTER JOIN p
                      ON p.slot_key = n.slot_key AND p.sales_date = n.sales_date
                     AND p.rn = n.rn
                )
                INSERT INTO ops.promotion_change_log
                    (uploaded_at, previous_uploaded_at, slot_key, cj_item,
                     rsp_unit, sales_date, rn, change_type, changed_fields,
                     old_state, new_state,
                     old_pro_type, new_pro_type,
                     old_pro_theme, new_pro_theme,
                     old_promo_price_per_piece, new_promo_price_per_piece,
                     old_rsp, new_rsp)
                SELECT CAST(:ua AS timestamptz), CAST(:prev_ua AS timestamptz),
                       slot_key, cj_item, rsp_unit, sales_date, rn,
                       CASE WHEN is_added THEN 'ADDED'
                            WHEN is_removed THEN 'REMOVED'
                            ELSE 'MODIFIED' END,
                       changed_fields, old_state, new_state,
                       old_pro_type, new_pro_type,
                       old_pro_theme, new_pro_theme,
                       old_promo_price_per_piece, new_promo_price_per_piece,
                       old_rsp, new_rsp
                FROM   diff
                WHERE  is_added OR is_removed OR is_modified
                RETURNING 1
                """),
                {"ua": uploaded_at, "prev_ua": prev_uploaded},
            ).rowcount
            if changed > 0.3 * rows_out:
                raise AssertionError(
                    f"[change_volume_band:{FACT}] observed={changed} changed days "
                    f"of {rows_out} (>30% of the grid in one upload) expected=stable")
    record_dq(engine, run_id, "zero_window:valid_to<valid_from", "WARN",
              zero_window == 0, observed=f"{zero_window} source rows",
              expected="0 — generate_series yields no days; kept in stg")
    record_dq(engine, run_id, "measure_range:promo_price<=rsp", "WARN",
              above_rsp == 0, observed=f"{above_rsp} daily rows above rsp",
              expected="0 — promo above list price is source data, not a blocker")
    record_dq(engine, run_id, "coverage_clip:pre-dim window tails", "WARN",
              True, observed=f"{clipped} days past dim_date max",
              expected="0 normally; source windows kept whole in stg")
    record_dq(engine, run_id, "dropped:valid_from-before-dim-floor", "WARN",
              pre_floor == 0, observed=f"{pre_floor} source promos dropped whole",
              expected="0 normally — owner ruling 2026-10-06: pre-floor promos "
                       "drop, dim starts 2025-01-01, stg keeps every row")
    warn(engine, run_id,
         lambda conn: c.referential(conn, FACT, "cj_item", "core.dim_item", "cj_item"),
         "referential:fact_running_progrid.cj_item->dim_item")
    return {"rows_in": rows_in, "rows_out": rows_out}
