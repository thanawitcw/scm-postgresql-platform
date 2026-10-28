"""Pipeline entrypoint. `python -m etl run --date YYYY-MM-DD` — idempotent, re-runnable."""

from __future__ import annotations

import typer

app = typer.Typer(no_args_is_help=True)


@app.command()
def health():
    """Check database connectivity."""
    from sqlalchemy import text

    from etl.db.engine import get_engine

    with get_engine().connect() as conn:
        typer.echo(f"OK db_time={conn.execute(text('SELECT now()')).scalar()}")


@app.command(name="validate")
def validate():
    """Pre-load check: validate every source WITHOUT writing anything.

    Prints PASS/FAIL per datasource with details. Fix FAILs at source
    first, then load. Exit 1 if anything fails.
    """
    from etl.validate import main

    raise typer.Exit(code=main())


@app.command()
def run(run_date: str = typer.Option(..., "--date", help="Business date to process")):
    """Run order (dependency DAG, §13): dim_date + dim_dc, then dim_supplier, then dim_item."""
    from datetime import date as _date
    from functools import partial
    from pathlib import Path

    from etl.db.engine import get_engine
    from etl.pipelines import dim_date, dim_dc, dim_item, dim_supplier
    from etl.pipelines.runner import extract_guarded, run_pipeline
    from etl.runlog import content_hash, upsert_dataset
    from etl.sources.access_product_master import AccessProductMaster

    parsed = _date.fromisoformat(run_date)
    engine = get_engine()

    run_pipeline(engine, "dim_date", "core.dim_date", parsed, dim_date.run)
    run_pipeline(engine, "dim_dc", "core.dim_dc", parsed, dim_dc.run)

    src = AccessProductMaster()
    frames = extract_guarded(
        engine, "dim_supplier", "core.dim_supplier", parsed,
        lambda: dict(src.extract(parsed)),  # one accdb copy per table
        "S8/S9 SHM_TMS_001_AllUser_New.accdb via AccessProductMaster")
    meta = src.dataset_meta()
    digest = content_hash(Path(meta["file_path"]))

    run_pipeline(engine, "dim_supplier", "core.dim_supplier", parsed,
                 partial(dim_supplier.run, df=frames["supplier"],
                         source_file=meta["file_path"]))
    upsert_dataset(engine, "S9", "tbl_Master_004_Supplier_List", meta["file_path"],
                   meta["file_name"], meta["uploaded_at"], None, None, digest, None)

    run_pipeline(engine, "dim_item", "core.dim_item", parsed,
                 partial(dim_item.run, df=frames["product"],
                         source_file=meta["file_path"]))
    upsert_dataset(engine, "S8", "tbl_Master_003_Product_List", meta["file_path"],
                   meta["file_name"], meta["uploaded_at"], None, None, digest, None)

    typer.echo(f"OK date={parsed} datasets=S8,S9")


def _resolve_month_file(directory, year: int, mon: int):
    """Exactly one file per (year, month). Exact-prefix match (Jan glob trap)."""
    matches = sorted(p for p in directory.glob(f"{year}_*.Daily_*.csv")
                     if p.name.startswith(f"{year}_{mon}.Daily_"))
    if len(matches) != 1:
        raise typer.BadParameter(
            f"expected exactly 1 file for {year}_{mon} in {directory}, found: "
            f"{[p.name for p in matches]}"
        )
    return matches[0]


