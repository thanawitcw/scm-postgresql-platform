"""Shared sellout reader (S3 + S4). BOM, drift guard, sentinels, enum fold.

Source grain (measured): one row per (date × item × promo price-point) —
every (date, item) carries a 'Normal' row plus promo-attributed rows.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pandas as pd

from etl.sources.access_product_master import normalise_enum

RENAME = {
    "Date": "sales_date",
    "CJ_Item": "cj_item",
    "Promotion_Type": "pro_type",
    "Promotion_Theme": "pro_theme",
    "ProPrice In.Vat": "pro_price_in_vat",
    "Total_SO_Qty": "so_qty_piece",
    "Total_SO_Amt (In.Vat)": "so_amt_in_vat",
    "Main_Promotion": "main_promotion",
}


def read_sellout(path: Path, expected: list[str], channel: str,
                 channel_col: str | None = None) -> tuple[pd.DataFrame, datetime]:
    """Read one monthly file as utf-8-sig (BOM). Returns (frame, uploaded_at)."""
    df = pd.read_csv(path, encoding="utf-8-sig")
    missing = [c for c in expected if c not in df.columns]
    extra = [c for c in df.columns if c not in expected]
    if missing or extra:
        raise ValueError(
            f"schema drift in {path.name}: missing={missing} extra={extra}. "
            "Failing the load, not the DB (R3)."
        )
    out = df.rename(columns=RENAME)
    # Date-format drift (measured): 7 of 43 files use M/D/YYYY ("2/1/2026"),
    # the rest ISO. Each file is internally consistent, and the leading
    # component always equals the file's month — so M/D is unambiguous.
    # Never infer dayfirst: a D/M misread silently shifts dates by months (S5 trap).
    raw = out["sales_date"].astype("string")
    if raw.str.contains("/").any():
        if raw.str.contains("-").any():
            raise ValueError(
                f"mixed date formats in {path.name} — refusing to guess (R3).")
        out["sales_date"] = pd.to_datetime(raw, format="%m/%d/%Y",
                                           errors="raise").dt.date
    else:
        out["sales_date"] = pd.to_datetime(raw, format="%Y-%m-%d",
                                           errors="raise").dt.date
    if out["sales_date"].map(lambda d: (d.year, d.month)).nunique() != 1:
        raise ValueError(
            f"{path.name}: dates span more than one month — wrong file or format.")
    out["cj_item"] = out["cj_item"].astype("string").str.strip()
    if channel_col is not None:
        got = out[channel_col].dropna().unique().tolist()
        if got != [channel]:
            raise ValueError(
                f"{path.name}: {channel_col}={got}, expected [{channel!r}] — "
                "wrong file in this channel's folder?"
            )
        out = out.drop(columns=[channel_col])
    out["sales_channel"] = channel
    out["pro_type"] = out["pro_type"].map(normalise_enum)
    out["pro_theme"] = out["pro_theme"].map(normalise_enum)
    # Sentinels → NULL (§5.1.1): 0-price and '-' marker are missing data.
    out["pro_price_in_vat"] = pd.to_numeric(out["pro_price_in_vat"],
                                            errors="raise").replace({0: None})
    out["so_qty_piece"] = pd.to_numeric(out["so_qty_piece"], errors="raise")
    out["so_amt_in_vat"] = pd.to_numeric(out["so_amt_in_vat"], errors="raise")
    out["is_main"] = out["main_promotion"].astype("string").str.strip() == "Main"
    out = out.drop(columns=["main_promotion"])
    uploaded_at = datetime.fromtimestamp(path.stat().st_mtime)
    return out, uploaded_at
