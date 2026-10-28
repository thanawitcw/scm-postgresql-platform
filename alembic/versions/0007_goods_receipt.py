"""Phase 4b (1/3): goods receipt (S5).

stg.stg_goods_receipt — raw annual files, no constraints.
core.fact_goods_receipt — grain (report_date x po_no x cj_item x dc_key),
158 measured dup rows collapse by SUM in the pipeline (additive measures).
GR_DC arrives as DC names (DC1/DC2/DC4/DC5) and resolves via dim_dc.dc_name.
"""

revision = "0007_goods_receipt"
down_revision = "0006_phase3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    from alembic import op

    op.execute(
        """
        CREATE TABLE stg.stg_goods_receipt (
            report_date      date,
            received_date    date,
            po_no            text,
            cj_item          text,
            gr_dc            text,
            gr_qty_piece     numeric,
            gr_value_in_vat  numeric,
            uploaded_at      timestamptz,
            source_file      text
        );
        """
    )
    op.execute(
        """
        CREATE TABLE core.fact_goods_receipt (
            report_date      date NOT NULL,
            received_date    date,
            po_no            text NOT NULL,
            cj_item          text,
            dc_key           bigint NOT NULL REFERENCES core.dim_dc(dc_key),
            gr_qty_piece     numeric,
            gr_value_in_vat  numeric,
            CONSTRAINT uq_fact_goods_receipt_grain UNIQUE
                (report_date, po_no, cj_item, dc_key)
        );
        CREATE INDEX ON core.fact_goods_receipt (received_date, dc_key);
        CREATE INDEX ON core.fact_goods_receipt (cj_item, received_date);
        """
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE "
        "ON stg.stg_goods_receipt, core.fact_goods_receipt TO etl_writer"
    )


def downgrade() -> None:
    from alembic import op

    op.execute("DROP TABLE IF EXISTS core.fact_goods_receipt")
    op.execute("DROP TABLE IF EXISTS stg.stg_goods_receipt")
