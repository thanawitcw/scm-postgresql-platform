"""fact_daily_dc_stock (S7) — date-window replace, one transaction (R1).

stg takes every row (file-faithful, incl. non-200 Materials). The fact
loads 200%-prefixed Materials only + WARN quarantine count (user ruling),
resolving Plant -> dc_key via dim_dc.plant_code. Unmapped Plant is a DQ
ERROR (master data is stable; a new code is an upstream change). Negative
stock_qty/value clamp to 0 in the fact (user ruling 2026-10-06; stg keeps
raw). '0'-date and exact-dupe drops are counted at read and WARNed here.
"""

from __future__ import annotations

import pandas as pd
from sqlalchemy import text

from etl.db.load import copy_df, truncate
from etl.pipelines.runner import warn
from etl.quality import checks as c
from etl.runlog import record_dq

STG = "stg.stg_dc_daily"
FACT = "core.fact_daily_dc_stock"
STG_COLS = ["stock_date", "plant", "material", "stock_qty", "stock_value",
            "uploaded_at", "source_file"]


def run(engine, run_id: str,
        frames: list[tuple[pd.DataFrame, object, str]]) -> dict:
    """frames: [(df, uploaded_at, source_file)] — one entry per daily file."""
    rows_in = sum(len(df) for df, _, _ in frames)
    dates = sorted({d for df, _, _ in frames for d in df["stock_date"].unique()})
    sentinel = sum(df.attrs.get("sentinel_dates_dropped", 0) for df, _, _ in frames)
    dupes = sum(df.attrs.get("exact_dupes_dropped", 0) for df, _, _ in frames)
    with engine.begin() as conn:  # tx1: staging (disposable, truncate-then-load)
        truncate(conn, STG)
        for df, uploaded_at, source_file in frames:
            staged = df.copy()
            staged["uploaded_at"] = uploaded_at
            staged["source_file"] = source_file
            copy_df(conn, STG, staged, STG_COLS)
        c.not_empty(conn, STG)
    with engine.begin() as conn:  # tx2: date-exact replace, ONE tx (R1)
        # DELETE exactly the dates being loaded (IN stg) — never a min/max
        # span, which would wipe already-loaded dates the frames don't carry.
        conn.execute(
            text(f"DELETE FROM {FACT} "
                 f"WHERE stock_date IN (SELECT DISTINCT stock_date FROM {STG})"))
        # Same grain claimed by 2+ files with differing rows (measured:
        # DC_End23-07-2025.xlsx carries 24.07 data). Fail NAMING the files,
        # not with a bare UniqueViolation.
        clash = conn.execute(text(
            f"""
            SELECT s.stock_date, s.plant, s.material,
                   array_agg(DISTINCT s.source_file) AS files
            FROM   {STG} s
            GROUP BY 1, 2, 3 HAVING count(*) > 1
            LIMIT 5
            """)).all()
        if clash:
            detail = "; ".join(
                f"{r[0]} {r[1]} {r[2]} in {r[3]}" for r in clash)
            raise AssertionError(
                f"[conflict:dc_daily same grain, multiple files] {detail} — "
                f"source files disagree; fix at source, then reload.")
        conn.execute(text(
            f"""
            INSERT INTO {FACT}
                (stock_date, dc_key, cj_item, stock_qty, stock_value)
            -- User ruling 2026-10-06: negatives clamp to 0 in the fact.
            -- (Reverses the backorder-signal preservation; stg keeps raw.)
            SELECT s.stock_date, d.dc_key, s.material,
                   GREATEST(s.stock_qty, 0), GREATEST(s.stock_value, 0)
            FROM   {STG} s
            JOIN   core.dim_dc d ON d.plant_code = s.plant
            WHERE  s.material LIKE '200%'
            """))
        # DQ INSIDE the tx: any ERROR RAISEs and rolls back (R1).
        c.not_empty(conn, FACT,
                    f"stock_date BETWEEN DATE '{dates[0]}' AND DATE '{dates[-1]}'")
        c.unique_on(conn, FACT, ["stock_date", "dc_key", "cj_item"])
        unmapped = conn.execute(text(
            f"SELECT count(DISTINCT s.plant) FROM {STG} s "
            f"WHERE NOT EXISTS (SELECT 1 FROM core.dim_dc d "
            f"WHERE d.plant_code = s.plant)")).scalar()
        if unmapped:
            bad = conn.execute(text(
                f"SELECT DISTINCT s.plant FROM {STG} s "
                f"WHERE NOT EXISTS (SELECT 1 FROM core.dim_dc d "
                f"WHERE d.plant_code = s.plant)")).scalars().all()
            raise AssertionError(
                f"[referential:dc_daily.plant->dim_dc] unmapped Plant codes "
                f"{bad} — new plant is an upstream change")
        quarantined = conn.execute(text(
            f"SELECT count(*) FROM {STG} WHERE material NOT LIKE '200%'")).scalar()
        rows_out = conn.execute(text(
            f"SELECT count(*) FROM {FACT} "
            f"WHERE stock_date BETWEEN :d0 AND :d1"),
            {"d0": dates[0], "d1": dates[-1]}).scalar()
    record_dq(engine, run_id, "quarantine:non-200-material", "WARN",
              quarantined == 0,
              observed=f"{quarantined} rows excluded from fact (kept in stg)",
              expected="0 — CJ items are 200%-prefixed")
    record_dq(engine, run_id, "sentinel:zero-dates", "WARN",
              sentinel == 0, observed=f"{sentinel} '0'-date rows dropped at read",
              expected="0")
    record_dq(engine, run_id, "exact_dupes_dropped", "WARN",
              dupes == 0, observed=f"{dupes} byte-identical rows",
              expected="0 — dropped at read, carry zero information")
    warn(engine, run_id,
         lambda conn: c.referential(conn, FACT, "cj_item", "core.dim_item", "cj_item"),
         "referential:fact_daily_dc_stock.cj_item->dim_item")
    return {"rows_in": rows_in, "rows_out": rows_out}
