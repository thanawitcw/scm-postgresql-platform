"""Atomic load helpers. Every pattern commits exactly once (R1).

Pattern A — incremental date window (default, R7):
    DELETE the window + INSERT the replacement + DQ, in ONE transaction.
    Readers outside the window are unaffected.

Pattern B — full refresh for small tables:
    TRUNCATE + INSERT + DQ, in ONE transaction.

Pattern C — large full refresh (shadow table + swap):
    Build <table>__new, DQ it, then swap. Reapplies rpt views (the swap
    breaks dependent views) and re-grants. Reserved for deliberate rebuilds.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import date

from sqlalchemy import text
from sqlalchemy.engine import Engine

Check = Callable[[object], None]  # receives the open Connection, RAISEs on failure


def incremental_load(
    engine: Engine,
    table: str,
    date_column: str,
    window: tuple[date, date],
    source_sql: str,
    dq: Sequence[Check] = (),
    params: dict | None = None,
) -> int:
    """DELETE + INSERT over a closed date window, atomically. Returns rows inserted."""
    d_from, d_to = window
    args = {"d_from": d_from, "d_to": d_to, **(params or {})}
    with engine.begin() as conn:  # ONE transaction — never commit between DELETE and INSERT
        conn.execute(
            text(f"DELETE FROM {table} WHERE {date_column} BETWEEN :d_from AND :d_to"),
            args,
        )
        result = conn.execute(text(source_sql), args)
        for check in dq:
            check(conn)
        conn.execute(text("SELECT pg_stat_reset() WHERE false"))  # no-op guard
    return result.rowcount if result.rowcount is not None else -1


def full_refresh_small(
    engine: Engine,
    table: str,
    source_sql: str,
    dq: Sequence[Check] = (),
    params: dict | None = None,
) -> int:
    """TRUNCATE + INSERT for small tables, atomically. Holds ACCESS EXCLUSIVE briefly."""
    with engine.begin() as conn:
        conn.execute(text(f"TRUNCATE {table}"))
        result = conn.execute(text(source_sql), params or {})
        for check in dq:
            check(conn)
    return result.rowcount if result.rowcount is not None else -1


def full_refresh_swap(
    engine: Engine,
    table: str,
    ddl_new: str,
    source_sql: str,
    rpt_view_sql: Sequence[str],
    grants_sql: Sequence[str] = (),
    dq: Sequence[Check] = (),
    params: dict | None = None,
) -> None:
    """Shadow-table swap for tables too big for Pattern B. Reapplies rpt views + grants."""
    schema, _, name = table.partition(".")
    new, old = f"{table}__new", f"{table}__old"
    with engine.begin() as conn:
        conn.execute(text(f"DROP TABLE IF EXISTS {new}"))
        conn.execute(text(ddl_new))
        conn.execute(text(source_sql), params or {})
        for check in dq:
            check(conn)
        conn.execute(text(f"ALTER TABLE {table} RENAME TO {name}__old"))
        conn.execute(text(f"ALTER TABLE {new} RENAME TO {name}"))
        conn.execute(text(f"DROP TABLE {old} CASCADE"))
        for stmt in rpt_view_sql:
            conn.execute(text(stmt))
        for stmt in grants_sql:
            conn.execute(text(stmt))
        _ = schema  # schema is embedded in `table`; kept for readability
