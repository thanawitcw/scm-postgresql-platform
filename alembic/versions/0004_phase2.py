"""Phase 2: actual sales (S3, S4).

Grain correction (measured 2026-10-06, supersedes the §5.1.1/§8 claim of
one row per date×item): the source is promo-attributed — every (date, item)
carries a 'Normal' row plus one row per promo price-point. The fact grain is
therefore (sales_date × item × channel × pro_type × pro_theme ×
pro_price_in_vat × is_main), enforced by UNIQUE NULLS NOT DISTINCT so that
NULLed sentinel prices still collide instead of slipping through.
"""

revision = "0004_phase2"
down_revision = "0003_truncate"
branch_labels = None
depends_on = None


def upgrade() -> None:
    from alembic import op

    op.execute(
        """
        CREATE TABLE stg.stg_csv_daily_sales (
            sales_date       date,
            cj_item          text,
            product_name     text,
            sales_channel    text,
            pro_type         text,
            pro_theme        text,
            pro_price_in_vat numeric,
            so_qty_piece     numeric,
            so_amt_in_vat    numeric,
            is_main          boolean,
            uploaded_at      timestamptz,
            source_file      text
        );
        """
    )
    op.execute(
        """
        CREATE TABLE core.dim_channel (
            channel_key   bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            sales_channel text NOT NULL UNIQUE,
            channel_name  text NOT NULL,
            loaded_at     timestamptz NOT NULL DEFAULT now()
        );
        INSERT INTO core.dim_channel (sales_channel, channel_name) VALUES
            ('CJ', 'CJ sellout'),
            ('TD', 'TD sellout')
        ON CONFLICT (sales_channel) DO NOTHING;
        """
    )
    op.execute(
        """
        CREATE TABLE core.fact_daily_sales (
            sales_date       date NOT NULL REFERENCES core.dim_date(date),
            cj_item          text,
            channel_key      bigint NOT NULL REFERENCES core.dim_channel(channel_key),
            pro_type         text,
            pro_theme        text,
            pro_price_in_vat numeric,
            so_qty_piece     numeric,
            so_amt_in_vat    numeric,
            is_main          boolean NOT NULL DEFAULT false,
            CONSTRAINT uq_fact_daily_sales_grain UNIQUE NULLS NOT DISTINCT
                (sales_date, cj_item, channel_key,
                 pro_type, pro_theme, pro_price_in_vat, is_main)
        );
        CREATE INDEX ON core.fact_daily_sales (sales_date, channel_key);
        CREATE INDEX ON core.fact_daily_sales (cj_item, sales_date);
        """
    )


def downgrade() -> None:
    from alembic import op

    op.execute("DROP TABLE IF EXISTS core.fact_daily_sales")
    op.execute("DROP TABLE IF EXISTS core.dim_channel")
    op.execute("DROP TABLE IF EXISTS stg.stg_csv_daily_sales")
