"""Reusable DQ assertion helpers. Each RAISEs inside the load transaction (§7).

Severity lives in the caller (rules.py): checks raise AssertionError with a
message; the pipeline records ERROR vs WARN in ops.dq_result and decides
whether to roll back (ERROR) or continue (WARN).
"""

from __future__ import annotations

from sqlalchemy import text


def _fail(check_name: str, observed: str, expected: str) -> None:
    raise AssertionError(f"[{check_name}] observed={observed} expected={expected}")


def not_empty(conn, table: str, where: str = "TRUE") -> None:
    n = conn.execute(text(f"SELECT count(*) FROM {table} WHERE {where}")).scalar()
    if not n:
        _fail(f"not_empty:{table}", f"{n} rows", ">0 rows")


def not_null(conn, table: str, columns: list[str], where: str = "TRUE") -> None:
    for col in columns:
        n = conn.execute(
            text(f"SELECT count(*) FROM {table} WHERE {where} AND {col} IS NULL")
        ).scalar()
        if n:
            _fail(f"not_null:{table}.{col}", f"{n} NULLs", "0 NULLs")


def unique_on(conn, table: str, columns: list[str], where: str = "TRUE") -> None:
    # GROUP BY (not count(DISTINCT …)): GROUP BY treats NULLs as equal, so
    # nullable grain columns still collide instead of slipping through.
    cols = ", ".join(columns)
    n = conn.execute(
        text(f"SELECT COALESCE(sum(cnt - 1), 0) FROM "
             f"(SELECT count(*) AS cnt FROM {table} WHERE {where} "
             f"GROUP BY {cols} HAVING count(*) > 1) t")
    ).scalar()
    if n:
        _fail(f"unique:{table}({cols})", f"{n} duplicates", "0 duplicates")


def row_count_within_history(
    conn, table: str, where: str, current_count: int, tolerance_pct: float = 10.0
) -> None:
    """Current window volume vs the median of prior SUCCESS runs. Catches partial files."""
    hist = conn.execute(
        text(
            "SELECT percentile_cont(0.5) WITHIN GROUP (ORDER BY rows_out) "
            "FROM ops.etl_run WHERE target_table = :t AND status = 'SUCCESS'"
        ),
        {"t": table},
    ).scalar()
    if hist is None:
        return  # no history yet — first run always passes
    lo = hist * (1 - tolerance_pct / 100)
    if current_count < lo:
        _fail(f"row_count_within_history:{table}", str(current_count), f">= {lo:.0f}")


def date_range(conn, table: str, column: str, dmin: str, dmax: str) -> None:
    bad = conn.execute(
        text(
            f"SELECT count(*) FROM {table} "
            f"WHERE {column} < DATE :dmin OR {column} > DATE :dmax"
        ),
        {"dmin": dmin, "dmax": dmax},
    ).scalar()
    if bad:
        _fail(f"date_range:{table}.{column}", f"{bad} out-of-range", f"within {dmin}..{dmax}")


def referential(
    conn, fact: str, fk: str, dim: str, pk: str, where: str = "TRUE"
) -> None:
    """Orphan FKs. New items with no dim_item row land here first."""
    n = conn.execute(
        text(
            f"SELECT count(*) FROM {fact} f LEFT JOIN {dim} d ON f.{fk} = d.{pk} "
            f"WHERE {where} AND f.{fk} IS NOT NULL AND d.{pk} IS NULL"
        )
    ).scalar()
    if n:
        _fail(f"referential:{fact}.{fk}->{dim}.{pk}", f"{n} orphans", "0 orphans")


def measure_range(
    conn, table: str, column: str, low=None, high=None, where: str = "TRUE"
) -> None:
    conds = []
    if low is not None:
        conds.append(f"{column} < {low!r}")
    if high is not None:
        conds.append(f"{column} > {high!r}")
    if not conds:
        return
    n = conn.execute(
        text(f"SELECT count(*) FROM {table} WHERE {where} AND ({' OR '.join(conds)})")
    ).scalar()
    if n:
        _fail(f"measure_range:{table}.{column}", f"{n} outside [{low},{high}]", "0 outside")
