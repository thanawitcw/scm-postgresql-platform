"""fact_goods_receipt (S5) — full replace, annual source files.

Both yearly files load into stg, then ONE transaction (R1): DELETE all +
INSERT the grain-aggregated replacement + DQ, one COMMIT. The 158 measured
dup rows on (report_date x po_no x cj_item x dc) collapse by SUM (additive
measures, totals preserved) with MIN(received_date). Unmapped GR_DC is a
DQ ERROR: the DC set is master data, a new value is an upstream change.
"""

from __future__ import annotations

from datetime import date

import pandas as pd
from sqlalchemy import text

from etl.db.load import copy_df, truncate
from etl.pipelines.runner import warn
from etl.quality import checks as c

STG = "stg.stg_goods_receipt"
FACT = "core.fact_goods_receipt"
PIPELINE = "fact_goods_receipt"
STG_COLS = ["report_date", "received_date", "po_no", "cj_item", "gr_dc",
            "gr_qty_piece", "gr_value_in_vat", "uploaded_at", "source_file"]


def run(engine, run_id: str, frames: list[tuple[pd.DataFrame, object, str]]) -> dict:
    """frames: [(df, uploaded_at, source_file)] — one entry per annual file."""
    rows_in = sum(len(df) for df, _, _ in frames)
    with engine.begin() as conn:  # tx1: staging (both years, disposable)
        truncate(conn, STG)
        for df, uploaded_at, source_file in frames:
            staged = df.copy()
            staged["uploaded_at"] = uploaded_at
            staged["source_file"] = source_file
            copy_df(conn, STG, staged, STG_COLS)
        c.not_empty(conn, STG)
    with engine.begin() as conn:  # tx2: year-scoped replace, ONE tx (R1)
        # DELETE only the years carried by frames (a single-file load must
        # never wipe other years). History loads carry all years = full.
        years = sorted({d.year for df, _, _ in frames
                        for d in df["report_date"].unique().tolist()})
        if not years:
            raise AssertionError("[scope:fact_goods_receipt] no report dates in frames")
        conn.execute(
            text(f"DELETE FROM {FACT} "
                 f"WHERE date_part('year', report_date) = ANY(CAST(:yrs AS int[]))"),
            {"yrs": years})
        conn.execute(text(
            f"""
            INSERT INTO {FACT}
                (report_date, received_date, po_no, cj_item, dc_key,
                 gr_qty_piece, gr_value_in_vat)
            SELECT s.report_date, min(s.received_date), s.po_no, s.cj_item,
                   d.dc_key,
                   sum(s.gr_qty_piece), sum(s.gr_value_in_vat)
            FROM   {STG} s
            JOIN   core.dim_dc d ON d.dc_name = s.gr_dc
            GROUP BY s.report_date, s.po_no, s.cj_item, d.dc_key
            """))
        # DQ INSIDE the tx: any ERROR RAISEs and rolls back (R1).
        c.not_empty(conn, FACT)
        c.unique_on(conn, FACT, ["report_date", "po_no", "cj_item", "dc_key"])
        unmapped = conn.execute(text(
            f"SELECT count(*) FROM {STG} s "
            f"WHERE NOT EXISTS (SELECT 1 FROM core.dim_dc d "
            f"WHERE d.dc_name = s.gr_dc)")).scalar()
        if unmapped:
            raise AssertionError(
                f"[referential:goods_receipt.gr_dc->dim_dc] observed={unmapped} "
                f"unmapped DC rows expected=0 (new DC value is an upstream change)")
        rows_out = conn.execute(text(f"SELECT count(*) FROM {FACT}")).scalar()
        stg_qty = conn.execute(
            text(f"SELECT sum(gr_qty_piece) FROM {STG}")).scalar()
        fact_qty = conn.execute(
            text(f"SELECT sum(gr_qty_piece) FROM {FACT} "
                 f"WHERE date_part('year', report_date) = ANY(CAST(:yrs AS int[]))"),
            {"yrs": years}).scalar()
        if stg_qty != fact_qty:
            raise AssertionError(
                f"[reconciliation:gr_qty] stg={stg_qty} fact={fact_qty} "
                f"expected=equal (SUM aggregation must preserve totals)")
    warn(engine, run_id,
         lambda conn: c.referential(conn, FACT, "cj_item", "core.dim_item", "cj_item"),
         "referential:fact_goods_receipt.cj_item->dim_item")
    return {"rows_in": rows_in, "rows_out": rows_out}
