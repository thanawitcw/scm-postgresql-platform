"""dim_date — seeded by migration 0002; pipeline only verifies the seed."""

from __future__ import annotations

from sqlalchemy import text

from etl.quality import checks as c

MIN_DAYS = 365 * 5  # 2020-2030 seed must cover at least 5 years


def run(engine, run_id: str) -> dict:
    with engine.connect() as conn:
        c.not_empty(conn, "core.dim_date")
        n = conn.execute(text("SELECT count(*) FROM core.dim_date")).scalar()
        assert n >= MIN_DAYS, f"dim_date seed short: {n} rows"
        span = conn.execute(
            text("SELECT min(date), max(date) FROM core.dim_date")
        ).fetchone()
        assert span[0] <= span[1], "dim_date seed has no span"
    return {"rows_in": n, "rows_out": n}
