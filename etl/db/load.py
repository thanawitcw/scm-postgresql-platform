"""Staging loads: TRUNCATE + COPY in the caller's transaction (R1, R2).

`to_sql` is banned for `core`/`rpt` (R2) and too clever for `stg` (it infers
types run-to-run). COPY into Alembic-managed DDL is explicit and fast, and
scales from the 2.7k-row product master to the 1.9M-row stock files.
"""

from __future__ import annotations

import io

import pandas as pd


def copy_df(conn, table: str, df: pd.DataFrame, columns: list[str]) -> int:
    """COPY a DataFrame into a pre-created table. NaN/NaT/'' -> NULL. Returns rows."""
    buf = io.StringIO()
    frame = df.reindex(columns=columns)
    for col in frame.columns:
        s = frame[col]
        # Access INTEGER cols arrive as float64 when any NULL exists (60.0);
        # Postgres rejects "60.0" for integer. Integral floats -> nullable Int64.
        if pd.api.types.is_float_dtype(s):
            non_null = s.dropna()
            if len(non_null) and all(float(x).is_integer() for x in non_null):
                frame[col] = s.astype("Int64")
    frame.to_csv(buf, index=False, header=False)
    buf.seek(0)
    raw = conn.connection.driver_connection
    with raw.cursor() as cur:
        with cur.copy(f"COPY {table} ({', '.join(columns)}) FROM STDIN (FORMAT CSV)") as cp:
            cp.write(buf.getvalue())
    return len(df)


def truncate(conn, table: str) -> None:
    conn.exec_driver_sql(f"TRUNCATE {table}")
