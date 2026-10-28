"""S10 — sales plan snapshots, RunningProGrid_AsOf_FCMonth CSVs.

Raw mirror: rename cus_code -> cj_item (the join contract, §5.1.1) and parse
types, nothing else. pro_type/pro_theme keep source spelling INCLUDING
case/whitespace variants — normalisation happens in rpt at join time.
forecast_qty/cartons carry thousands separators ("1,428.14") — stripped at
parse (parsing, not a business transform). ISO sales_plan_date, strict.
"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

import pandas as pd

from etl.config import settings

SUBDIR = "Data for PowerBI"
SUB = "progrid_sales_plan_as_of"

EXPECTED = ["forecast_month_key", "fc_lead_time_months", "sales_plan_date",
            "demand_owner", "cus_code", "sales_channel", "pro_type",
            "pro_theme", "promoprice_per_piece", "forecast_cartons",
            "forecast_qty"]

FILENAME_RE = re.compile(r"^(\d{4})_(\d{2})\.RunningProGrid_AsOf_FCMonth\.csv$")


def source_dir() -> Path:
    return Path(settings.share_root) / "Back_Office_SCM" / SUBDIR / SUB


def _num(series: pd.Series) -> pd.Series:
    return pd.to_numeric(series.astype("string").str.replace(",", "", regex=False)
                         .str.strip().replace({"": None, "nan": None, "None": None}),
                         errors="raise")


def read_plan_file(path: Path) -> tuple[pd.DataFrame, datetime]:
    df = pd.read_csv(path, encoding="utf-8-sig", dtype=str)
    missing = [c for c in EXPECTED if c not in df.columns]
    extra = [c for c in df.columns if c not in EXPECTED]
    if missing or extra:
        raise ValueError(
            f"schema drift in {path.name}: missing={missing} extra={extra}. "
            "Failing the load, not the DB (R3).")
    out = df.rename(columns={"cus_code": "cj_item",
                           "promoprice_per_piece": "promo_price_per_piece"})
    out["sales_plan_date"] = pd.to_datetime(
        out["sales_plan_date"].astype("string"), format="%Y-%m-%d",
        errors="raise").dt.date
    out["cj_item"] = out["cj_item"].astype("string").str.strip()
    out["fc_lead_time_months"] = pd.to_numeric(
        out["fc_lead_time_months"], errors="raise").astype("Int64")
    for col in ("promo_price_per_piece", "forecast_cartons", "forecast_qty"):
        out[col] = _num(out[col])
    uploaded_at = datetime.fromtimestamp(path.stat().st_mtime)
    return out, uploaded_at
