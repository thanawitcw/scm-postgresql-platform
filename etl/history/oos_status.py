"""HISTORY LOADER — OOS stock status (S12). NOT part of the daily pipeline.

One-time / migration use only: loads every snapshot whose week is missing
from the fact, via the same fact_oos_status pipeline (same guards, same
run log). Run by hand:

    python -m etl.history.oos_status

It is deliberately NOT a typer command in etl.cli — the daily command
(load-oos-status, latest snapshot only) can never trigger a backfill.
Re-uploads of single files go through load-oos-status --file.
"""

from __future__ import annotations

import sys
from datetime import date as _date
from functools import partial
from pathlib import Path

from sqlalchemy import text

from etl.db.engine import get_engine
from etl.pipelines import fact_oos_status
from etl.pipelines.runner import extract_guarded, run_pipeline
from etl.runlog import content_hash, upsert_dataset
from etl.sources.xlsx_oos_status import (
    file_stock_date, read_oos_file, source_dir)


def main() -> None:
    engine = get_engine()
    directory = source_dir()

    def _plan():
        files = sorted(p for p in directory.glob("*.xlsx")
                       if not p.name.startswith("~$"))
        if not files:
            raise ValueError(f"no snapshots in {directory}")
        with engine.connect() as conn:
            have = {r[0] for r in conn.execute(
                text("SELECT DISTINCT stock_date FROM core.fact_oos_status")).all()}
        return [p for p in files if file_stock_date(p.name) not in have]

    missing = extract_guarded(
        engine, "fact_oos_status_hist", "core.fact_oos_status",
        _date.today(), _plan, f"S12 history scan in {directory}")
    if not missing:
        print("SKIP all OOS snapshots already loaded")
        return

    def _extract():
        frames = []
        for path in missing:
            df = read_oos_file(path)
            frames.append((df, _date.today(), str(path)))
        return frames

    frames = extract_guarded(
        engine, "fact_oos_status_hist", "core.fact_oos_status",
        _date.today(), _extract,
        f"S12 history ({len(missing)} files) via xlsx_oos_status.read_oos_file")
    run_id = run_pipeline(engine, "fact_oos_status_hist",
                          "core.fact_oos_status", _date.today(),
                          partial(fact_oos_status.run, frames=frames))
    for df, uploaded_at, spath in frames:
        p = Path(spath)
        upsert_dataset(engine, "S12", "OOS_Stock_Status", spath, p.name,
                       uploaded_at, df["stock_date"].min(),
                       df["stock_date"].max(), content_hash(p), run_id)
    print(f"OK files={len(frames)} rows={sum(len(df) for df, _, _ in frames)}")


if __name__ == "__main__":
    sys.exit(main())