def _do_load_month(channel: str, year: int, mon: int, directory, path=None) -> int:
    """Shared single-month load (Pattern A, idempotent). Returns row count.

    File resolution + read + month check all run inside extract_guarded,
    so a missing/drifted file becomes a FAILED etl_run row naming the
    datasource — never a silent traceback.
    """
    from functools import partial

    from etl.db.engine import get_engine
    from etl.pipelines import fact_daily_sales
    from etl.pipelines.runner import extract_guarded, run_pipeline
    from etl.runlog import content_hash, upsert_dataset
    from etl.sources.csv_sellout import CsvSellout
    from etl.sources.csv_sellout_td import CsvSelloutTd

    loader = CsvSellout() if channel == "CJ" else CsvSelloutTd()
    engine = get_engine()
    run_date = fact_daily_sales.month_window(year, mon)[1]

    def _extract():
        p = path or _resolve_month_file(directory, year, mon)
        df, uploaded_at = loader.load_month_file(p)
        dmin, dmax = df["sales_date"].min(), df["sales_date"].max()
        if (dmin.year, dmin.month) != (year, mon) or (dmax.year, dmax.month) != (year, mon):
            raise typer.BadParameter(
                f"{p.name}: dates {dmin}..{dmax} outside {year}-{mon:02d} — "
                "wrong file or mis-parsed format."
            )
        return df, uploaded_at, p

    df, uploaded_at, path = extract_guarded(
        engine, f"fact_daily_sales_{channel.lower()}", "core.fact_daily_sales",
        run_date, _extract, f"S3/S4 {year}-{mon:02d} in {directory}")
    dmin, dmax = df["sales_date"].min(), df["sales_date"].max()
    run_pipeline(engine, f"fact_daily_sales_{channel.lower()}", "core.fact_daily_sales",
                 run_date, partial(fact_daily_sales.run, df=df, channel=channel,
                                   year=year, month=mon, uploaded_at=uploaded_at,
                                   source_file=str(path)))
    content = content_hash(path)
    upsert_dataset(engine, "S3" if channel == "CJ" else "S4",
                   "CJ_Sellout_with_promotion_name" if channel == "CJ" else "TD_SellOut",
                   str(path), path.name, uploaded_at, dmin, dmax, content, None)
    return len(df)


def _latest_month_file(directory):
    """Latest file by (year, month) parsed from the YYYY_M.Daily_ prefix.

    Business-month order, never file mtime (mtime is meaningless, §10.1).
    """
    import re

    best = None
    for p in directory.glob("*.Daily_*.csv"):
        m = re.match(r"(\d+)_(\d+)\.Daily_", p.name)
        if m:
            key = (int(m.group(1)), int(m.group(2)))
            if best is None or key > best[0]:
                best = (key, p)
    if best is None:
        raise typer.BadParameter(f"no monthly files in {directory}")
    return best[0][0], best[0][1], best[1]


@app.command(name="load-month")
def load_month(
    channel: str = typer.Option(..., "--channel", help="CJ or TD"),
    month: str = typer.Option(None, "--month", help="YYYY-MM (or use --file)"),
    file: str = typer.Option(None, "--file", help="Exact filename, e.g. 2026_9.Daily_Sep.csv"),
):
    """Load one sellout month file (S3/S4) into fact_daily_sales. Idempotent.

    One-off backfill tool ( steady state uses load-latest).
    Give --month OR --file (exact name in that channel's folder).
    """
    import re
    from etl.sources.csv_sellout import source_dir as cj_dir
    from etl.sources.csv_sellout_td import source_dir as td_dir

    channel = channel.upper()
    if channel not in ("CJ", "TD"):
        raise typer.BadParameter("channel must be CJ or TD")
    directory = cj_dir() if channel == "CJ" else td_dir()
    if file is not None:
        path = directory / file
        if not path.is_file():
            raise typer.BadParameter(f"no file named {file!r} in {directory}")
        m = re.match(r"(\d+)_(\d+)\.Daily_", path.name)
        if not m:
            raise typer.BadParameter(f"{file!r} is not a YYYY_M.Daily_*.csv file")
        year, mon = int(m.group(1)), int(m.group(2))
    elif month is not None:
        year, mon = map(int, month.split("-"))
        path = None
    else:
        raise typer.BadParameter("give --month YYYY-MM or --file <name>")
    rows = _do_load_month(channel, year, mon, directory, path)
    typer.echo(f"OK channel={channel} month={year}-{mon:02d} rows={rows}")


