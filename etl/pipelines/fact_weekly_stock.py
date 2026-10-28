"""fact_weekly_stock (S11) — APPEND-ONLY per week, wide grain.

One row per (stock_date x cj_item) with Total/DC1/DC2/DC4/DC5 blocks as
columns (12 measures each, file-faithful incl. DOH*/PercOOS). TierCompany
rides as an attribute. Files before 2026-05-18 carry no DC5 block: stg keeps
NULL (file-faithful), the fact coalesces to 0 (user ruling).

Append rule: weeks already in the fact are NEVER rewritten on the default
path — the load fails loudly naming the week. Rewriting one week is a
deliberate `--file` reload (scope="week"), not something a scheduled run
can do by accident.
The UNIQUE(stock_date, cj_item) constraint backstops this structurally.
"""

from __future__ import annotations

import pandas as pd
from sqlalchemy import text

from etl.db.load import copy_df, truncate
from etl.pipelines.runner import warn
from etl.quality import checks as c

STG = "stg.stg_weekly_stock"
FACT = "core.fact_weekly_stock"

BLOCKS = ["total", "dc1", "dc2", "dc4", "dc5"]
MEASURES = ["total_store", "avg_sales_qty_90d", "avg_cogs_90d",
            "store_stock_qty", "store_stock_value", "store_doh",
            "dc_stock_qty", "dc_stock_value", "dc_doh",
            "active_store", "oos_store", "store_oos_pct"]
MEASURE_COLS = [f"{b}_{m}" for b in BLOCKS for m in MEASURES]
STG_COLS = ["stock_date", "cj_item", "tier_company"] + MEASURE_COLS + [
    "uploaded_at", "source_file"]


def run(engine, run_id: str,
        frames: list[tuple[pd.DataFrame, object, str]],
        scope: str = "append") -> dict:
    """frames: [(df, uploaded_at, source_file)] — one entry per weekly file.

    scope="append" (default): new weeks only; an already-loaded week fails
    loudly (scheduled path can never rewrite history). scope="week":
    DELETE the frames' weeks + INSERT (deliberate single-file re-upload).
    """
    rows_in = sum(len(df) for df, _, _ in frames)
    weeks = sorted({df["stock_date"].iloc[0] for df, _, _ in frames})
    with engine.begin() as conn:  # tx1: staging (disposable, truncate-then-load)
        truncate(conn, STG)
        for df, uploaded_at, source_file in frames:
            staged = df.copy()
            for col in MEASURE_COLS:  # absent blocks (DC5 pre-2026-05-18)
                if col not in staged.columns:
                    staged[col] = None
            staged["uploaded_at"] = uploaded_at
            staged["source_file"] = source_file
            copy_df(conn, STG, staged, STG_COLS)
        c.not_empty(conn, STG)
    coalesce = ", ".join(
        f"COALESCE({m}, 0) AS {m}" for m in MEASURE_COLS)
    with engine.begin() as conn:  # tx2: append or week-replace, ONE tx (R1)
        if scope == "week":
            conn.execute(
                text(f"DELETE FROM {FACT} "
                     f"WHERE stock_date IN (SELECT DISTINCT stock_date FROM {STG})"))
        else:
            present = {r[0] for r in conn.execute(text(
                "SELECT DISTINCT stock_date FROM core.fact_weekly_stock "
                "WHERE stock_date BETWEEN :w0 AND :w1"),
                {"w0": weeks[0], "w1": weeks[-1]}).all()}
            clash = sorted(set(weeks) & present)
            if clash:
                raise AssertionError(
                    f"[append:fact_weekly_stock] weeks already loaded: "
                    f"{[str(w) for w in clash]} — refusing to rewrite history. "
                    f"Reloading a week is a deliberate decision, not a scheduled append.")
        conn.execute(text(
            f"""
            INSERT INTO {FACT}
                (stock_date, cj_item, tier_company, {', '.join(MEASURE_COLS)},
                 loaded_at, source_file)
            SELECT stock_date, cj_item, tier_company, {coalesce},
                   now(), source_file
            FROM   {STG}
            """))
        # DQ INSIDE the tx: any ERROR RAISEs and rolls back (R1).
        c.not_empty(conn, FACT,
                    f"stock_date BETWEEN DATE '{weeks[0]}' AND DATE '{weeks[-1]}'")
        c.unique_on(conn, FACT, ["stock_date", "cj_item"])
        rows_out = conn.execute(text(
            f"SELECT count(*) FROM {FACT} "
            f"WHERE stock_date BETWEEN :w0 AND :w1"),
            {"w0": weeks[0], "w1": weeks[-1]}).scalar()
    from etl.runlog import record_dq

    dupes = sum(df.attrs.get("exact_dupes_dropped", 0) for df, _, _ in frames)
    warn(engine, run_id,
         lambda conn: c.referential(conn, FACT, "cj_item", "core.dim_item", "cj_item"),
         "referential:fact_weekly_stock.cj_item->dim_item")
    record_dq(engine, run_id, "exact_dupes_dropped", "WARN",
              dupes == 0, observed=f"{dupes} byte-identical rows",
              expected="0 — dropped at read, carry zero information")
    return {"rows_in": rows_in, "rows_out": rows_out}
