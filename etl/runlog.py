"""Provenance writer for ops.etl_run + ops.dataset (§10.1).

Habit: write the RUNNING row FIRST, then UPDATE on completion. A process
killed mid-load leaves RUNNING with no finished_at — that IS the crash signal.
error_text is appended, never overwritten.
"""

from __future__ import annotations

import hashlib
import time
import uuid
from datetime import date
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.engine import Engine


def start_run(engine: Engine, pipeline: str, target_table: str, run_date: date) -> str:
    run_id = str(uuid.uuid4())
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO ops.etl_run "
                "(run_id, pipeline, target_table, run_date, started_at, status) "
                "VALUES (:run_id, :pipeline, :target_table, :run_date, now(), 'RUNNING')"
            ),
            {"run_id": run_id, "pipeline": pipeline,
             "target_table": target_table, "run_date": run_date},
        )
    return run_id


def finish_run(
    engine: Engine, run_id: str, status: str,
    rows_in: int | None = None, rows_out: int | None = None,
    error_text: str | None = None, duration_ms: int | None = None,
) -> None:
    with engine.begin() as conn:
        conn.execute(
            text(
                "UPDATE ops.etl_run SET status = :status, finished_at = now(), "
                "rows_in = COALESCE(CAST(:rows_in AS bigint), rows_in), "
                "rows_out = COALESCE(CAST(:rows_out AS bigint), rows_out), "
                "duration_ms = CAST(:duration_ms AS integer), "
                "error_text = CASE WHEN CAST(:error_text AS text) IS NULL THEN error_text "
                "WHEN error_text IS NULL THEN CAST(:error_text AS text) "
                "ELSE error_text || E'\\n' || CAST(:error_text AS text) END "
                "WHERE run_id = CAST(:run_id AS uuid)"
            ),
            {"run_id": run_id, "status": status, "rows_in": rows_in,
             "rows_out": rows_out, "error_text": error_text,
             "duration_ms": duration_ms},
        )


def content_hash(path: Path) -> str:
    """sha256 of a file. Makes an in-place overwrite visible (§10.1)."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def upsert_dataset(
    engine: Engine, dataset_key: str, dataset_name: str, file_path: str,
    file_name: str | None, uploaded_at, business_min: date | None,
    business_max: date | None, digest: str | None, run_id: str | None,
) -> None:
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO ops.dataset (dataset_key, dataset_name, file_name, file_path, "
                "uploaded_at, business_date_min, business_date_max, content_hash, last_run_id) "
                "VALUES (CAST(:k AS text), CAST(:n AS text), CAST(:fn AS text), "
                "CAST(:fp AS text), CAST(:ua AS timestamptz), CAST(:bmin AS date), "
                "CAST(:bmax AS date), CAST(:h AS text), CAST(:rid AS uuid)) "
                "ON CONFLICT (dataset_key) DO UPDATE SET "
                "dataset_name = EXCLUDED.dataset_name, file_name = EXCLUDED.file_name, "
                "file_path = EXCLUDED.file_path, uploaded_at = EXCLUDED.uploaded_at, "
                "business_date_min = EXCLUDED.business_date_min, "
                "business_date_max = EXCLUDED.business_date_max, "
                "content_hash = EXCLUDED.content_hash, last_run_id = EXCLUDED.last_run_id, "
                "last_seen_at = now()"
            ),
            {"k": dataset_key, "n": dataset_name, "fn": file_name, "fp": file_path,
             "ua": uploaded_at, "bmin": business_min, "bmax": business_max,
             "h": digest, "rid": run_id},
        )


def record_dq(
    engine: Engine, run_id: str, check_name: str, severity: str,
    passed: bool, observed: str | None = None, expected: str | None = None,
) -> None:
    """Write one ops.dq_result row. WARN never rolls back; ERROR is raised by the check itself."""
    from sqlalchemy import text as _text

    with engine.begin() as conn:
        conn.execute(
            _text(
                "INSERT INTO ops.dq_result "
                "(run_id, check_name, severity, passed, observed, expected) "
                "VALUES (CAST(:rid AS uuid), CAST(:cn AS text), CAST(:sev AS text), "
                ":p, CAST(:o AS text), CAST(:e AS text))"
            ),
            {"rid": run_id, "cn": check_name, "sev": severity,
             "p": passed, "o": observed, "e": expected},
        )


def get_dataset(engine: Engine, dataset_key: str) -> dict | None:
    """Latest ops.dataset row for one key (S3, S4, ...). None if never loaded."""
    from sqlalchemy import text as _text

    with engine.connect() as conn:
        row = conn.execute(
            _text("SELECT dataset_key, file_name, content_hash, "
                  "business_date_min, business_date_max, last_seen_at "
                  "FROM ops.dataset WHERE dataset_key = CAST(:k AS text)"),
            {"k": dataset_key},
        ).mappings().first()
    return dict(row) if row else None


class timed:
    """Usage: with timed() as t: ...; finish_run(..., duration_ms=t.ms)"""
    def __enter__(self):
        self._t0 = time.perf_counter()
        return self

    def __exit__(self, *exc):
        self.ms = int((time.perf_counter() - self._t0) * 1000)
