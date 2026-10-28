"""HISTORY LOADER — CJ/TD sellout (S3/S4). NOT part of the daily pipeline.

One-time / migration use only: loads every monthly file whose
(channel, month) window is missing from the fact, via the same
fact_daily_sales pipeline (Pattern A per month, same guards, same run
log). Run by hand:

    python -m etl.history.sellout

It is deliberately NOT a typer command in etl.cli — the daily commands
(load-latest per channel) and the deliberate per-month backfill tool
(load-month) stay the only scheduled/manual paths.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

from sqlalchemy import text

from etl.cli import _do_load_month
from etl.db.engine import get_engine
from etl.sources.csv_sellout import source_dir as cj_dir
from etl.sources.csv_sellout_td import source_dir as td_dir


def _file_months(directory: Path) -> dict[tuple[int, int], Path]:
    """(year, mon) -> file. Exact-prefix parse (Jan glob trap)."""
    out = {}
    for p in sorted(directory.glob("*.Daily_*.csv")):
        m = re.match(r"(\d+)_(\d+)\.Daily_", p.name)
        if m:
            out[(int(m.group(1)), int(m.group(2)))] = p
    return out


def main() -> None:
    engine = get_engine()
    with engine.connect() as conn:
        have = {(r[0], r[1].year, r[1].month) for r in conn.execute(text(
            "SELECT ch.sales_channel, date_trunc('month', f.sales_date)::date "
            "FROM core.fact_daily_sales f "
            "JOIN core.dim_channel ch ON ch.channel_key = f.channel_key "
            "GROUP BY 1, 2")).all()}
    total = 0
    for channel, directory in (("CJ", cj_dir()), ("TD", td_dir())):
        missing = sorted(ym for ym in _file_months(directory)
                         if (channel, ym[0], ym[1]) not in have)
        for year, mon in missing:
            rows = _do_load_month(channel, year, mon, directory)
            print(f"OK channel={channel} month={year}-{mon:02d} rows={rows}",
                  flush=True)
            total += 1
    if not total:
        print("SKIP all sellout months already loaded")


if __name__ == "__main__":
    sys.exit(main())
