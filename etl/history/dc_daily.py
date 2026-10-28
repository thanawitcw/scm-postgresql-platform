"""HISTORY LOADER — DC daily stock (S7). NOT part of the daily pipeline.

One-time / migration use only: scans the 2025/2026 history folders (+ root
dailies) and loads every file-date missing from the fact, via the same
fact_daily_dc_stock pipeline (same guards, same run log). Run by hand:

    python -m etl.history.dc_daily

It is deliberately NOT a typer command in etl.cli — the daily command
(load-dc-stock --date, root scan only) can never trigger a backfill.
"""

from __future__ import annotations

import sys
from datetime import date as _date
from functools import partial
from pathlib import Path

from sqlalchemy import text

from etl.db.engine import get_engine
from etl.pipelines import fact_daily_dc_stock
from etl.pipelines.runner import extract_guarded, run_pipeline
from etl.runlog import upsert_dataset
from etl.sources.xlsx_dc_daily import (
    FILENAME_RE,
    KNOWN_NON_DATA,
    file_mtime,
    file_stock_date,
    read_dc_file,
    source_dirs,
    source_root,
)


def main() -> None:
    engine = get_engine()
    year_dirs = source_dirs()

    def _plan():
        all_files = ([p for p in sorted(source_root().glob("*.xlsx"))
                      if p.is_file()]
                     + [p for d in year_dirs for p in sorted(d.rglob("*.xlsx"))])
        all_files = [p for p in all_files
                     if not p.name.startswith("~$")
                     and p.name not in KNOWN_NON_DATA]
        bad = [p.name for p in all_files if not FILENAME_RE.match(p.name)]
        if bad:
            raise ValueError(f"non-snapshot xlsx in history scan: {bad}")
        with engine.connect() as conn:
            have = {r[0] for r in conn.execute(
                text("SELECT DISTINCT stock_date FROM core.fact_daily_dc_stock")).all()}
        return [p for p in all_files if file_stock_date(p) not in have]

    missing = extract_guarded(
        engine, "fact_daily_dc_stock_hist", "core.fact_daily_dc_stock",
        _date.today(), _plan, "S7 history scan (2025/2026 folders + root)")
    if not missing:
        print("SKIP all DC daily snapshots already loaded")
        return

    def _extract():
        frames = []
        for path in missing:
            df = read_dc_file(path)
            frames.append((df, file_mtime(path), str(path)))
        return frames

    frames = extract_guarded(
        engine, "fact_daily_dc_stock_hist", "core.fact_daily_dc_stock",
        _date.today(), _extract,
        f"S7 history ({len(missing)} files) via xlsx_dc_daily.read_dc_file")
    run_id = run_pipeline(engine, "fact_daily_dc_stock_hist",
                          "core.fact_daily_dc_stock", _date.today(),
                          partial(fact_daily_dc_stock.run, frames=frames))
    upsert_dataset(engine, "S7", "DC_DailyStock",
                   str(source_root()), None, None,
                   min(df["stock_date"].min() for df, _, _ in frames),
                   max(df["stock_date"].max() for df, _, _ in frames),
                   None, run_id)
    print(f"OK files={len(frames)} "
          f"rows={sum(len(df) for df, _, _ in frames)}")


if __name__ == "__main__":
    sys.exit(main())
