"""S7 — DC daily stock snapshots, DC_DailyStock xlsx (2025 + 2026 folders).

Contract: Sheet1 pinned, 7 columns with 2 blank spacers (select BY NAME —
S7-style positional reads are a silent-drift hazard). Only 5 load:
Calendar Day, Plant, Material, Stock Qty, Stock Value.
Calendar Day is the string 'DD.MM.YYYY' (explicit format, ISO output).
'0'/blank dates are sentinels (measured 2025-10-15) — dropped + counted.
Byte-identical duplicate rows carry zero information — dropped + counted;
conflicting dupes on (date, plant, material) fail the load.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from pathlib import Path

import pandas as pd

from etl.config import settings

SUBDIR = "Data For Stock"
SUB = "DC_DailyStock"
SHEET = "Sheet1"
YEARS = ("2025", "2026")

# Steady-state filename contract: DC_Enddd-mm-yyyy.xlsx, nothing else.
FILENAME_RE = re.compile(r"^DC_End(\d{2})-(\d{2})-(\d{4})\.xlsx$")


def file_stock_date(path: Path) -> date:
    """Filename date (dd-mm-yyyy). Non-matching names fail loudly —
    e.g. the Template file must never be mistaken for data."""
    m = FILENAME_RE.match(path.name)
    if not m:
        raise ValueError(
            f"{path.name}: not a DC_Enddd-mm-yyyy.xlsx snapshot — refusing.")
    return date(int(m.group(3)), int(m.group(2)), int(m.group(1)))

KEEP = ["Calendar Day", "Plant", "Material", "Stock Qty", "Stock Value"]

RENAME = {"Calendar Day": "stock_date", "Plant": "plant",
          "Material": "material", "Stock Qty": "stock_qty",
          "Stock Value": "stock_value"}


def source_dirs() -> list[Path]:
    return [Path(settings.share_root) / "Back_Office_SCM" / SUBDIR / SUB / y
            for y in YEARS]


def source_root() -> Path:
    return Path(settings.share_root) / "Back_Office_SCM" / SUBDIR / SUB


# Exact filenames that are known non-data (skipped by name, loudly logged).
# Anything else not matching FILENAME_RE fails the run — a future mislabeled
# data file must never be silently ignored.
KNOWN_NON_DATA = {"Template Daily DC Stock.xlsx"}


def read_dc_file(path: Path) -> pd.DataFrame:
    df = pd.read_excel(path, sheet_name=SHEET, engine="openpyxl", dtype=str)
    df = df.drop(columns=[c for c in df.columns
                          if c is None or str(c).startswith("Unnamed:")],
                 errors="ignore")
    missing = [c for c in KEEP if c not in df.columns]
    if missing:
        raise ValueError(
            f"{path.name}: expected column(s) missing: {missing} — "
            "schema drift. Failing the load, not the DB (R3).")
    out = df[KEEP].rename(columns=RENAME)
    # Sentinel dates first ('0' is not a date), then strict dotted parse.
    raw = out["stock_date"].astype("string").str.strip()
    sentinel = raw.isin(["0", "", "None", "<NA>"]) | raw.isna()
    n_sentinel = int(sentinel.sum())
    out = out[~sentinel].copy()
    out["stock_date"] = pd.to_datetime(out["stock_date"], format="%d.%m.%Y",
                                       errors="raise").dt.date
    out.attrs["sentinel_dates_dropped"] = n_sentinel
    out["plant"] = out["plant"].astype("string").str.strip()
    out["material"] = out["material"].astype("string").str.strip()
    out["stock_qty"] = pd.to_numeric(out["stock_qty"], errors="raise")
    out["stock_value"] = pd.to_numeric(out["stock_value"], errors="raise")
    n0 = len(out)
    out = out.drop_duplicates()
    out.attrs["exact_dupes_dropped"] = n0 - len(out)
    dup = out.duplicated(subset=["stock_date", "plant", "material"], keep=False)
    if dup.any():
        bad = out.loc[dup].head(3).to_dict("records")
        raise ValueError(
            f"{path.name}: conflicting dupes on (date, plant, material) {bad} — "
            "grain violated, failing (R3).")
    return out


def file_mtime(path: Path) -> datetime:
    return datetime.fromtimestamp(path.stat().st_mtime)
