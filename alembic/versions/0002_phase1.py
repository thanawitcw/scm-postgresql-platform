"""Phase 1: reference & product (S8, S9).

stg.stg_product_list / stg.stg_supplier_list — explicit DDL, no constraints (§3).
core.dim_date (seeded 2020-2030), dim_dc (seeded D001/D002/D004/D005),
dim_supplier, dim_item (natural UNIQUE on cj_item / supplier_code, M1).
"""

revision = "0002_phase1"
down_revision = "0001_ops"
branch_labels = None
depends_on = None


def upgrade() -> None:
    from alembic import op

    op.execute("CREATE SCHEMA IF NOT EXISTS stg")
    op.execute("CREATE SCHEMA IF NOT EXISTS core")

    # ── stg: close to source, disposable, no constraints ──────────────
    op.execute(
        """
        CREATE TABLE stg.stg_product_list (
            division text, buyer_team text, oem_brand text, non_vat boolean,
            supplier_code text, shm_item text, cj_item text, td_item text, dcm_item text,
            cj_description text, description text, cat text, sub_cat text, brand text,
            item_group text, first_so_date timestamp, cj_status text,
            cj_status_as_of timestamp, td_status text, td_status_as_of timestamp,
            dcm_status text, product_shelf_life integer,
            pc_cartons double precision, pc_pack double precision,
            pack_cartons double precision, buy_price double precision,
            sale_price_dcm double precision, sale_price_cj double precision,
            ti integer, hi integer, cartons_pallet integer, ship_from_code text,
            barcode_cartons text, ctn_wide_cc integer, ctn_long_cc integer,
            ctn_high_cc integer, gross_weight_kg integer, barcode_pc text,
            barcode_pack text, country text, transport_owner_by_item text,
            loading_group text, loading_note text, machine_group text,
            group_of_sup integer, commodities_flag boolean, fc_item boolean,
            base_lt integer, moq_number integer, moq_text text, moq_group text,
            dc1_lt integer, dc2_lt integer, dc3_lt integer, dc4_lt integer,
            dc5_lt integer, business_group text, rec_no integer,
            ship_from_condition text,
            uploaded_at timestamptz, source_file text
        );
        """
    )
    op.execute(
        """
        CREATE TABLE stg.stg_supplier_list (
            supplier_code text, supplier_name text, supplier_type text,
            product text, owner text, status text, credit_term integer,
            country text, division text,
            uploaded_at timestamptz, source_file text
        );
        """
    )

    # ── core.dim_date: generated forward, 2020-2030 ───────────────────
    op.execute(
        """
        CREATE TABLE core.dim_date (
            date_key      integer PRIMARY KEY,
            date          date NOT NULL UNIQUE,
            year          smallint NOT NULL,
            quarter       smallint NOT NULL,
            month         smallint NOT NULL,
            week_iso      smallint NOT NULL,
            day_of_week   smallint NOT NULL,
            is_weekend    boolean NOT NULL,
            is_holiday    boolean NOT NULL DEFAULT false,
            thai_year     smallint NOT NULL,
            buddhist_year smallint NOT NULL,
            month_start   date NOT NULL,
            month_name    text NOT NULL,
            week_start    date NOT NULL,
            day_of_month  smallint NOT NULL
        );
        INSERT INTO core.dim_date
            (date_key, date, year, quarter, month, week_iso, day_of_week,
             is_weekend, is_holiday, thai_year, buddhist_year,
             month_start, month_name, week_start, day_of_month)
        SELECT to_char(d, 'YYYYMMDD')::int,
               d::date,
               extract(year FROM d)::smallint,
               extract(quarter FROM d)::smallint,
               extract(month FROM d)::smallint,
               extract(week FROM d)::smallint,
               extract(isodow FROM d)::smallint,
               extract(isodow FROM d) IN (6, 7),
               false,
               (extract(year FROM d) + 543)::smallint,
               (extract(year FROM d) + 543)::smallint,
               date_trunc('month', d)::date,
               trim(to_char(d, 'Month')),
               date_trunc('week', d)::date,
               extract(day FROM d)::smallint
        FROM   generate_series(date '2025-01-01', date '2030-12-31',
                               interval '1 day') AS d
        ON CONFLICT DO NOTHING;
        """
    )

    # ── core.dim_dc: static seed from the S7 Plant mapping (§5.1.1) ────
    op.execute(
        """
        CREATE TABLE core.dim_dc (
            dc_key     bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            plant_code text NOT NULL UNIQUE,
            dc_name    text NOT NULL,
            loaded_at  timestamptz NOT NULL DEFAULT now()
        );
        INSERT INTO core.dim_dc (plant_code, dc_name) VALUES
            ('D001', 'DC1'), ('D002', 'DC2'), ('D004', 'DC4'), ('D005', 'DC5')
        ON CONFLICT (plant_code) DO NOTHING;
        """
    )

    # ── core.dim_supplier (S9; blank placeholder filtered at extract) ──
    op.execute(
        """
        CREATE TABLE core.dim_supplier (
            supplier_key  bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            supplier_code text NOT NULL UNIQUE,
            supplier_name text,
            supplier_type text,
            status        text,
            credit_term   integer,
            country       text,
            division      text,
            loaded_at     timestamptz NOT NULL DEFAULT now(),
            source_file   text
        );
        """
    )

    # ── core.dim_item: 2 columns. One row per cj_item; every supplier that
    # carries the item is concatenated (','-separated, alphabetical) so no
    # shm_item variant is ever dropped. Names resolve via dim_supplier;
    # codes with no master row fall back to the raw code (never lost).
    op.execute(
        """
        CREATE TABLE core.dim_item (
            item_key     bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            cj_item      text NOT NULL UNIQUE,
            supplier_name text
        );
        """
    )

    # §11.2: runtime writes data, never owns schema.
    op.execute("GRANT USAGE ON SCHEMA stg, core TO etl_writer")
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA stg, core "
        "TO etl_writer"
    )
    op.execute(
        "ALTER DEFAULT PRIVILEGES IN SCHEMA stg, core "
        "GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO etl_writer"
    )


def downgrade() -> None:
    from alembic import op

    op.execute("DROP TABLE IF EXISTS core.dim_item")
    op.execute("DROP TABLE IF EXISTS core.dim_supplier")
    op.execute("DROP TABLE IF EXISTS core.dim_dc")
    op.execute("DROP TABLE IF EXISTS core.dim_date")
    op.execute("DROP TABLE IF EXISTS stg.stg_product_list")
    op.execute("DROP TABLE IF EXISTS stg.stg_supplier_list")
