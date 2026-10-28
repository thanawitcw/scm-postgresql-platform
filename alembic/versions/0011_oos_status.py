"""OOS stock status snapshots, OOS_Stock_Status xlsx (S12).

Raw wide mirror, no business transform (user ruling): every source column
lands, including DOH/StockStatus/OOS notes. Grain
(stock_date x cj_item x shm_item) — same item recurs across SHM pack
variants, so shm_item is part of the key and nothing is lost. DC5 block
exists only in 2026 files: numerics 0-fill, dates/text NULL (user ruling).
"""

revision = "0011_oos_status"
down_revision = "0010_sales_plan"
branch_labels = None
depends_on = None

DESCRIPTORS = [
    ("stock_date", "date"),
    ("division_shm", "text"),
    ("owner_scm", "text"),
    ("supplier_name", "text"),
    ("shm_item", "text"),
    ("cj_item", "text"),
    ("cj_description", "text"),
    ("lead_time_days", "numeric"),
    ("cj_status", "text"),
    ("shm_status", "text"),
    ("pc_cartons", "numeric"),
    ("first_so_date", "date"),
    ("helper_column_for_total", "text"),
]

BLOCKS = ["total", "dc1", "dc2", "dc4", "dc5"]
BLOCK_NUM = ["active_assort", "store_oos", "store_stock_qty", "doh_store",
             "avg_sale_qty_90d", "dc_stock_qty", "doh_dc", "po_pending"]
BLOCK_DATE = ["first_del_date"]
BLOCK_TEXT = ["stock_status", "oos_by", "oos_reason", "owner_note"]

BLOCK_COLS = ([f"{b}_{m}" for b in BLOCKS for m in BLOCK_NUM]
              + [f"{b}_first_del_date" for b in BLOCKS]
              + [f"{b}_{m}" for b in BLOCKS for m in BLOCK_TEXT])


def _ddl(with_constraints: bool) -> str:
    cols = ",\n            ".join(f"{n} {t}" for n, t in DESCRIPTORS)
    cols += ",\n            " + ",\n            ".join(
        f"{c} numeric" for b in BLOCKS for c in
        [f"{b}_{m}" for m in BLOCK_NUM])
    cols += ",\n            " + ",\n            ".join(
        f"{b}_first_del_date date" for b in BLOCKS)
    cols += ",\n            " + ",\n            ".join(
        f"{b}_{m} text" for b in BLOCKS for m in BLOCK_TEXT)
    return cols


def upgrade() -> None:
    from alembic import op

    op.execute(
        f"""
        CREATE TABLE stg.stg_oos_status (
            { _ddl(False) },
            uploaded_at  timestamptz,
            source_file  text
        );
        """
    )
    op.execute(
        f"""
        CREATE TABLE core.fact_oos_status (
            { _ddl(False) },
            loaded_at    timestamptz NOT NULL DEFAULT now(),
            source_file  text,
            CONSTRAINT uq_fact_oos_status_grain UNIQUE
                (stock_date, cj_item, shm_item)
        );
        CREATE INDEX ON core.fact_oos_status (stock_date);
        CREATE INDEX ON core.fact_oos_status (cj_item, stock_date);
        """
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE "
        "ON stg.stg_oos_status, core.fact_oos_status TO etl_writer"
    )
    # Contract views that read tables from 0007-0011 can only be (re)applied
    # here, at the head — 0006 predates their tables. CREATE OR REPLACE.
    from pathlib import Path as _Path
    _rpt = _Path(__file__).resolve().parent.parent.parent / "sql" / "rpt"
    op.execute((_rpt / "v_freshness.sql").read_text(encoding="utf-8"))
    op.execute("GRANT SELECT ON rpt.v_freshness TO reporting")


def downgrade() -> None:
    from alembic import op

    op.execute("DROP TABLE IF EXISTS core.fact_oos_status")
    op.execute("DROP TABLE IF EXISTS stg.stg_oos_status")
