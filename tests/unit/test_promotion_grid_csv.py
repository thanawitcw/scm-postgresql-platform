"""Unit tests for the S1/S2 daily-CSV contract (2026-10-21 source change).

Encodes the traps measured in the live exports:
- S1 dates are `M/D/YYYY`; S2 dates are `DD-Mon-YYYY` (both strict formats)
- S2's header/cells carry padding (` Pro_Price `, ` Cancel `)
- UTF-8 BOM on both files
- newest file wins by the YYYYMMDD in the name, never mtime
- the S1 `Pro_Price` → `pro_price_rsp_gap` split stays intact
"""

import csv
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from etl.sources import csv_promotion_grid as s1
from etl.sources import csv_promotion_grid_pcb as s2


def _write(path: Path, header: list[str], rows: list[list[str]]) -> Path:
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)
    return path


def _s1_row(**over):
    row = {"Cus_code": "20037758", "RSP": "39",
           "RSP Unit (Carton / Pack / Piece)": "piece", "Pro_Type": "7-Days",
           "Pro_Theme": "Price Off", "Pro_Price": "20",
           "PromoPrice_per_Piece": "36", "Start": "2/25/2025",
           "End": "3/24/2025", "Status": "0"}
    row.update(over)
    return [row[c] for c in s1.EXPECTED]


def _s2_row(**over):
    row = {"Cus_code": "20022553", "RSP": "75",
           "RSP Unit (Carton / Pack / Piece)": "Piece", "Pro_Type": "7-Days",
           "Pro_Theme": "Price Off", "Pro_Price": " 59 ",
           "Start": "16-Jan-2025", "End": "22-Jan-2025",
           "Status": " Cancel "}
    row.update(over)
    return [row[c] for c in s2.EXPECTED]


def _s2_header() -> list[str]:
    # Live export pads three headers; read_file must trim before matching.
    return [{"Pro_Price": " Pro_Price ", "Status": " Status "}.get(c, c)
            for c in s2.EXPECTED]


def test_s1_frame_parses_us_dates_and_price_split(tmp_path):
    p = _write(tmp_path / "ProGrid_Data_20261021.csv",
               s1.EXPECTED + ["Division"],
               [_s1_row() + ["Foods"]])
    df = s1.read_file(p)
    row = df.iloc[0]
    assert row["valid_from"] == date(2025, 2, 25)
    assert row["valid_to"] == date(2025, 3, 24)
    assert row["promo_price_per_piece"] == 36
    assert row["pro_price_rsp_gap"] == 20
    assert row["pro_type"] == "7-DAYS"
    assert row["rsp_unit"] == "PIECE"
    assert row["is_cancelled"] == False  # noqa: E712 — numpy bool
    assert row["source_key"] == "S1"


def test_s2_frame_trims_padded_header_and_values(tmp_path):
    p = _write(tmp_path / "ProGrid_PCB_20261021.csv",
               _s2_header(), [_s2_row()])
    df = s2.read_file(p)
    row = df.iloc[0]
    assert row["valid_from"] == date(2025, 1, 16)
    assert row["valid_to"] == date(2025, 1, 22)
    assert row["promo_price_per_piece"] == 59
    assert pd.isna(row["pro_price_rsp_gap"])
    assert row["is_cancelled"] == True  # noqa: E712 — numpy bool
    assert row["source_key"] == "S2"


def test_iso_or_wrong_date_format_fails_loudly(tmp_path):
    p = _write(tmp_path / "ProGrid_Data_20261021.csv", s1.EXPECTED,
               [_s1_row(Start="2025-02-25")])
    with pytest.raises(ValueError):
        s1.read_file(p)

    q = _write(tmp_path / "ProGrid_PCB_20261021.csv", _s2_header(),
               [_s2_row(Start="16/01/2025")])
    with pytest.raises(ValueError):
        s2.read_file(q)


def test_blank_cells_become_null_like_the_xlsx_read(tmp_path):
    p = _write(tmp_path / "ProGrid_Data_20261021.csv", s1.EXPECTED,
               [_s1_row(Pro_Price="", Start="")])
    row = s1.read_file(p).iloc[0]
    assert pd.isna(row["pro_price_rsp_gap"])
    assert pd.isna(row["valid_from"])


def test_missing_column_fails_loudly(tmp_path):
    header = [c for c in s1.EXPECTED if c != "PromoPrice_per_Piece"]
    p = _write(tmp_path / "ProGrid_Data_20261021.csv", header,
               [[v for c, v in zip(s1.EXPECTED, _s1_row())
                 if c != "PromoPrice_per_Piece"]])
    with pytest.raises(ValueError, match="PromoPrice_per_Piece"):
        s1.read_file(p)


def test_latest_file_is_newest_yyyymmdd_not_mtime(tmp_path):
    old = _write(tmp_path / "ProGrid_Data_20261020.csv", s1.EXPECTED, [_s1_row()])
    new = _write(tmp_path / "ProGrid_Data_20261021.csv", s1.EXPECTED, [_s1_row()])
    # A newer mtime on the older business date must not win.
    old.touch()
    (tmp_path / "~$ProGrid_Data_20261022.csv").write_text("lock", encoding="utf-8")
    assert s1.latest_file(tmp_path) == new
    assert s1.file_date(new) == date(2026, 10, 21)


def test_unexpected_csv_name_fails_loudly(tmp_path):
    _write(tmp_path / "ProGrid_Data_20261021.csv", s1.EXPECTED, [_s1_row()])
    _write(tmp_path / "ProGrid_Data_latest.csv", s1.EXPECTED, [_s1_row()])
    with pytest.raises(ValueError, match="unexpected file"):
        s1.latest_file(tmp_path)


def test_empty_folder_fails_loudly(tmp_path):
    with pytest.raises(FileNotFoundError):
        s2.latest_file(tmp_path)
