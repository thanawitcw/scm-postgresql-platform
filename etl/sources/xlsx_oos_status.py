"""S12 — OOS stock status snapshots, OOS_Stock_Status xlsx.

Raw mirror, no business transform: every source column loads (Unnamed
spacers dropped). Stock_Date arrives mixed — ISO strings, Excel serials
("45719"), native datetimes — all parsed to ISO dates, strict. DC5 block
(2026 files only) absent elsewhere: stg keeps NULL (file-faithful).
A future unknown column fails the load — never silently ignored.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd

from etl.config import settings

SUBDIR = "Data for PowerBI"
SUB = "OOS_Stock_Status"
SHEET = "Sheet1"

FILENAME_RE = re.compile(r"^(\d{4})_(\d{1,2})\.[A-Za-z]+ (\d{2})-(\d{2})-(\d{4})\.xlsx$")

RENAME = {
    "Stock_Date": "stock_date", "Division_SHM": "division_shm",
    "ownerSCM": "owner_scm", "Supplier Name": "supplier_name",
    "SHM_Item": "shm_item", "CJ_Item": "cj_item",
    "CJ_Description": "cj_description", "LeadTime(Days)": "lead_time_days",
    "CJ_Status": "cj_status", "SHM_Status": "shm_status",
    "PC_Cartons": "pc_cartons", "First_SO_Date": "first_so_date",
    "Helper Column for Total": "helper_column_for_total",
}

SUFFIX_MAP = {
    "ActiveAssort": "active_assort", "StoreOOS": "store_oos",
    "StoreStockQty": "store_stock_qty", "DOHStore": "doh_store",
    "AvgSaleQty90D": "avg_sale_qty_90d", "DCStockQty": "dc_stock_qty",
    "DOHDC": "doh_dc", "POpending": "po_pending",
    "First_Del_Date": "first_del_date", "StockStatus": "stock_status",
    "OOS_By": "oos_by", "OOS_Reason": "oos_reason",
    "Owner Note": "owner_note",
}

BLOCKS = ("Total", "DC1", "DC2", "DC4", "DC5")

NUM_SUFFIX = ("ActiveAssort", "StoreOOS", "StoreStockQty", "DOHStore",
              "AvgSaleQty90D", "DCStockQty", "DOHDC", "POpending")
DATE_SUFFIX = ("First_Del_Date",)

EXCEL_EPOCH = date(1899, 12, 30)


def source_dir() -> Path:
    return Path(settings.share_root) / "Back_Office_SCM" / SUBDIR / SUB


def file_stock_date(name: str) -> date:
    m = FILENAME_RE.match(name)
    if not m:
        raise ValueError(f"{name}: not a weekly OOS snapshot filename — refusing.")
    return date(int(m.group(5)), int(m.group(4)), int(m.group(3)))


def _parse_date_value(v) -> date | None:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    s = str(v).strip()
    if not s or s.lower() in ("none", "nat", "<na>"):
        return None
    if re.fullmatch(r"\d{4,6}", s):  # Excel serial (measured "45719")
        return EXCEL_EPOCH + timedelta(days=int(s))
    if re.fullmatch(r"\d{4,6}\.0+", s):  # integral float serial ("45470.0")
        return EXCEL_EPOCH + timedelta(days=int(float(s)))
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"unparseable date value {s!r} — failing, not guessing.")


def read_oos_file(path: Path) -> pd.DataFrame:
    df = pd.read_excel(path, sheet_name=SHEET, engine="openpyxl", dtype=str)
    df = df.drop(columns=[c for c in df.columns
                          if c is None or str(c).startswith("Unnamed:")],
                 errors="ignore")
    known = set(RENAME)
    for c in df.columns:
        s = str(c)
        if s in known:
            continue
        hit = next((b for b in BLOCKS if s.startswith(f"{b}_")), None)
        if hit is None or s[len(hit) + 1:] not in SUFFIX_MAP:
            raise ValueError(
                f"{path.name}: unexpected column {s!r} — new block or drift. "
                "Failing the load, not the DB (R3).")
    out = pd.DataFrame()
    out["stock_date"] = df["Stock_Date"].map(_parse_date_value)
    out["first_so_date"] = (df["First_SO_Date"].map(_parse_date_value)
                            if "First_SO_Date" in df.columns else None)
    for src, dst in RENAME.items():
        if src in ("Stock_Date", "First_SO_Date") or src not in df.columns:
            continue  # dates parsed above (ISO/serial/datetime mix)
        out[dst] = df[src].astype("string").str.strip().replace(
            {"": None, "None": None, "<NA>": None})
    for c in df.columns:
        s = str(c)
        hit = next((b for b in BLOCKS if s.startswith(f"{b}_")), None)
        if hit is None:
            continue
        dst = f"{hit.lower()}_{SUFFIX_MAP[s[len(hit) + 1:]]}"
        suffix = s[len(hit) + 1:]
        if suffix in NUM_SUFFIX:
            out[dst] = pd.to_numeric(df[c].astype("string").str.replace(
                ",", "", regex=False).str.strip(), errors="raise")
        elif suffix in DATE_SUFFIX:
            out[dst] = df[c].map(_parse_date_value)
        else:
            out[dst] = df[c].astype("string").str.strip().replace(
                {"": None, "None": None, "<NA>": None})
    for col in ("cj_item", "shm_item"):
        if out[col].isna().any():
            raise ValueError(f"{path.name}: NULL {col} — grain key missing.")
    if out.duplicated(subset=["stock_date", "cj_item", "shm_item"]).any():
        bad = out.loc[out.duplicated(
            subset=["stock_date", "cj_item", "shm_item"], keep=False),
            ["stock_date", "cj_item", "shm_item"]].head(3).to_dict("records")
        raise ValueError(f"{path.name}: dupes on grain {bad} — failing (R3).")
    return out
