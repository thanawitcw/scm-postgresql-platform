"""S11 — weekly stock snapshots, Sahamit xlsx (Weekly_Stock folder).

Contract: one file per Monday, `YYYY_M.Mon DD-MM-YYYY.xlsx`, Sheet1 pinned.
stock_date comes from the filename (explicit %d-%m-%Y, never inferred).
Grain (stock_date x cj_item): one row per Product per file; TierCompany rides
as an attribute. Total/DC1/DC2/DC4(/DC5) measure blocks, all 12 suffixes
stored file-faithful (owner ruling: DOH* and PercOOS land, nothing derived
away at read).
Dropped at read: Class, Category, Brick, Subcate, Name, Division, Status,
Delivery_unit, Delivery_unit_num, plus one-off 'tier' (2026-02-23 only).
A future unknown block (e.g. DC6_*) fails the load — never silently ignored.
"""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path

import pandas as pd

from etl.config import settings
from etl.sources.base import Source  # noqa: F401  (ABC reference for §13)

SUBDIR = "Data for PowerBI"
SUB = "Weekly_Stock"
SHEET = "Sheet1"

DROP = {"Class", "Category", "Brick", "Subcate", "Name", "Division",
        "Status", "Delivery_unit", "Delivery_unit_num", "tier"}

BLOCKS = ("Total", "DC1", "DC2", "DC4", "DC5")
MEASURE_MAP = {"ScmAssort": "total_store", "AvgSaleQty90D": "avg_sales_qty_90d",
               "AvgCOGS90D": "avg_cogs_90d",
               "StoreStockQty": "store_stock_qty",
               "StoreStockValue": "store_stock_value",
               "DOHStore": "store_doh",
               "DCStockQty": "dc_stock_qty", "DCStockValue": "dc_stock_value",
               "DOHDC": "dc_doh",
               "OOSAssort": "active_store", "CountOKROOS": "oos_store",
               "PercOOS": "store_oos_pct"}


def source_dir() -> Path:
    return Path(settings.share_root) / "Back_Office_SCM" / SUBDIR / SUB


def stock_date_from_name(name: str) -> date:
    m = re.search(r"(\d{2})-(\d{2})-(\d{4})\.xlsx$", name)
    if not m:
        raise ValueError(f"{name}: no DD-MM-YYYY date in filename — failing (R3).")
    d = date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
    if d.strftime("%a") != "Mon":
        raise ValueError(f"{name}: stock_date {d} is not a Monday — check the file.")
    return d


def read_weekly_file(path: Path) -> pd.DataFrame:
    """Read one snapshot. Absent blocks (DC5 pre-2026-05-18) stay NULL here;
    the 0-fill ruling applies stg -> fact so stg stays file-faithful."""
    from etl.sources.xlsx_reader import read_pinned_sheet  # noqa

    df = pd.read_excel(path, sheet_name=SHEET, engine="openpyxl")
    df = df.drop(columns=[c for c in df.columns
                          if c is None or str(c).startswith("Unnamed:")],
                 errors="ignore")
    unknown = [c for c in df.columns
               if c not in DROP and c != "Product" and c != "TierCompany"
               and not any(str(c).startswith(f"{b}_") for b in BLOCKS)]
    if unknown:
        raise ValueError(
            f"{path.name}: unexpected column(s) {unknown} — new block or drift. "
            "Failing the load, not the DB (R3).")
    keep = ["Product", "TierCompany"] + [
        c for c in df.columns
        if any(str(c).startswith(f"{b}_") for b in BLOCKS)]
    out = df[keep].copy()
    rename = {"Product": "cj_item", "TierCompany": "tier_company"}
    for c in keep:
        if "_" in str(c):
            block, measure = str(c).split("_", 1)
            rename[c] = f"{block.lower()}_{MEASURE_MAP[measure]}"
    unknown_measure = [c for c in keep if c not in rename]
    if unknown_measure:
        raise ValueError(
            f"{path.name}: unmapped measure column(s) {unknown_measure} — "
            "add to MEASURE_MAP, never silently ignore.")
    out = out.rename(columns=rename)
    out["cj_item"] = out["cj_item"].astype("string").str.strip()
    out["tier_company"] = out["tier_company"].astype("string").str.strip().replace(
        {"": None, "None": None, "<NA>": None})
    for c in out.columns:
        if c not in ("cj_item", "tier_company"):
            out[c] = pd.to_numeric(out[c], errors="raise")
    out["stock_date"] = stock_date_from_name(path.name)
    # Exact-duplicate rows carry zero information (measured: 1 product x2 in
    # 2025-01-06). Drop them, count them for a DQ WARN. Conflicting dupes
    # (same cj_item, different values) are ambiguous and fail below.
    n0 = len(out)
    out = out.drop_duplicates()
    out.attrs["exact_dupes_dropped"] = n0 - len(out)
    if out["cj_item"].isna().any():
        bad = out.loc[out["cj_item"].isna()].head(3).to_dict("records")
        raise ValueError(
            f"{path.name}: NULL cj_item {bad} — grain key missing, failing (R3).")
    if out["cj_item"].duplicated().any():
        bad = out.loc[out["cj_item"].duplicated(keep=False), "cj_item"].unique()[:5]
        raise ValueError(
            f"{path.name}: conflicting duplicate Product rows {list(bad)} — "
            "grain violated, failing (R3).")
    return out