@app.command(name="load-latest")
def load_latest(
    channel: str = typer.Option(..., "--channel", help="CJ or TD"),
):
    """Steady-state sellout load: the single latest month file only.

    Skips when the file is byte-identical to the recorded dataset
    (same name + sha256) — a re-run with no new delivery is a no-op, never
    a backfill. Backfills stay a deliberate per-month load-month decision.
    """
    from datetime import date as _date

    from etl.db.engine import get_engine
    from etl.pipelines.runner import extract_guarded
    from etl.runlog import content_hash, get_dataset
    from etl.sources.csv_sellout import source_dir as cj_dir
    from etl.sources.csv_sellout_td import source_dir as td_dir

    channel = channel.upper()
    if channel not in ("CJ", "TD"):
        raise typer.BadParameter("channel must be CJ or TD")
    directory = cj_dir() if channel == "CJ" else td_dir()
    engine = get_engine()
    year, mon, path = extract_guarded(
        engine, f"fact_daily_sales_{channel.lower()}", "core.fact_daily_sales",
        _date.today(), lambda: _latest_month_file(directory),
        f"S3/S4 latest-file scan in {directory}")
    seen = get_dataset(engine, "S3" if channel == "CJ" else "S4")
    if seen is not None and seen["file_name"] == path.name \
            and seen["content_hash"] == content_hash(path):
        typer.echo(f"SKIP channel={channel} month={year}-{mon:02d} "
                   f"file={path.name} unchanged")
        return
    rows = _do_load_month(channel, year, mon, directory, path)
    typer.echo(f"OK channel={channel} month={year}-{mon:02d} rows={rows}")


@app.command(name="load-progrid")
def load_progrid():
    """Archive newest S1+S2 CSVs, then rebuild fact_running_progrid + change log.

    Both sources publish one CSV per business date (``*_YYYYMMDD.csv``) into
    accumulating folders. Only the newest file per source is archived and
    loaded (business order from the filename, never mtime). When both newest
    files are byte-identical to the recorded load, the run is a no-op.
    """
    from datetime import datetime
    from functools import partial

    from etl.db.engine import get_engine
    from etl.pipelines import fact_running_progrid
    from etl.pipelines.runner import extract_guarded, run_pipeline
    from etl.runlog import content_hash, get_dataset, upsert_dataset
    from etl.sources.progrid_archive import (
        SOURCES, archive_upload, select_latest, snapshot_root)

    engine = get_engine()

    paths = extract_guarded(
        engine, "fact_running_progrid", "core.fact_running_progrid",
        datetime.now().date(), select_latest,
        "S1/S2 latest-file scan (ProGrid_Data_YYYYMMDD.csv, "
        "ProGrid_PCB_YYYYMMDD.csv)")
    seen = {key: get_dataset(engine, key) for key in paths}
    if all(seen[key] is not None
           and seen[key]["file_name"] == p.name
           and seen[key]["content_hash"] == content_hash(p)
           for key, p in paths.items()):
        typer.echo("SKIP S1/S2 progrid unchanged: "
                   + ", ".join(f"{k}={p.name}" for k, p in paths.items()))
        return

    def _extract():
        manifest = archive_upload(paths)  # validates BEFORE promoting; aborts on drift
        snap = snapshot_root() / manifest["uploaded_at"]
        uploaded_at = datetime.strptime(
            manifest["uploaded_at"], "%Y%m%dT%H%M%S").astimezone()
        frames = {
            key: SOURCES[key].read_file(snap / manifest["files"][key]["file_name"])
            for key in paths
        }
        return manifest, snap, uploaded_at, frames

    manifest, snap, uploaded_at, frames = extract_guarded(
        engine, "fact_running_progrid", "core.fact_running_progrid",
        datetime.now().date(), _extract,
        "S1/S2 progrid archive via progrid_archive.archive_upload")
    run_pipeline(engine, "fact_running_progrid", "core.fact_running_progrid",
                 uploaded_at.date(),
                 partial(fact_running_progrid.run, frames=frames,
                         uploaded_at=uploaded_at, manifest=manifest))
    names = {"S1": "ProGrid_Data", "S2": "ProGrid_PCB"}
    for key in ("S1", "S2"):
        f = manifest["files"][key]
        df = frames[key]
        upsert_dataset(engine, key, names[key], str(snap / f["file_name"]),
                       f["file_name"], uploaded_at,
                       df["valid_from"].min(), df["valid_to"].max(),
                       f["sha256"], None)
    typer.echo(f"OK snapshot={manifest['uploaded_at']} "
               f"rows={sum(len(v) for v in frames.values())}")


