"""dim_supplier (S9) — stg truncate+COPY, then upsert on supplier_code.

Never DELETEs: a supplier that disappears from the file keeps its row
(the FK from dim_item must never dangle). Blank placeholder filtered at extract.
"""

from __future__ import annotations

import pandas as pd
from sqlalchemy import text

from etl.db.load import copy_df, truncate
from etl.pipelines.runner import warn
from etl.quality import checks as c

STG = "stg.stg_supplier_list"
DIM = "core.dim_supplier"
STG_COLS = ["supplier_code", "supplier_name", "supplier_type", "product", "owner",
            "status", "credit_term", "country", "division",
            "uploaded_at", "source_file"]


def run(engine, run_id: str, df: pd.DataFrame, source_file: str) -> dict:
    rows_in = len(df)
    with engine.begin() as conn:  # tx1: staging (disposable, truncate-then-load)
        truncate(conn, STG)
        staged = df.copy()
        staged["uploaded_at"] = None
        staged["source_file"] = source_file
        copy_df(conn, STG, staged, STG_COLS)
        c.not_empty(conn, STG)
    with engine.begin() as conn:  # tx2: upsert (Type 1 overwrite, M6)
        conn.execute(text(
            f"""
            INSERT INTO {DIM}
                (supplier_code, supplier_name, supplier_type, status,
                 credit_term, country, division, loaded_at, source_file)
            SELECT supplier_code, supplier_name, supplier_type, status,
                   credit_term, country, division, now(), source_file
            FROM   {STG}
            ON CONFLICT (supplier_code) DO UPDATE SET
                supplier_name = EXCLUDED.supplier_name,
                supplier_type = EXCLUDED.supplier_type,
                status        = EXCLUDED.status,
                credit_term   = EXCLUDED.credit_term,
                country       = EXCLUDED.country,
                division      = EXCLUDED.division,
                loaded_at     = now(),
                source_file   = EXCLUDED.source_file
            """
        ))
        c.not_empty(conn, DIM)
        c.unique_on(conn, DIM, ["supplier_code"])
        rows_out = conn.execute(text(f"SELECT count(*) FROM {DIM}")).scalar()
    warn(engine, run_id,
         lambda conn: c.row_count_within_history(conn, DIM, "TRUE", rows_out, 20.0),
         "row_count_within_history:core.dim_supplier")
    return {"rows_in": rows_in, "rows_out": rows_out}
