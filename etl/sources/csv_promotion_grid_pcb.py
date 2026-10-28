"""S2 — promotion grid, PCB. Daily CSV export of Progrid_PCB.

Source (2026-10-21): `ProGrid_PCB_YYYYMMDD.csv` in the OneDrive folder
below — one full snapshot per business date, files accumulate, NEWEST date
only (business order from the filename, never mtime).

Encoding is UTF-8 WITH BOM, comma. This export's header carries padding
(` Pro_Price `, ` Period `, ` Status `) and its cells are padded too, so
headers are trimmed before the by-name contract and values are trimmed at
parse. `Start`/`End` arrive as `DD-Mon-YYYY`, parsed with an explicit format.

Price ruling (§5.1.1): S2 `Pro_Price` → promo_price_per_piece (same business
quantity as S1's PromoPrice_per_Piece under a different column name).
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

SUBDIR = "raw_progrid_pcb"
FILENAME_RE = re.compile(r"^ProGrid_PCB_(\d{8})\.csv$")
DATE_FORMAT = "%d-%b-%Y"

# Trimmed column set: only the 9 contracted columns (0007 dropped
# division / supplier / description / pro_id_raw from stg).
EXPECTED = ["Cus_code", "RSP",
            "RSP Unit (Carton / Pack / Piece)", "Pro_Type", "Pro_Theme",
            "Pro_Price", "Start", "End", "Status"]

RENAME = {"Cus_code": "cj_item", "RSP": "rsp",
          "RSP Unit (Carton / Pack / Piece)": "rsp_unit",
          "Pro_Type": "pro_type", "Pro_Theme": "pro_theme",
          "Pro_Price": "promo_price_per_piece",
          "Start": "valid_from", "End": "valid_to",
          "Status": "status_raw"}


def source_dir() -> Path:
    return Path(settings.stock_report_root) / SUBDIR


def file_date(path: Path) -> date:
    """Business date from `ProGrid_PCB_YYYYMMDD.csv`; anything else fails."""
    m = FILENAME_RE.match(path.name)
    if not m:
        raise ValueError(
            f"{path.name}: not a ProGrid_PCB_YYYYMMDD.csv file "
            f"(expected e.g. ProGrid_PCB_20261021.csv)")
    return datetime.strptime(m.group(1), "%Y%m%d").date()


def iter_files(directory: Path | None = None) -> list[Path]:
    """Every candidate CSV, owner locks excluded. Unexpected names fail loudly."""
    d = directory or source_dir()
    files = list(iter_data_files(d, "*.csv"))
    if not files:
        raise FileNotFoundError(f"no PCB promotion-grid CSVs in {d}")
    bad = [p.name for p in files if not FILENAME_RE.match(p.name)]
    if bad:
        raise ValueError(
            f"unexpected file(s) in {d}: {bad} — expected "
            "ProGrid_PCB_YYYYMMDD.csv only (remove or rename).")
    return files


def latest_file(directory: Path | None = None) -> Path:
    """Newest file by the YYYYMMDD in its name (business order, never mtime)."""
    return max(iter_files(directory), key=file_date)


def _text(series: pd.Series) -> pd.Series:
    """Trim; a blank cell means NULL (as the xlsx read did), never a parse error."""
    return series.astype("string").str.strip().replace({"": pd.NA})


def frame_s2(df: pd.DataFrame) -> pd.DataFrame:
    out = df.rename(columns=RENAME)
    out["cj_item"] = out["cj_item"].astype("string").str.strip()
    for col in ("pro_type", "pro_theme", "rsp_unit"):
        out[col] = out[col].map(normalise_enum)
    out["status_norm"] = out["status_raw"].map(normalise_status)
    out["is_cancelled"] = out["status_norm"] == "cancel"
    for col in ("valid_from", "valid_to"):
        out[col] = pd.to_datetime(
            _text(out[col]), format=DATE_FORMAT,
            errors="raise").dt.date
    for col in ("rsp", "promo_price_per_piece"):
        out[col] = pd.to_numeric(_text(out[col]), errors="raise")
    out["pro_price_rsp_gap"] = None
    out["source_key"] = "S2"
    return out


def read_file(path: Path) -> pd.DataFrame:
    """Read one daily export; header padding trimmed, columns BY NAME."""
    df = pd.read_csv(path, encoding="utf-8-sig", dtype=str,
                     keep_default_na=False)
    df.columns = [str(c).strip() for c in df.columns]
    missing = [c for c in EXPECTED if c not in df.columns]
    if missing:
        raise ValueError(
            f"{path.name} [S2]: expected column(s) missing: {missing} — "
            "schema drift. Failing the load, not the DB (R3).")
    return frame_s2(df[EXPECTED])


class CsvPromotionGridPcb(Source):
    name = "csv_promotion_grid_pcb"

    def extract(self, run_date: date):
        raise NotImplementedError(
            "progrid loads from the archive; use frame_s2(latest_file())")

    def read_file(self, path: Path) -> pd.DataFrame:
        return read_file(path)
