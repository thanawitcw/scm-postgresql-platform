"""Phase 3 live regression (reads production; run after load-progrid).

Census 2026-10-21 (daily CSV export, ProGrid_Data/ProGrid_PCB_20261021):
S1 10,952 + S2 5,189 source rows → MAIL 5,339 in stg. If the source drifts
(new daily file), update the census counts here — but the STRUCTURAL
assertions (no case variants survive, wildcard stays distinct) must keep
passing without edits.
"""

import os

import pytest
import sqlalchemy as sa

pytestmark = pytest.mark.skipif(
    os.environ.get("SCM_INTEGRATION") != "1",
    reason="live-DB regression: set SCM_INTEGRATION=1 against a loaded database",
)

MAIL_EXPECTED = 5_339  # census 2026-10-21: newest daily CSVs (was 5,310 on xlsx)
DISTINCT_EXPECTED = 10


def _engine():
    return sa.create_engine(
        "postgresql+psycopg://scm_admin:"
        f"{os.environ.get('PGPASSWORD', '')}@127.0.0.1:5432/"
        f"{os.environ.get('PGDATABASE', 'scm')}")


def test_mail_count_and_distinct():
    # Census is SOURCE-grain (stg keeps every row); the fact explodes it to days.
    e = _engine()
    with e.connect() as c:
        mail = c.execute(sa.text(
            "SELECT count(*) FROM stg.stg_progrid_raw WHERE pro_type='MAIL'")).scalar()
        distinct = c.execute(sa.text(
            "SELECT count(DISTINCT pro_type) FROM core.fact_running_progrid")).scalar()
        lower_left = c.execute(sa.text(
            "SELECT count(*) FROM core.fact_running_progrid WHERE pro_type <> upper(pro_type)")).scalar()
    assert mail == MAIL_EXPECTED, f"MAIL source rows: {mail}"
    assert distinct == DISTINCT_EXPECTED, f"distinct pro_type: {distinct}"
    assert lower_left == 0, f"{lower_left} rows escaped upper-casing"


def test_nbsp_cancel_classified_and_never_ran_excluded():
    # Policy A (never_ran): classification must catch NBSP variants, and only
    # never_ran rows (cancelled at/after upload, never priced) are excluded.
    # NOTE: deviates from the Phase-3 checklist line "3 rows must be excluded":
    # those rows have 2025 windows vs a 2026 upload, so under Policy A they
    # load with their full window (history reproducible from the archive).
    e = _engine()
    with e.connect() as c:
        folded = c.execute(sa.text(
            "SELECT count(*) FROM stg.stg_progrid_raw "
            "WHERE status_raw LIKE '%' || chr(160) || '%' "
            "AND status_norm = 'cancel'")).scalar()
        never_ran_leaked = c.execute(sa.text(
            "SELECT count(*) FROM core.fact_running_progrid "
            "WHERE is_cancelled AND valid_from >= uploaded_at::date")).scalar()
    assert folded >= 1, "NBSP-cancel folding broke (expected ≥1, census had 3)"
    assert never_ran_leaked == 0, f"{never_ran_leaked} never_ran rows leaked"
