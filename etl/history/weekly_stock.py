"""HISTORY LOADER — weekly stock (S11). NOT part of the daily pipeline.

One-time / migration use only: loads every weekly snapshot whose week is
missing from the fact, via the same append-only fact_weekly_stock pipeline
(same guards, same run log). Run by hand:

    python -m etl.history.weekly_stock

It is deliberately NOT a typer command in etl.cli — the daily command
(load-weekly-stock, latest snapshot only) can never trigger a backfill.
"""

from __future__ import annotations

import sys
from datetime import date as _date
from functools import partial
from pathlib import Path

from sqlalchemy import text

from etl.db.engine import get_engine
from etl.pipelines import fact_weekly_stock
from etl.pipelines.runner import extract_guarded, run_pipeline
from etl.runlog import content_hash, upsert_dataset
from etl.sources.xlsx_weekly_stock import (
    read_weekly_file, source_dir, stock_date_from_name)
from etl.sources.scan import iter_data_files


def main() -> None:
    engine = get_engine()
    directory = source_dir()

    def _plan():
        files = list(iter_data_files(directory, "*.xlsx"))
        if not files:
            raise ValueError(f"no weekly snapshots in {directory}")
        with engine.connect() as conn:
            have = {r[0] for r in conn.execute(
                text("SELECT DISTINCT stock_date FROM core.fact_weekly_stock")).all()}
        return [p for p in files
                if stock_date_from_name(p.name) not in have]

    missing = extract_guarded(
        engine, "fact_weekly_stock_hist", "core.fact_weekly_stock",
        _date.today(), _plan, f"S11 history scan in {directory}")
    if not missing:
        print("SKIP all weekly snapshots already loaded")
        return

    def _extract():
        frames = []
        for path in missing:
            df = read_weekly_file(path)
            frames.append((df, _date.today(), str(path)))
        return frames

    frames = extract_guarded(
        engine, "fact_weekly_stock_hist", "core.fact_weekly_stock",
        _date.today(), _extract,
        f"S11 history ({len(missing)} files) via xlsx_weekly_stock.read_weekly_file")
    run_id = run_pipeline(engine, "fact_weekly_stock_hist",
                          "core.fact_weekly_stock", _date.today(),
                          partial(fact_weekly_stock.run, frames=frames))
    for df, uploaded_at, spath in frames:
        p = Path(spath)
        upsert_dataset(engine, "S11", "Weekly_Stock", spath, p.name, uploaded_at,
                       df["stock_date"].min(), df["stock_date"].max(),
                       content_hash(p), run_id)
    print(f"OK weeks={len(frames)} rows={sum(len(df) for df, _, _ in frames)}")


if __name__ == "__main__":
    sys.exit(main())
