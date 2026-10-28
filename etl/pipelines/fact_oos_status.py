"""fact_oos_status (S12) — raw wide mirror, date-exact replace, ONE tx (R1).

Every source column lands untouched. DC5 numerics absent pre-2026 coalesce
to 0 (user ruling: 0 for numerics, NULL for dates/text — stg keeps NULL,
file-faithful). Same-date claimed by 2+ files fails naming them (S7 lesson).
DQ inside the tx; orphans WARN per D15 (loaded regardless).
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
from sqlalchemy import text

from etl.db.load import copy_df, truncate
from etl.pipelines.runner import warn
from etl.quality import checks as c
from etl.runlog import record_dq
from etl.sources.xlsx_oos_status import BLOCKS as _BLOCKS
from etl.sources.xlsx_oos_status import SUFFIX_MAP as _SUFFIX_MAP

STG = "stg.stg_oos_status"
FACT = "core.fact_oos_status"

DESCRIPTOR_COLS = ["stock_date", "division_shm", "owner_scm", "supplier_name",
                   "shm_item", "cj_item", "cj_description", "lead_time_days",
                   "cj_status", "shm_status", "pc_cartons", "first_so_date",
                   "helper_column_for_total"]

NUM_DST = ["active_assort", "store_oos", "store_stock_qty", "doh_store",
           "avg_sale_qty_90d", "dc_stock_qty", "doh_dc", "po_pending"]
_blocks = [b.lower() for b in _BLOCKS]
NUM_COLS = [f"{b}_{m}" for b in _blocks for m in NUM_DST]
DATE_COLS = [f"{b}_first_del_date" for b in _blocks]
TEXT_COLS = [f"{b}_{m}" for b in _blocks
             for m in ["stock_status", "oos_by", "oos_reason", "owner_note"]]
STG_COLS = (DESCRIPTOR_COLS + NUM_COLS + DATE_COLS + TEXT_COLS
            + ["uploaded_at", "source_file"])
GRAIN = ["stock_date", "cj_item", "shm_item"]


def run(engine, run_id: str,
        frames: list[tuple[pd.DataFrame, object, str]]) -> dict:
    """frames: [(df, uploaded_at, source_file)] — one entry per snapshot file."""
    rows_in = sum(len(df) for df, _, _ in frames)
    dates = sorted({d for df, _, _ in frames for d in df["stock_date"].unique()})
    with engine.begin() as conn:  # tx1: staging (disposable, truncate-then-load)
        truncate(conn, STG)
        for df, uploaded_at, source_file in frames:
            staged = df.copy()
            for col in NUM_COLS + DATE_COLS + TEXT_COLS:  # absent DC5 block
                if col not in staged.columns:
                    staged[col] = None
            staged["uploaded_at"] = uploaded_at
            staged["source_file"] = source_file
            copy_df(conn, STG, staged, STG_COLS)
        c.not_empty(conn, STG)
    num_zero = ", ".join(f"COALESCE({m}, 0) AS {m}" for m in NUM_COLS)
    with engine.begin() as conn:  # tx2: date-exact replace, ONE tx (R1)
        conn.execute(
            text(f"DELETE FROM {FACT} "
                 f"WHERE stock_date IN (SELECT DISTINCT stock_date FROM {STG})"))
        clash = conn.execute(text(
            f"""
            SELECT s.stock_date, s.cj_item, s.shm_item,
                   array_agg(DISTINCT s.source_file) AS files
            FROM   {STG} s
            GROUP BY 1, 2, 3 HAVING count(*) > 1
            LIMIT 5
            """)).all()
        if clash:
            detail = "; ".join(
                f"{r[0]} {r[1]} {r[2]} in {[Path(f).name for f in r[3]]}"
                for r in clash)
            raise AssertionError(
                f"[conflict:oos_status same grain, multiple files] {detail} — "
                f"source files disagree; fix at source, then reload.")
        conn.execute(text(
            f"""
            INSERT INTO {FACT}
                ({', '.join(DESCRIPTOR_COLS)}, {', '.join(NUM_COLS)},
                 {', '.join(DATE_COLS)}, {', '.join(TEXT_COLS)},
                 loaded_at, source_file)
            SELECT {', '.join(DESCRIPTOR_COLS)}, {num_zero},
                   {', '.join(DATE_COLS)}, {', '.join(TEXT_COLS)},
                   now(), source_file
            FROM   {STG}
            """))
        # DQ INSIDE the tx: any ERROR RAISEs and rolls back (R1).
        c.not_empty(conn, FACT)
        c.unique_on(conn, FACT, GRAIN)
        rows_out = conn.execute(text(f"SELECT count(*) FROM {FACT}")).scalar()
    with engine.connect() as conn:
        orphans = conn.execute(text(
            f"SELECT count(*) FROM {FACT} f "
            f"WHERE NOT EXISTS (SELECT 1 FROM core.dim_item d "
            f"WHERE d.cj_item = f.cj_item)")).scalar()
    record_dq(engine, run_id, "referential:oos_status.cj_item->dim_item",
              "WARN", orphans == 0, observed=f"{orphans} orphan rows",
              expected="0 — orphan rows load regardless, never dropped")
    return {"rows_in": rows_in, "rows_out": rows_out}
