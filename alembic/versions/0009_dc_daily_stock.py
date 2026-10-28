"""Phase 4b (3/3): DC daily stock snapshots, DC_DailyStock xlsx (S7).

stg.stg_dc_daily — every row incl. non-200 Materials (file-faithful).
core.fact_daily_dc_stock — grain (stock_date x dc_key x cj_item):
Plant D001/D002/D004/D005 resolves via dim_dc.plant_code (user ruling:
surfaced as DC1/DC2/DC4/DC5). Non-200 Materials excluded + WARN (user
ruling 2026-10-06: systematic since 2026-02-25, failing history forever
is impractical). Negative stock_qty kept (backorder signal, never clamped).
"""

revision = "0009_dc_daily_stock"
down_revision = "0008_weekly_stock"
branch_labels = None
depends_on = None


def upgrade() -> None:
    from alembic import op

    op.execute(
        """
        CREATE TABLE stg.stg_dc_daily (
            stock_date   date,
            plant        text,
            material     text,
            stock_qty    numeric,
            stock_value  numeric,
            uploaded_at  timestamptz,
            source_file  text
        );
        """
    )
    op.execute(
        """
        CREATE TABLE core.fact_daily_dc_stock (
            stock_date   date NOT NULL,
            dc_key       bigint NOT NULL REFERENCES core.dim_dc(dc_key),
            cj_item      text NOT NULL,
            stock_qty    numeric,
            stock_value  numeric,
            CONSTRAINT uq_fact_daily_dc_stock_grain UNIQUE
                (stock_date, dc_key, cj_item)
        );
        CREATE INDEX ON core.fact_daily_dc_stock (stock_date, dc_key);
        CREATE INDEX ON core.fact_daily_dc_stock (cj_item, stock_date);
        """
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE "
        "ON stg.stg_dc_daily, core.fact_daily_dc_stock TO etl_writer"
    )


def downgrade() -> None:
    from alembic import op

    op.execute("DROP TABLE IF EXISTS core.fact_daily_dc_stock")
    op.execute("DROP TABLE IF EXISTS stg.stg_dc_daily")
