"""dim_item — 2-column mapping (cj_item, supplier_name).

One row per cj_item (natural PK). Every non-null cj_item loads; duplicate
cj_item rows (pack-size variants) collapse with their supplier names
concatenated alphabetically (','-separated), resolved via dim_supplier with
fallback to the raw code — no variant's supplier is ever dropped.
Full replace (DELETE + INSERT) in ONE transaction; 2.7k rows, readers
see old-or-new, never empty (R1).
"""

from __future__ import annotations

import pandas as pd
from sqlalchemy import text

from etl.db.load import copy_df, truncate
from etl.quality import checks as c

DIM = "core.dim_item"
STG = "stg.stg_product_list"
STG_COLS = ["division", "buyer_team", "oem_brand", "non_vat", "supplier_code",
            "shm_item", "cj_item", "td_item", "dcm_item", "cj_description",
            "description", "cat", "sub_cat", "brand", "item_group",
            "first_so_date", "cj_status", "cj_status_as_of", "td_status",
            "td_status_as_of", "dcm_status", "product_shelf_life", "pc_cartons",
            "pc_pack", "pack_cartons", "buy_price", "sale_price_dcm",
            "sale_price_cj", "ti", "hi", "cartons_pallet", "ship_from_code",
            "barcode_cartons", "ctn_wide_cc", "ctn_long_cc", "ctn_high_cc",
            "gross_weight_kg", "barcode_pc", "barcode_pack", "country",
            "transport_owner_by_item", "loading_group", "loading_note",
            "machine_group", "group_of_sup", "commodities_flag", "fc_item",
            "base_lt", "moq_number", "moq_text", "moq_group", "dc1_lt",
            "dc2_lt", "dc3_lt", "dc4_lt", "dc5_lt", "business_group", "rec_no",
            "ship_from_condition", "uploaded_at", "source_file"]


def run(engine, run_id: str, df: pd.DataFrame, source_file: str) -> dict:
    rows_in = len(df)
    with engine.begin() as conn:  # tx1: staging (every row, forensic copy)
        truncate(conn, STG)
        staged = df.copy()
        staged["uploaded_at"] = None
        staged["source_file"] = source_file
        copy_df(conn, STG, staged, STG_COLS)
        c.not_empty(conn, STG)
    with engine.begin() as conn:  # tx2: full replace of the 2-column map
        conn.execute(text(f"DELETE FROM {DIM}"))
        conn.execute(text(
            f"""
            INSERT INTO {DIM} (cj_item, supplier_name)
            SELECT s.cj_item,
                   string_agg(
                       DISTINCT COALESCE(d.supplier_name, s.supplier_code),
                       ',' ORDER BY COALESCE(d.supplier_name, s.supplier_code))
            FROM   stg.stg_product_list s
            LEFT JOIN core.dim_supplier d
                   ON d.supplier_code = s.supplier_code
            WHERE  s.cj_item IS NOT NULL
            GROUP BY s.cj_item
            """
        ))
        c.not_empty(conn, DIM)
        c.unique_on(conn, DIM, ["cj_item"])
        rows_out = conn.execute(text(f"SELECT count(*) FROM {DIM}")).scalar()
        null_names = conn.execute(text(
            f"SELECT count(*) FROM {DIM} WHERE supplier_name IS NULL")).scalar()
    from etl.runlog import record_dq
    record_dq(engine, run_id, "null_supplier_name", "WARN",
              null_names == 0, observed=f"{null_names} items",
              expected="0 — every cj_item has ≥1 supplier code")
    return {"rows_in": rows_in, "rows_out": rows_out}
