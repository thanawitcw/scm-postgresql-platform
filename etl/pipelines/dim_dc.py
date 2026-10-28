"""dim_dc — static seed from the S7 Plant mapping (D001→DC1, no D003)."""

from __future__ import annotations

from sqlalchemy import text

from etl.quality import checks as c

EXPECTED = {"D001": "DC1", "D002": "DC2", "D004": "DC4", "D005": "DC5"}


def run(engine, run_id: str) -> dict:
    with engine.connect() as conn:
        c.not_empty(conn, "core.dim_dc")
        rows = dict(
            conn.execute(text("SELECT plant_code, dc_name FROM core.dim_dc")).all()
        )
    assert rows == EXPECTED, f"dim_dc drift: {rows}"
    return {"rows_in": len(rows), "rows_out": len(rows)}
