"""S5 — goods receipt, annual Summary_GR files (S.5.1.1).

ISO-date rule: Report_Date / Received_Date MUST be ISO (YYYY-MM-DD). Any
slash-formatted value fails the load — the owner fixed the source to ISO
(2026-10-05) and drift must surface, never silently parse (S5 D/M trap).
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pandas as pd

from etl.config import settings

SUBDIR = "Data for PowerBI"
SUB = "Data_GR_CJ"

EXPECTED = ["Report_Date", "Received_Date", "PO_CJ_NO", "CJ_Item", "GR_DC",
            "GR_Qty", "GR_Value"]

RENAME = {"Report_Date": "report_date", "Received_Date": "received_date",
          "PO_CJ_NO": "po_no", "CJ_Item": "cj_item", "GR_DC": "gr_dc",
          "GR_Qty": "gr_qty_piece", "GR_Value": "gr_value_in_vat"}


def source_dir() -> Path:
    return Path(settings.share_root) / "Back_Office_SCM" / SUBDIR / SUB


def _parse_iso(series: pd.Series, col: str, path: Path) -> pd.Series:
    raw = series.astype("string")
    if raw.str.contains("/").any():
        bad = raw[raw.str.contains("/")].head(3).tolist()
        raise ValueError(
            f"{path.name}: {col} has non-ISO values {bad} — source must be "
            "ISO YYYY-MM-DD (owner fixed 2026-10-05; failing, not guessing).")
    return pd.to_datetime(raw, format="%Y-%m-%d", errors="raise").dt.date


def read_gr_file(path: Path) -> tuple[pd.DataFrame, datetime]:
    """Read one annual file. Headers stripped (' GR_Qty ' trap)."""
    df = pd.read_csv(path, encoding="utf-8-sig", dtype=str)
    df = df.rename(columns={c: c.strip() for c in df.columns})
    missing = [c for c in EXPECTED if c not in df.columns]
    extra = [c for c in df.columns if c not in EXPECTED]
    if missing or extra:
        raise ValueError(
            f"schema drift in {path.name}: missing={missing} extra={extra}. "
            "Failing the load, not the DB (R3).")
    out = df.rename(columns=RENAME)
    out["report_date"] = _parse_iso(out["report_date"], "Report_Date", path)
    out["received_date"] = _parse_iso(out["received_date"], "Received_Date", path)
    out["cj_item"] = out["cj_item"].astype("string").str.strip()
    out["po_no"] = out["po_no"].astype("string").str.strip()
    out["gr_dc"] = out["gr_dc"].astype("string").str.strip()
    out["gr_qty_piece"] = pd.to_numeric(out["gr_qty_piece"], errors="raise")
    out["gr_value_in_vat"] = pd.to_numeric(out["gr_value_in_vat"], errors="raise")
    uploaded_at = datetime.fromtimestamp(path.stat().st_mtime)
    return out, uploaded_at