@app.command(name="load-gr")
def load_gr(
    file: str = typer.Option(None, "--file", help="Exact annual filename, e.g. 2026_Summary_GR.csv"),
):
    """Goods receipt steady state (S5): the single latest annual file only.

    Latest = max YYYY in *_Summary_GR.csv. Skips when byte-identical to the
    recorded dataset (same name + sha256). History lives in
    etl.history.goods_receipt (excluded from daily running, like DC history).
    With --file: reload exactly that annual file.
    """
    import re
    from datetime import date as _date
    from functools import partial
    from pathlib import Path

    from etl.db.engine import get_engine
    from etl.pipelines import fact_goods_receipt
    from etl.pipelines.runner import extract_guarded, run_pipeline
    from etl.runlog import content_hash, get_dataset, upsert_dataset
    from etl.sources.csv_goods_receipt import read_gr_file, source_dir
    from etl.sources.scan import iter_data_files

    engine = get_engine()
    directory = source_dir()

    def _extract():
        if file is not None:
            path = directory / file
            if not path.is_file():
                raise typer.BadParameter(f"no file named {file!r} in {directory}")
            m = re.match(r"(\d{4})_Summary_GR\.csv$", path.name)
            if not m:
                raise typer.BadParameter(
                    f"{path.name}: not a YYYY_Summary_GR.csv file.")
            year = int(m.group(1))
        else:
            cands = []
            for p in iter_data_files(directory, "*_Summary_GR.csv"):
                m = re.match(r"(\d{4})_Summary_GR\.csv$", p.name)
                if not m:
                    raise typer.BadParameter(
                        f"{p.name}: not a YYYY_Summary_GR.csv file.")
                cands.append((int(m.group(1)), p))
            if not cands:
                raise typer.BadParameter(f"no Summary_GR files in {directory}")
            year, path = max(cands)
        df, uploaded_at = read_gr_file(path)
        return year, path, df, uploaded_at

    year, path, df, uploaded_at = extract_guarded(
        engine, "fact_goods_receipt", "core.fact_goods_receipt",
        _date.today(), _extract, f"S5 latest-file scan in {directory}")
    seen = get_dataset(engine, "S5")
    if seen is not None and seen["file_name"] == path.name \
            and seen["content_hash"] == content_hash(path):
        typer.echo(f"SKIP year={year} file={path.name} unchanged")
        return
    frames = [(df, uploaded_at, str(path))]
    run_id = run_pipeline(engine, "fact_goods_receipt", "core.fact_goods_receipt",
                          _date.today(),
                          partial(fact_goods_receipt.run, frames=frames))
    upsert_dataset(engine, "S5", "Data_GR_CJ", str(path), path.name, uploaded_at,
                   df["report_date"].min(), df["report_date"].max(),
                   content_hash(path), run_id)
    typer.echo(f"OK year={year} file={path.name} rows={len(df)}")


