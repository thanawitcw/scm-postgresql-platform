"""HISTORY LOADER — goods receipt (S5). NOT part of the daily pipeline.

One-time / migration use only: loads ALL *_Summary_GR.csv annual files via
the same fact_goods_receipt pipeline (full replace, same guards, same run
log). Run by hand:

    python -m etl.history.goods_receipt

It is deliberately NOT a typer command in etl.cli — the daily command
(load-gr, latest year only) can never trigger a backfill.
"""

from __future__ import annotations

import sys
from datetime import date as _date
from functools import partial
from pathlib import Path

from etl.db.engine import get_engine
from etl.pipelines import fact_goods_receipt
from etl.pipelines.runner import extract_guarded, run_pipeline
from etl.runlog import content_hash, upsert_dataset
from etl.sources.csv_goods_receipt import read_gr_file, source_dir
from etl.sources.scan import iter_data_files


def main() -> None:
    engine = get_engine()
    directory = source_dir()

    def _extract():
        files = list(iter_data_files(directory, "*_Summary_GR.csv"))
        if not files:
            raise ValueError(f"no Summary_GR files in {directory}")
        frames = []
        for path in files:
            df, uploaded_at = read_gr_file(path)
            frames.append((df, uploaded_at, str(path)))
        return frames

    frames = extract_guarded(
        engine, "fact_goods_receipt_hist", "core.fact_goods_receipt",
        _date.today(), _extract,
        f"S5 history ({source_dir()}) via csv_goods_receipt.read_gr_file")
    run_id = run_pipeline(engine, "fact_goods_receipt_hist",
                          "core.fact_goods_receipt", _date.today(),
                          partial(fact_goods_receipt.run, frames=frames))
    for df, uploaded_at, spath in frames:
        p = Path(spath)
        upsert_dataset(engine, "S5", "Data_GR_CJ", spath, p.name, uploaded_at,
                       df["report_date"].min(), df["report_date"].max(),
                       content_hash(p), run_id)
    print(f"OK files={[Path(s).name for _, _, s in frames]} "
          f"rows={sum(len(df) for df, _, _ in frames)}")


if __name__ == "__main__":
    sys.exit(main())
