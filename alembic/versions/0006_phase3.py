"""Phase 3: promotion (S1, S2).

stg_progrid_raw (every source row incl. cancelled),
fact_running_progrid (faithful generate_series expansion — NOT a resolution),
ops.promotion_change_log (table, §8.1.1), rpt contract views (from sql/rpt/).
pro_type is free text (no ref table): canonical values are documented in §4.3
and unknown values surface via rpt.v_unmapped_values, never defaulted.
"""

revision = "0006_phase3"
down_revision = "0005_fact_cjitem"
branch_labels = None
depends_on = None

RPT_VIEWS = [
    "v_fact_progrid_bestprice.sql",
    "v_promotion_changes.sql",
    "v_promotion_effective_periods.sql",
    "v_unmapped_values.sql",
]


def _view_sql(name: str) -> str:
    from pathlib import Path

    return (Path(__file__).resolve().parent.parent.parent
            / "sql" / "rpt" / name).read_text(encoding="utf-8")


def upgrade() -> None:
    from alembic import op

    op.execute("CREATE SCHEMA IF NOT EXISTS rpt")
    op.execute(
        """
        CREATE TABLE stg.stg_progrid_raw (
            source_key           text,
            cj_item              text,
            rsp                  numeric,
            rsp_unit             text,
            pro_type             text,
            pro_theme            text,
            pro_price_rsp_gap    numeric,
            promo_price_per_piece numeric,
            valid_from           date,
            valid_to             date,
            status_raw           text,
            status_norm          text,
            is_cancelled         boolean,
            uploaded_at          timestamptz,
            source_file          text
        );
        """
    )
    op.execute(
        """
        CREATE TABLE core.fact_running_progrid (
            sales_date            date NOT NULL,
            cj_item               text NOT NULL,
            rsp_unit              text,
            rsp                   numeric,
            pro_type              text NOT NULL,
            pro_theme             text,
            promo_price_per_piece numeric,
            valid_from            date NOT NULL,
            valid_to              date NOT NULL,
            slot_key              text NOT NULL,
            promo_row_id          bigint NOT NULL,
            is_cancelled          boolean NOT NULL DEFAULT false,
            uploaded_at           timestamptz,
            source_file           text,
            UNIQUE NULLS NOT DISTINCT (sales_date, cj_item, rsp_unit, promo_row_id)
        );
        CREATE INDEX ON core.fact_running_progrid (sales_date, cj_item);
        CREATE INDEX ON core.fact_running_progrid (slot_key, sales_date);
        """
    )
    op.execute(
        """
        CREATE TABLE ops.promotion_change_log (
            change_id            bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            detected_at          timestamptz NOT NULL DEFAULT now(),
            uploaded_at          timestamptz NOT NULL,
            previous_uploaded_at timestamptz NOT NULL,
            slot_key             text NOT NULL,
            cj_item              text NOT NULL,
            rsp_unit             text,
            sales_date           date NOT NULL,
            rn                   integer NOT NULL,
            change_type          text NOT NULL,
            changed_fields       text[],
            old_state            jsonb,
            new_state            jsonb,
            old_pro_type         text,
            new_pro_type         text,
            old_pro_theme        text,
            new_pro_theme        text,
            old_promo_price_per_piece numeric,
            new_promo_price_per_piece numeric,
            old_rsp              numeric,
            new_rsp              numeric,
            UNIQUE (uploaded_at, slot_key, sales_date, rn)
        );
        CREATE INDEX ON ops.promotion_change_log (slot_key, sales_date DESC);
        CREATE INDEX ON ops.promotion_change_log (uploaded_at DESC);
        """
    )
    for view in RPT_VIEWS:
        op.execute(_view_sql(view))

    # ETL runtime on the new tables (stg/core via existing defaults; ops is new).
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE "
        "ON stg.stg_progrid_raw, core.fact_running_progrid, "
        "ops.promotion_change_log TO etl_writer"
    )
    op.execute(
        "ALTER DEFAULT PRIVILEGES IN SCHEMA ops "
        "GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO etl_writer"
    )
    # Consumers see the contract and this ONE audit table (R6 exception, §8.1.1).
    op.execute("GRANT USAGE ON SCHEMA rpt TO reporting")
    op.execute(
        "GRANT SELECT ON ALL TABLES IN SCHEMA rpt TO reporting"
    )
    op.execute(
        "ALTER DEFAULT PRIVILEGES IN SCHEMA rpt "
        "GRANT SELECT ON TABLES TO reporting"
    )
    op.execute("GRANT SELECT ON ops.promotion_change_log TO reporting")


def downgrade() -> None:
    from alembic import op

    op.execute("REVOKE SELECT ON ops.promotion_change_log FROM reporting")
    for view in ("v_unmapped_values", "v_promotion_effective_periods",
                 "v_promotion_changes", "v_fact_progrid_bestprice"):
        op.execute(f"DROP VIEW IF EXISTS rpt.{view}")
    op.execute("DROP TABLE IF EXISTS ops.promotion_change_log")
    op.execute("DROP TABLE IF EXISTS core.fact_running_progrid")
    op.execute("DROP TABLE IF EXISTS stg.stg_progrid_raw")
