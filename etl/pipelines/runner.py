"""Pipeline runner: one etl_run row per pipeline, RUNNING first (§10.1)."""

from __future__ import annotations

from collections.abc import Callable
from datetime import date

from etl.runlog import finish_run, record_dq, start_run, timed


def run_pipeline(engine, pipeline: str, target_table: str, run_date: date,
                 fn: Callable[[object, str], dict]) -> str:
    """fn(engine, run_id) does the work and returns {'rows_in','rows_out'}.

    ERROR severity propagates (rolls back the load tx, marks FAILED);
    WARN is recorded in ops.dq_result and the load stands.
    """
    with timed() as t:
        run_id = start_run(engine, pipeline, target_table, run_date)
    try:
        counts = fn(engine, run_id)
        finish_run(engine, run_id, "SUCCESS", duration_ms=t.ms, **counts)
        return run_id
    except AssertionError as e:
        record_dq(engine, run_id, str(e)[:200], "ERROR", False)
        finish_run(engine, run_id, "FAILED", duration_ms=t.ms,
                   error_text=f"{type(e).__name__}: {e}"[:2000])
        raise
    except Exception as e:  # noqa: BLE001 — must be recorded, never swallowed
        finish_run(engine, run_id, "FAILED", duration_ms=t.ms,
                   error_text=f"{type(e).__name__}: {e}"[:2000])
        raise


def extract_guarded(engine, pipeline: str, target_table: str, run_date: date,
                    fn: Callable[[], object], source_label: str):
    """Run an extract fn() that lives OUTSIDE run_pipeline's transaction.

    Source reads (share copy, xlsx/CSV parse, archive) happen before any
    pipeline tx opens — without this, a drift/missing-file error escapes
    with NO etl_run row. Here it becomes a FAILED row naming the pipeline,
    the target table and the datasource that caused it, then re-raises.
    """
    try:
        return fn()
    except Exception as e:  # noqa: BLE001 — recorded, then re-raised
        run_id = start_run(engine, pipeline, target_table, run_date)
        finish_run(engine, run_id, "FAILED",
                   error_text=f"EXTRACT {source_label}: {type(e).__name__}: {e}"[:2000])
        raise


def warn(engine, run_id: str, fn: Callable[[object], None], name: str) -> None:
    """Run a DQ check as WARN: record and continue."""
    try:
        from etl.db.engine import get_engine as _g  # noqa: F401
        with engine.connect() as conn:
            fn(conn)
    except AssertionError as e:
        record_dq(engine, run_id, name or str(e)[:200], "WARN", False,
                  observed=str(e)[:500])
    else:
        record_dq(engine, run_id, name, "WARN", True)
