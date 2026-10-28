"""fact_daily_sales — Pattern A incremental by (channel, month) window.

One transaction: DELETE the month+channel window + INSERT the replacement +
DQ, one COMMIT (R1). Joins use the natural cj_item (dim_item is a 2-column
map since 0009). Orphan sellout items (no dim_item row) still load — every
row preserved — with a DQ WARN naming them.
"""

from __future__ import annotations

import calendar
from datetime import date

import pandas as pd
from sqlalchemy import text

from etl.db.load import copy_df, truncate
from etl.pipelines.runner import warn
from etl.quality import checks as c

STG = "stg.stg_csv_daily_sales"
FACT = "core.fact_daily_sales"
STG_COLS = ["sales_date", "cj_item", "product_name", "sales_channel", "pro_type",
            "pro_theme", "pro_price_in_vat", "so_qty_piece", "so_amt_in_vat",
            "is_main", "uploaded_at", "source_file"]


def month_window(year: int, month: int) -> tuple[date, date]:
    last = calendar.monthrange(year, month)[1]
    return date(year, month, 1), date(year, month, last)


def run(engine, run_id: str, df: pd.DataFrame, channel: str,
        year: int, month: int, uploaded_at, source_file: str) -> dict:
    rows_in = len(df)
    d_from, d_to = month_window(year, month)
    with engine.begin() as conn:  # tx1: staging
        truncate(conn, STG)
        staged = df.copy()
        staged["uploaded_at"] = uploaded_at
        staged["source_file"] = source_file
        copy_df(conn, STG, staged, STG_COLS)
        c.not_empty(conn, STG)
    with engine.begin() as conn:  # tx2: Pattern A — ONE transaction
        conn.execute(
            text(f"""
                DELETE FROM {FACT} f
                USING core.dim_channel ch
                WHERE f.channel_key = ch.channel_key
                  AND ch.sales_channel = :ch
                  AND f.sales_date BETWEEN :d_from AND :d_to
                """),
            {"ch": channel, "d_from": d_from, "d_to": d_to},
        )
        conn.execute(
            text(f"""
                INSERT INTO {FACT}
                    (sales_date, cj_item, channel_key, pro_type, pro_theme,
                     pro_price_in_vat, so_qty_piece, so_amt_in_vat,
                     is_main)
                SELECT s.sales_date, s.cj_item, ch.channel_key,
                       s.pro_type, s.pro_theme, s.pro_price_in_vat,
                       s.so_qty_piece, s.so_amt_in_vat, s.is_main
                FROM   {STG} s
                JOIN   core.dim_channel ch
                       ON ch.sales_channel = s.sales_channel
                """)
        )
        # DQ INSIDE the tx: any ERROR RAISEs and rolls back (R1).
        c.not_empty(conn, FACT,
                    f"sales_date BETWEEN DATE '{d_from}' AND DATE '{d_to}'")
        orphans = conn.execute(
            text(f"""
                SELECT count(*) FROM {STG} s
                WHERE NOT EXISTS (SELECT 1 FROM core.dim_item d
                                  WHERE d.cj_item = s.cj_item)
                """),
        ).scalar()
        rows_out = conn.execute(
            text(f"""
                SELECT count(*) FROM {FACT} f
                JOIN core.dim_channel ch ON ch.channel_key = f.channel_key
                WHERE ch.sales_channel = :ch
                  AND f.sales_date BETWEEN :d_from AND :d_to
                """),
            {"ch": channel, "d_from": d_from, "d_to": d_to},
        ).scalar()
    from etl.runlog import record_dq
    record_dq(engine, run_id, "referential:sellout.cj_item->dim_item",
              "WARN", orphans == 0, observed=f"{orphans} orphan rows in {channel} {year}-{month:02d}",
              expected="0 — orphan rows load regardless, never dropped")
    warn(engine, run_id,
         lambda conn: c.row_count_within_history(conn, FACT, "TRUE", rows_out, 30.0),
         "row_count_within_history:core.fact_daily_sales")
    return {"rows_in": rows_in, "rows_out": rows_out}
