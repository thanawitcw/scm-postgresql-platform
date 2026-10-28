"""S1 — promotion grid, all divisions. Daily CSV export of ProGrid_Data.

Source (2026-10-21): the owner now publishes one CSV per business date,
`ProGrid_Data_YYYYMMDD.csv`, into the OneDrive folder below. Each file is a
full snapshot and files accumulate — the pipeline always loads the NEWEST
date only (never mtime: sync order is meaningless, §10.1).

Encoding is UTF-8 WITH BOM, comma, header row 1, 21 columns. `Start`/`End`
arrive as `M/D/YYYY` (the xlsx-era ISO/Excel-datetime rule no longer applies),
so they are parsed with an explicit format and fail loudly on drift.

Price ruling (§5.1.1): S1 `PromoPrice_per_Piece` → promo_price_per_piece;
S1's own `Pro_Price` is a DIFFERENT field → pro_price_rsp_gap (nullable,
never coalesced).
"""

from __future__ import annotations

import re
from datetime import date, datetime
from pathlib import Path

import pandas as pd

from etl.config import settings
from etl.sources.access_product_master import normalise_enum, normalise_status
from etl.sources.base import Source
from etl.sources.scan import iter_data_files

SUBDIR = "raw_progrid_food_nf_import"
FILENAME_RE = re.compile(r"^ProGrid_Data_(\d{8})\.csv$")
DATE_FORMAT = "%m/%d/%Y"

# Trimmed column set: only the 10 contracted columns (0007 dropped
# division / description / pro_id_raw from stg).
EXPECTED = ["Cus_code", "RSP",
            "RSP Unit (Carton / Pack / Piece)", "Pro_Type", "Pro_Theme",
            "Pro_Price", "PromoPrice_per_Piece", "Start", "End",
            "Status"]

RENAME = {"Cus_code": "cj_item",
          "RSP": "rsp",
          "RSP Unit (Carton / Pack / Piece)": "rsp_unit",
          "Pro_Type": "pro_type", "Pro_Theme": "pro_theme",
          "Pro_Price": "pro_price_rsp_gap",
          "PromoPrice_per_Piece": "promo_price_per_piece",
          "Start": "valid_from", "End": "valid_to",
          "Status": "status_raw"}


def source_dir() -> Path:
    return Path(settings.stock_report_root) / SUBDIR


def file_date(path: Path) -> date:
    """Business date from `ProGrid_Data_YYYYMMDD.csv`; anything else fails."""
    m = FILENAME_RE.match(path.name)
    if not m:
        raise ValueError(
            f"{path.name}: not a ProGrid_Data_YYYYMMDD.csv file "
            f"(expected e.g. ProGrid_Data_20261021.csv)")
    return datetime.strptime(m.group(1), "%Y%m%d").date()


def iter_files(directory: Path | None = None) -> list[Path]:
    """Every candidate CSV, owner locks excluded. Unexpected names fail loudly."""
    d = directory or source_dir()
    files = list(iter_data_files(d, "*.csv"))
    if not files:
        raise FileNotFoundError(f"no promotion-grid CSVs in {d}")
    bad = [p.name for p in files if not FILENAME_RE.match(p.name)]
    if bad:
        raise ValueError(
            f"unexpected file(s) in {d}: {bad} — expected "
            "ProGrid_Data_YYYYMMDD.csv only (remove or rename).")
    return files


def latest_file(directory: Path | None = None) -> Path:
    """Newest file by the YYYYMMDD in its name (business order, never mtime)."""
    return max(iter_files(directory), key=file_date)


def _text(series: pd.Series) -> pd.Series:
    """Trim; a blank cell means NULL (as the xlsx read did), never a parse error."""
    return series.astype("string").str.strip().replace({"": pd.NA})


def frame_s1(df: pd.DataFrame) -> pd.DataFrame:
    out = df.rename(columns=RENAME)
    out["cj_item"] = out["cj_item"].astype("string").str.strip()
    for col in ("pro_type", "pro_theme", "rsp_unit"):
        out[col] = out[col].map(normalise_enum)
    out["status_norm"] = out["status_raw"].map(normalise_status)
    # Exact membership — never str.contains (a future 'Not Cancelled' must not match).
    out["is_cancelled"] = out["status_norm"] == "cancel"
    for col in ("valid_from", "valid_to"):
        out[col] = pd.to_datetime(
            _text(out[col]), format=DATE_FORMAT,
            errors="raise").dt.date
    for col in ("rsp", "pro_price_rsp_gap", "promo_price_per_piece"):
        out[col] = pd.to_numeric(_text(out[col]), errors="raise")
    out["source_key"] = "S1"
    return out


def read_file(path: Path) -> pd.DataFrame:
    """Read one daily export, columns BY NAME (extras are ignored)."""
    df = pd.read_csv(path, encoding="utf-8-sig", dtype=str,
                     keep_default_na=False)
    df.columns = [str(c).strip() for c in df.columns]
    missing = [c for c in EXPECTED if c not in df.columns]
    if missing:
        raise ValueError(
            f"{path.name} [S1]: expected column(s) missing: {missing} — "
            "schema drift. Failing the load, not the DB (R3).")
    return frame_s1(df[EXPECTED])


class CsvPromotionGrid(Source):
    name = "csv_promotion_grid"

    def extract(self, run_date: date):
        raise NotImplementedError(
            "progrid loads from the archive; use frame_s1(latest_file())")

    def read_file(self, path: Path) -> pd.DataFrame:
        return read_file(path)
