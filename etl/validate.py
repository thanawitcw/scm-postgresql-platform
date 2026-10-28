"""Pre-load validation: prove the sources are clean BEFORE any load writes.

Every check is read-only (no DB writes, no archive, no snapshot). A FAIL
names the file and the exact problem so it can be fixed at source first.
Run by hand before loads, or after any upstream delivery::

    python -m etl validate
"""

from __future__ import annotations


def check_access() -> str:
    """S8/S9: file present, both tables readable, rename contract holds."""
    from etl.sources.access_product_master import (
        ALLOWLIST,
        TABLE_PRODUCT,
        TABLE_SUPPLIER,
        AccessProductMaster,
        extract_accdb_table,
        frame_product,
        frame_supplier,
    )

    src = AccessProductMaster()._source_path()
    assert src.name in ALLOWLIST, f"{src.name} not in allowlist"
    assert src.exists(), f"missing: {src}"
    prod = frame_product(extract_accdb_table(src, TABLE_PRODUCT))
    supp = frame_supplier(extract_accdb_table(src, TABLE_SUPPLIER))
    return (f"{src.name}: product={len(prod)} rows, "
            f"supplier={len(supp)} rows, rename contract holds")


def check_sellout(channel: str) -> str:
    """S3/S4: latest file parses, channel constant, dates in-month."""
    from etl.sources.csv_sellout import CsvSellout, source_dir as cj_dir
    from etl.sources.csv_sellout_td import CsvSelloutTd, source_dir as td_dir

    loader = CsvSellout() if channel == "CJ" else CsvSelloutTd()
    directory = cj_dir() if channel == "CJ" else td_dir()
    files = sorted(p for p in directory.glob("*.Daily_*.csv")
                   if not p.name.startswith("~$"))
    import re
    dated = []
    for p in files:
        m = re.match(r"(\d+)_(\d+)\.Daily_", p.name)
        if m:
            dated.append(((int(m.group(1)), int(m.group(2))), p))
    (year, mon), path = max(dated)
    df, _ = loader.load_month_file(path)
    dmin, dmax = df["sales_date"].min(), df["sales_date"].max()
    assert (dmin.year, dmin.month) == (year, mon), f"dates {dmin}..{dmax} outside file month"
    return (f"{path.name}: {len(df)} rows, {dmin}..{dmax}, "
            f"channel={channel} asserted")


def check_progrid() -> str:
    """S1/S2: newest daily CSVs parse (strict dates/prices), cancellation folds."""
    from etl.sources import csv_promotion_grid, csv_promotion_grid_pcb

    s1_path = csv_promotion_grid.latest_file()
    s2_path = csv_promotion_grid_pcb.latest_file()
    s1 = csv_promotion_grid.read_file(s1_path)
    s2 = csv_promotion_grid_pcb.read_file(s2_path)
    return (f"S1 {s1_path.name}={len(s1)} rows "
            f"({int(s1['is_cancelled'].sum())} cancelled), "
            f"S2 {s2_path.name}={len(s2)} rows "
            f"({int(s2['is_cancelled'].sum())} cancelled)")


def check_gr() -> str:
    """S5: latest annual file parses, ISO dates, headers stripped."""
    import re
    from etl.sources.csv_goods_receipt import read_gr_file, source_dir
    from etl.sources.scan import iter_data_files

    cands = []
    for p in iter_data_files(source_dir(), "*_Summary_GR.csv"):
        m = re.match(r"(\d{4})_Summary_GR\.csv$", p.name)
        if m:
            cands.append((int(m.group(1)), p))
    year, path = max(cands)
    df, _ = read_gr_file(path)
    return (f"{path.name}: {len(df)} rows, "
            f"{df['report_date'].min()}..{df['report_date'].max()}, "
            f"DCs={sorted(df['gr_dc'].unique().tolist())}")


def check_weekly() -> str:
    """S11: latest snapshot parses, Monday date, grain holds."""
    from etl.sources.xlsx_weekly_stock import (
        read_weekly_file, source_dir, stock_date_from_name)
    from etl.sources.scan import iter_data_files

    files = list(iter_data_files(source_dir(), "*.xlsx"))
    _, path = max((stock_date_from_name(p.name), p) for p in files)
    df = read_weekly_file(path)
    return (f"{path.name}: {len(df)} rows, week={df['stock_date'].iloc[0]}, "
            f"dupes_dropped={df.attrs.get('exact_dupes_dropped', 0)}")


def check_dc() -> str:
    """S7: latest root snapshot parses, plants known, ISO output."""
    from etl.sources.xlsx_dc_daily import (
        FILENAME_RE, file_stock_date, read_dc_file, source_root)

    files = [p for p in sorted(source_root().glob("*.xlsx")) if p.is_file()
             and not p.name.startswith("~$") and FILENAME_RE.match(p.name)]
    _, path = max((file_stock_date(p), p) for p in files)
    df = read_dc_file(path)
    return (f"{path.name}: {len(df)} rows, date={df['stock_date'].iloc[0]}, "
            f"plants={sorted(df['plant'].unique().tolist())}, "
            f"qty_min={df['stock_qty'].min()}")


def check_plan() -> str:
    """S10: latest snapshot parses, grain holds, no transform applied."""
    import re
    from etl.sources.csv_sales_plan import read_plan_file, source_dir
    from etl.sources.scan import iter_data_files

    cands = []
    for p in iter_data_files(source_dir(), "*.RunningProGrid_AsOf_FCMonth.csv"):
        m = re.match(r"(\d{4})_(\d{2})\.RunningProGrid_AsOf_FCMonth\.csv$", p.name)
        if m:
            cands.append(((int(m.group(1)), int(m.group(2))), p))
    _, path = max(cands)
    df, _ = read_plan_file(path)
    return (f"{path.name}: {len(df)} rows, "
            f"{df['sales_plan_date'].min()}..{df['sales_plan_date'].max()}")


def check_oos() -> str:
    """S12: latest snapshot parses (all date shapes), grain holds."""
    from etl.sources.xlsx_oos_status import (
        file_stock_date, read_oos_file, source_dir)
    from etl.sources.scan import iter_data_files

    files = list(iter_data_files(source_dir(), "*.xlsx"))
    _, path = max((file_stock_date(p.name), p) for p in files)
    df = read_oos_file(path)
    return (f"{path.name}: {len(df)} rows, week={df['stock_date'].iloc[0]}")


CHECKS = [
    ("S8/S9 master", check_access),
    ("S3 CJ sellout", lambda: check_sellout("CJ")),
    ("S4 TD sellout", lambda: check_sellout("TD")),
    ("S1/S2 progrid", check_progrid),
    ("S5 goods receipt", check_gr),
    ("S11 weekly stock", check_weekly),
    ("S7 DC daily", check_dc),
    ("S10 sales plan", check_plan),
    ("S12 OOS status", check_oos),
]


def main() -> int:
    fails = []
    for label, fn in CHECKS:
        try:
            detail = fn()
        except Exception as e:  # noqa: BLE001 — the point is the report
            print(f"FAIL {label}: {type(e).__name__}: {e}", flush=True)
            fails.append(label)
        else:
            print(f"PASS {label}: {detail}", flush=True)
    print(f"validate: {len(CHECKS) - len(fails)}/{len(CHECKS)} PASS"
          + (f" — FAILED: {fails}" if fails else ""))
    return 1 if fails else 0
