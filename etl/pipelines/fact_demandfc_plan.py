"""fact_sales_plan (S10) — append mode, key-exact replace (DC-stock pattern).

Raw mirror (no business transform): every file row lands once. Each load
APPENDs: DELETE exactly the forecast_month_keys carried by the frames
(IN stg) + INSERT, in ONE transaction (R1) — never DELETE all, so prior
snapshots survive. Re-upload of the same snapshot is idempotent by key.
The 8 monthly snapshots overlap in sales_plan_date but the grain carries
forecast_month_key, so windows never collide. DQ inside the tx; orphans
WARN per D15 (loaded regardless).
"""

from __future__ import annotations

import pandas as pd
from sqlalchemy import text

from etl.db.load import copy_df, truncate
from etl.pipelines.runner import warn
from etl.quality import checks as c
from etl.runlog import record_dq

STG = "stg.stg_demandfc_history"
FACT = "core.fact_demandfc_history"
STG_COLS = ["forecast_month_key", "fc_lead_time_months", "sales_plan_date",
            "demand_owner", "cj_item", "sales_channel", "pro_type",
            "pro_theme", "promo_price_per_piece", "forecast_cartons",
            "forecast_qty", "uploaded_at", "source_file"]
GRAIN = ["forecast_month_key", "sales_plan_date", "cj_item",
         "sales_channel", "pro_type", "pro_theme"]


def run(engine, run_id: str,
        frames: list[tuple[pd.DataFrame, object, str]],
        scope: str = "append") -> dict:
    """frames: [(df, uploaded_at, source_file)] — one entry per snapshot file.

    Append mode (DC-stock pattern): DELETE exactly the forecast_month_keys
    being loaded (IN stg) + INSERT, in ONE transaction (R1). Re-upload of
    the same snapshot deletes + reinserts only its own keys — other
    snapshots are never touched. ``scope`` is accepted for backward
    compat and must be ``"append"`` or ``"snapshot"``; ``"full"`` (DELETE
    all) is rejected.
    """
    if scope not in ("append", "snapshot"):
        raise AssertionError(
            f"[scope:{scope}] full replace retired — sales_plan is append mode; "
            f"re-upload is key-exact delete + insert")
    rows_in = sum(len(df) for df, _, _ in frames)
    with engine.begin() as conn:  # tx1: staging (disposable, truncate-then-load)
        truncate(conn, STG)
        for df, uploaded_at, source_file in frames:
            staged = df.copy()
            staged["uploaded_at"] = uploaded_at
            staged["source_file"] = source_file
            copy_df(conn, STG, staged, STG_COLS)
        c.not_empty(conn, STG)
    with engine.begin() as conn:  # tx2: key-exact replace, ONE tx (R1)
        # Validate the re-upload key first — a NULL/empty key would match
        # nothing in the DELETE below and silently duplicate on INSERT.
        null_keys = conn.execute(
            text(f"SELECT count(*) FROM {STG} WHERE forecast_month_key IS NULL")
        ).scalar()
        if null_keys:
            raise AssertionError(
                f"[scope:append] {null_keys} stg rows with NULL "
                f"forecast_month_key — fix at source, then reload.")
        # DELETE exactly the keys being loaded (IN stg) — never DELETE all,
        # never a min/max span.
        conn.execute(
            text(f"DELETE FROM {FACT} "
                 f"WHERE forecast_month_key IN "
                 f"(SELECT DISTINCT forecast_month_key FROM {STG})"))
        # Same grain claimed by 2+ rows (measured risk: overlapping
        # snapshots re-issued under a new filename). Fail NAMING the files,
        # not with a bare UniqueViolation — DC-stock pattern.
        clash = conn.execute(text(
            f"""
            SELECT {', '.join(GRAIN)},
                   array_agg(DISTINCT s.source_file) AS files
            FROM   {STG} s
            GROUP BY {', '.join(GRAIN)} HAVING count(*) > 1
            LIMIT 5
            """)).all()
        if clash:
            detail = "; ".join(
                f"{' | '.join(str(v) for v in r[:-1])} in {r[-1]}" for r in clash)
            raise AssertionError(
                f"[conflict:sales_plan same grain, multiple rows] {detail} — "
                f"source files disagree; fix at source, then reload.")
        conn.execute(text(
            f"""
            INSERT INTO {FACT}
                ({', '.join(STG_COLS[:-2])}, loaded_at, source_file)
            SELECT {', '.join(STG_COLS[:-2])}, now(), source_file
            FROM   {STG}
            """))
        # DQ INSIDE the tx: any ERROR RAISEs and rolls back (R1).
        c.not_empty(conn, FACT)
        c.unique_on(conn, FACT, GRAIN)
        rows_out = conn.execute(text(
            f"SELECT count(*) FROM {FACT} "
            f"WHERE forecast_month_key IN "
            f"(SELECT DISTINCT forecast_month_key FROM {STG})")).scalar()
    orphans = None
    with engine.connect() as conn:
        orphans = conn.execute(text(
            f"SELECT count(*) FROM {FACT} f "
            f"WHERE f.forecast_month_key IN "
            f"(SELECT DISTINCT forecast_month_key FROM {STG}) "
            f"AND f.cj_item IS NOT NULL AND NOT EXISTS "
            f"(SELECT 1 FROM core.dim_item d WHERE d.cj_item = f.cj_item)"
        )).scalar()
    record_dq(engine, run_id, "referential:sales_plan.cj_item->dim_item",
              "WARN", orphans == 0, observed=f"{orphans} orphan rows",
              expected="0 — orphan rows load regardless, never dropped")
    warn(engine, run_id,
         lambda conn: c.row_count_within_history(conn, FACT, "TRUE", rows_out, 30.0),
         "row_count_within_history:core.fact_demandfc_history")
    return {"rows_in": rows_in, "rows_out": rows_out}