@app.command(name="load-weekly-stock")
def load_weekly_stock(
    file: str = typer.Option(None, "--file", help="Exact snapshot filename for single-week re-upload"),
):
    """Weekly stock steady state (S11): the single latest snapshot only.

    Latest = max DD-MM-YYYY in the filename. Skips when that week is already
    loaded. History lives in etl.history.weekly_stock (excluded from daily
    running). With --file: reload exactly that week (week-scoped delete +
    insert — deliberate re-upload path around the append guard).
    """
    from datetime import date as _date
    from functools import partial
    from pathlib import Path

    from etl.db.engine import get_engine
    from etl.pipelines import fact_weekly_stock
    from etl.pipelines.runner import extract_guarded, run_pipeline
    from etl.runlog import content_hash, get_dataset, upsert_dataset
    from etl.sources.xlsx_weekly_stock import (
        read_weekly_file, source_dir, stock_date_from_name)
    from etl.sources.scan import iter_data_files

    engine = get_engine()
    directory = source_dir()

    def _extract():
        if file is not None:
            path = directory / file
            if not path.is_file():
                raise typer.BadParameter(f"no snapshot named {file!r}")
            return path, read_weekly_file(path)
        files = list(iter_data_files(directory, "*.xlsx"))
        if not files:
            raise typer.BadParameter(f"no weekly snapshots in {directory}")
        dated = [(stock_date_from_name(p.name), p) for p in files]
        _, path = max(dated)
        return path, read_weekly_file(path)

    path, df = extract_guarded(
        engine, "fact_weekly_stock", "core.fact_weekly_stock",
        _date.today(), _extract,
        f"S11 {'re-upload ' + file if file else 'latest-file scan'}")
    from sqlalchemy import text as _text
    with engine.connect() as _conn:
        _weeks = {r[0] for r in _conn.execute(
            _text("SELECT DISTINCT stock_date FROM core.fact_weekly_stock")).all()}
    week = df["stock_date"].iloc[0]
    is_single = file is not None
    if not is_single:
        seen = get_dataset(engine, "S11")
        if week in _weeks:
            same = seen is not None and seen["file_name"] == path.name \
                and seen["content_hash"] == content_hash(path)
            typer.echo(f"SKIP week={week} file={path.name} already loaded"
                       + ("" if same else " (content differs from recorded hash — "
                          "corrections need a deliberate reload, not a silent rewrite)"))
            return
    frames = [(df, _date.today(), str(path))]
    run_id = run_pipeline(engine, "fact_weekly_stock", "core.fact_weekly_stock",
                          _date.today(),
                          partial(fact_weekly_stock.run, frames=frames,
                                  scope="week" if is_single else "append"))
    upsert_dataset(engine, "S11", "Weekly_Stock", str(path), path.name,
                   None, df["stock_date"].min(), df["stock_date"].max(),
                   content_hash(path), run_id)
    typer.echo(f"OK week={df['stock_date'].iloc[0]} rows={len(df)}")


