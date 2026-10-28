"""Phase 4 (1/2): sales plan snapshots, RunningProGrid_AsOf_FCMonth CSVs (S10).

Raw mirror, no business transform (user ruling): source values land as-is,
including pro_type case/whitespace variants ('Mail', ' 7-Days ') — any
normalisation belongs in rpt at join time, not here. Grain
(forecast_month_key x sales_plan_date x cj_item x sales_channel x
pro_type x pro_theme): measured 0 dupes across all 8 snapshots.
"""

revision = "0010_sales_plan"
down_revision = "0009_dc_daily_stock"
branch_labels = None
depends_on = None


def upgrade() -> None:
    from alembic import op

    op.execute(
        """
        CREATE TABLE stg.stg_demandfc_history (
            forecast_month_key   text,
            fc_lead_time_months  integer,
            sales_plan_date      date,
            demand_owner         text,
            cj_item              text,
            sales_channel        text,
            pro_type             text,
            pro_theme            text,
            promo_price_per_piece numeric,
            forecast_cartons     numeric,
            forecast_qty         numeric,
            uploaded_at          timestamptz,
            source_file          text
        );
        """
    )
    op.execute(
        """
        CREATE TABLE core.fact_demandfc_history (
            forecast_month_key   text NOT NULL,
            fc_lead_time_months  integer,
            sales_plan_date      date NOT NULL,
            demand_owner         text,
            cj_item              text,
            sales_channel        text,
            pro_type             text,
            pro_theme            text,
            promo_price_per_piece numeric,
            forecast_cartons     numeric,
            forecast_qty         numeric,
            loaded_at            timestamptz NOT NULL DEFAULT now(),
            source_file          text,
            CONSTRAINT uq_fact_demandfc_history_grain UNIQUE NULLS NOT DISTINCT
                (forecast_month_key, sales_plan_date, cj_item,
                  sales_channel, pro_type, pro_theme)
        );
        CREATE INDEX ON core.fact_demandfc_history (sales_plan_date, cj_item);
        CREATE INDEX ON core.fact_demandfc_history (forecast_month_key);
        """
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE "
        "ON stg.stg_demandfc_history, core.fact_demandfc_history TO etl_writer"
    )


def downgrade() -> None:
    from alembic import op

    op.execute("DROP TABLE IF EXISTS core.fact_demandfc_history")
    op.execute("DROP TABLE IF EXISTS stg.stg_demandfc_history")
