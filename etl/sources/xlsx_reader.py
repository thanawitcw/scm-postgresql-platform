"""Shared pinned-sheet xlsx reader. No format conversion (§2 boundary rule).

Sheets pinned by explicit name in module config; columns selected BY NAME
(S7-style blank spacer columns and the S1 trailing empty column make
positional reads a silent-drift hazard).
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd


def read_pinned_sheet(path: Path, sheet: str, expected: list[str]) -> pd.DataFrame:
    df = pd.read_excel(path, sheet_name=sheet, engine="openpyxl")
    df = df.drop(columns=[c for c in df.columns
                           if c is None or str(c).startswith("Unnamed:")],
                 errors="ignore")
    missing = [c for c in expected if c not in df.columns]
    if missing:
        raise ValueError(
            f"{path.name} [{sheet}]: expected column(s) missing: {missing} — "
            "schema drift. Failing the load, not the DB (R3).")
    return df[expected]