@app.command(name="load-dc-stock")
def load_dc_stock(
    file: str = typer.Option(..., "--file", help="Exact snapshot filename, e.g. DC_End05-10-2026.xlsx"),
):
    """DC daily stock (S7) — one file per run: delete its date + insert.

    Finds the exact filename across the root and the 2025/2026 history
    folders, so any file reloads exactly (history included).
    New snapshots land at root; daily_run passes the newest root filename.
    """
    from datetime import date as _date
    from functools import partial
    from pathlib import Path

    from etl.db.engine import get_engine
    from etl.pipelines import fact_daily_dc_stock
    from etl.pipelines.runner import extract_guarded, run_pipeline
    from etl.runlog import upsert_dataset
    from etl.sources.xlsx_dc_daily import (
        KNOWN_NON_DATA, file_mtime, file_stock_date, read_dc_file,
        source_dirs, source_root)

    engine = get_engine()
    want_name = file

    def _extract():
        # Exact-name match across root + history folders (cf. OOS --file).
        all_files = ([p for p in sorted(source_root().glob("*.xlsx"))
                      if p.is_file()]
                     + [p for d in source_dirs()
                        for p in sorted(d.rglob("*.xlsx"))])
        seen_names = {p.name for p in all_files}
        skipped = sorted(KNOWN_NON_DATA & seen_names)
        all_files = [p for p in all_files
                     if not p.name.startswith("~$")  # owner locks: transient, never data
                     and p.name not in KNOWN_NON_DATA]
        bad_names = [p.name for p in all_files if not _matches(p)]
        if bad_names:
            raise typer.BadParameter(
                f"non-snapshot xlsx in scan: {bad_names} — remove or rename.")
        paths = [p for p in all_files if p.name == want_name]
        if not paths:
            raise typer.BadParameter(f"no DC snapshot file named {want_name!r}")
        want = file_stock_date(Path(want_name))
        frames = []
        for path in paths:
            df = read_dc_file(path)
            content_dates = set(df["stock_date"].unique().tolist())
            if content_dates != {want}:
                raise typer.BadParameter(
                    f"{path.name}: content dates {sorted(str(d) for d in content_dates)} "
                    f"!= filename date {want} — mislabeled file (cf. 23/24-07-2025).")
            frames.append((df, file_mtime(path), str(path)))
        return frames, skipped, want
        return frames, skipped

    def _matches(p: Path) -> bool:
        from etl.sources.xlsx_dc_daily import FILENAME_RE
        return FILENAME_RE.match(p.name) is not None

    frames, skipped, want = extract_guarded(
        engine, "fact_daily_dc_stock", "core.fact_daily_dc_stock",
        file_stock_date(Path(want_name)), _extract,
        f"S7 re-upload {want_name} via xlsx_dc_daily.read_dc_file")
    run_id = run_pipeline(engine, "fact_daily_dc_stock", "core.fact_daily_dc_stock",
                          _date.today(),
                          partial(fact_daily_dc_stock.run, frames=frames))
    upsert_dataset(engine, "S7", "DC_DailyStock", str(source_root()), None, None,
                   min(df["stock_date"].min() for df, _, _ in frames),
                   max(df["stock_date"].max() for df, _, _ in frames),
                   None, run_id)
    typer.echo(f"OK files={len(frames)} rows={sum(len(df) for df, _, _ in frames)}"
               + (f" skipped_non_data={skipped}" if skipped else ""))


@app.command(name="load-plan")
def load_plan(
    file: str = typer.Option(None, "--file", help="Exact snapshot filename for single-file re-upload"),
):
    """Sales plan steady state (S10): append mode, latest snapshot only.

    Latest = max YYYY_MM in YYYY_MM.RunningProGrid_AsOf_FCMonth.csv. Skips
    when byte-identical to the recorded dataset. History lives in
    etl.history.sales_plan (excluded from daily running).
    Every load is key-exact delete + insert on forecast_month_key
    (DC-stock pattern) — prior snapshots are never touched.
    With --file: reload exactly that snapshot (same key-scoped path).
    """
    import re
    from datetime import date as _date
    from functools import partial

    from etl.db.engine import get_engine
    from etl.pipelines import fact_demandfc_plan
    from etl.pipelines.runner import extract_guarded, run_pipeline
    from etl.runlog import content_hash, get_dataset, upsert_dataset
    from etl.sources.csv_sales_plan import read_plan_file, source_dir

    engine = get_engine()
    directory = source_dir()

    def _extract():
        if file is not None:
            path = directory / file
            if not path.is_file():
                raise typer.BadParameter(f"no snapshot named {file!r}")
            df, uploaded_at = read_plan_file(path)
            return path, df, uploaded_at, True
        cands = []
        for p in sorted(directory.glob("*.RunningProGrid_AsOf_FCMonth.csv")):
            m = re.match(r"(\d{4})_(\d{2})\.RunningProGrid_AsOf_FCMonth\.csv$", p.name)
            if not m:
                raise typer.BadParameter(
                    f"{p.name}: not a YYYY_MM.RunningProGrid_AsOf_FCMonth.csv file.")
            cands.append(((int(m.group(1)), int(m.group(2))), p))
        if not cands:
            raise typer.BadParameter(f"no plan snapshots in {directory}")
        _, path = max(cands)
        df, uploaded_at = read_plan_file(path)
        return path, df, uploaded_at, False

    path, df, uploaded_at, is_single = extract_guarded(
        engine, "fact_sales_plan", "core.fact_demandfc_history",
        _date.today(), _extract, f"S10 {'re-upload ' + file if file else 'latest-file scan'}")
    if not is_single:
        seen = get_dataset(engine, "S10")
        if seen is not None and seen["file_name"] == path.name \
                and seen["content_hash"] == content_hash(path):
            typer.echo(f"SKIP file={path.name} unchanged")
            return
    frames = [(df, uploaded_at, str(path))]
    run_id = run_pipeline(engine, "fact_sales_plan", "core.fact_demandfc_history",
                          _date.today(),
                          partial(fact_demandfc_plan.run, frames=frames))
    upsert_dataset(engine, "S10", "RunningProGrid_AsOf_FCMonth",
                   str(path), path.name, uploaded_at,
                   df["sales_plan_date"].min(), df["sales_plan_date"].max(),
                   content_hash(path), run_id)
    typer.echo(f"OK file={path.name} rows={len(df)}")


