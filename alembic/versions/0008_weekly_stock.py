"""Phase 4b (2/3): weekly stock, Sahamit xlsx snapshots (S11).

Wide grain (stock_date x cj_item): one row per product per Monday snapshot,
Total/DC1/DC2/DC4/DC5 measure blocks as columns (12 measures each,
file-faithful incl. DOH*/PercOOS). DC5 exists only from
2026-05-18; absent blocks load 0 in the fact (user ruling), NULL in stg.
TierCompany rides as an attribute (not part of the UNIQUE).
"""

revision = "0008_weekly_stock"
down_revision = "0007_goods_receipt"
branch_labels = None
depends_on = None

BLOCKS = ["total", "dc1", "dc2", "dc4", "dc5"]
MEASURES = ["total_store", "avg_sales_qty_90d", "avg_cogs_90d",
            "store_stock_qty", "store_stock_value", "store_doh",
            "dc_stock_qty", "dc_stock_value", "dc_doh",
            "active_store", "oos_store", "store_oos_pct"]

MEASURE_COLS = [f"{b}_{m}" for b in BLOCKS for m in MEASURES]


def _cols() -> str:
    return ",\n            ".join(f"{c} numeric" for c in MEASURE_COLS)


def upgrade() -> None:
    from alembic import op

    op.execute(
        f"""
        CREATE TABLE stg.stg_weekly_stock (
            stock_date   date,
            cj_item      text,
            tier_company text,
            { _cols() },
            uploaded_at  timestamptz,
            source_file  text
        );
        """
    )
    op.execute(
        f"""
        CREATE TABLE core.fact_weekly_stock (
            stock_date   date NOT NULL,
            cj_item      text NOT NULL,
            tier_company text,
            { _cols() },
            loaded_at    timestamptz NOT NULL DEFAULT now(),
            source_file  text,
            CONSTRAINT uq_fact_weekly_stock_grain UNIQUE (stock_date, cj_item)
        );
        CREATE INDEX ON core.fact_weekly_stock (stock_date);
        CREATE INDEX ON core.fact_weekly_stock (cj_item, stock_date);
        """
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE "
        "ON stg.stg_weekly_stock, core.fact_weekly_stock TO etl_writer"
    )


def downgrade() -> None:
    from alembic import op

    op.execute("DROP TABLE IF EXISTS core.fact_weekly_stock")
    op.execute("DROP TABLE IF EXISTS stg.stg_weekly_stock")
