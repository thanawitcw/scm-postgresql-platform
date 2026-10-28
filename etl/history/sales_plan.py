"""HISTORY LOADER — sales plan (S10). NOT part of the daily pipeline.

One-time / migration use only: loads ALL snapshot files via the same
fact_sales_plan pipeline (append mode, key-exact replace, same guards,
same run log).
Run by hand:

    python -m etl.history.sales_plan

It is deliberately NOT a typer command in etl.cli — the daily command
(load-plan, latest snapshot only) can never trigger a backfill.
"""

from __future__ import annotations

import sys
from datetime import date as _date
from functools import partial
from pathlib import Path

from etl.db.engine import get_engine
from etl.pipelines import fact_demandfc_plan
from etl.pipelines.runner import extract_guarded, run_pipeline
from etl.runlog import content_hash, upsert_dataset
from etl.sources.csv_sales_plan import read_plan_file, source_dir
from etl.sources.scan import iter_data_files


def main() -> None:
    engine = get_engine()
    directory = source_dir()

    def _extract():
        files = list(iter_data_files(
            directory, "*.RunningProGrid_AsOf_FCMonth.csv"))
        if not files:
            raise ValueError(f"no plan snapshots in {directory}")
        frames = []
        for path in files:
            df, uploaded_at = read_plan_file(path)
            frames.append((df, uploaded_at, str(path)))
        return frames

    frames = extract_guarded(
        engine, "fact_sales_plan_hist", "core.fact_demandfc_history",
        _date.today(), _extract,
        f"S10 history ({source_dir()}) via csv_sales_plan.read_plan_file")
    run_id = run_pipeline(engine, "fact_sales_plan_hist",
                          "core.fact_demandfc_history", _date.today(),
                          partial(fact_demandfc_plan.run, frames=frames))
    for df, uploaded_at, spath in frames:
        p = Path(spath)
        upsert_dataset(engine, "S10", "RunningProGrid_AsOf_FCMonth",
                       spath, p.name, uploaded_at,
                       df["sales_plan_date"].min(), df["sales_plan_date"].max(),
                       content_hash(p), run_id)
    print(f"OK files={[Path(s).name for _, _, s in frames]} "
          f"rows={sum(len(df) for df, _, _ in frames)}")


if __name__ == "__main__":
    sys.exit(main())