@app.command(name="load-oos-status")
def load_oos_status(
    file: str = typer.Option(None, "--file", help="Exact snapshot filename for re-upload"),
):
    """OOS stock status steady state (S12): the single latest snapshot only.

    Latest = max week-date in the filename. With --file: load exactly that
    file (re-upload path: check DB -> delete its dates -> insert).
    A same-grain conflict across files fails naming them.
    """
    from datetime import date as _date
    from functools import partial
    from pathlib import Path

    from etl.db.engine import get_engine
    from etl.pipelines import fact_oos_status
    from etl.pipelines.runner import extract_guarded, run_pipeline
    from etl.runlog import content_hash, get_dataset, upsert_dataset
    from etl.sources.xlsx_oos_status import (
        file_stock_date, read_oos_file, source_dir)
    from etl.sources.scan import iter_data_files

    engine = get_engine()
    directory = source_dir()

    def _extract():
        files = list(iter_data_files(directory, "*.xlsx"))
        if file is not None:
            wanted = [p for p in files if p.name == file]
            if not wanted:
                raise typer.BadParameter(f"no snapshot named {file!r}")
            paths = wanted
        else:
            dated = [(file_stock_date(p.name), p) for p in files]
            _, latest = max(dated)
            paths = [latest]
        frames = []
        for path in paths:
            df = read_oos_file(path)
            frames.append((df, _date.today(), str(path)))
        return frames

    frames = extract_guarded(
        engine, "fact_oos_status", "core.fact_oos_status",
        _date.today(), _extract,
        f"S12 {'re-upload ' + file if file else 'latest-file scan'} "
        f"in {directory}")
    if file is None:
        path = Path(frames[0][2])
        seen = get_dataset(engine, "S12")
        if seen is not None and seen["file_name"] == path.name \
                and seen["content_hash"] == content_hash(path):
            df = frames[0][0]
            typer.echo(f"SKIP week={df['stock_date'].iloc[0]} file={path.name} unchanged")
            return
    run_id = run_pipeline(engine, "fact_oos_status", "core.fact_oos_status",
                          _date.today(),
                          partial(fact_oos_status.run, frames=frames))
    for df, uploaded_at, spath in frames:
        p = Path(spath)
        upsert_dataset(engine, "S12", "OOS_Stock_Status", spath, p.name,
                       uploaded_at, df["stock_date"].min(),
                       df["stock_date"].max(), content_hash(p), run_id)
    typer.echo(f"OK files={[Path(s).name for _, _, s in frames]} "
               f"rows={sum(len(df) for df, _, _ in frames)}")


if __name__ == "__main__":
    app()
