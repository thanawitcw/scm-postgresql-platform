"""Initial ops schema: etl_run + dq_result + dataset (§10, §10.1)."""

revision = "0001_ops"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    from alembic import op

    op.execute("CREATE SCHEMA IF NOT EXISTS ops")
    op.execute(
        """
        CREATE TABLE ops.etl_run (
            run_id        uuid PRIMARY KEY,
            pipeline      text NOT NULL,
            target_table  text NOT NULL,
            run_date      date NOT NULL,
            started_at    timestamptz NOT NULL,
            finished_at   timestamptz,
            status        text NOT NULL,
            rows_in       bigint,
            rows_out      bigint,
            duration_ms   integer,
            error_text    text
        );
        CREATE INDEX ON ops.etl_run (target_table, started_at DESC);
        """
    )
    op.execute(
        """
        CREATE TABLE ops.dq_result (
            run_id      uuid REFERENCES ops.etl_run(run_id),
            check_name  text NOT NULL,
            severity    text NOT NULL,
            passed      boolean NOT NULL,
            observed    text,
            expected    text,
            checked_at  timestamptz DEFAULT now()
        );
        """
    )
    op.execute(
        """
        CREATE TABLE ops.dataset (
            dataset_key   text PRIMARY KEY,
            dataset_name  text NOT NULL,
            file_name     text,
            file_path     text NOT NULL,
            uploaded_at   timestamptz,
            business_date_min date,
            business_date_max date,
            content_hash  text,
            last_run_id   uuid REFERENCES ops.etl_run(run_id),
            last_seen_at  timestamptz NOT NULL DEFAULT now()
        );
        CREATE INDEX ON ops.dataset (uploaded_at DESC);
        """
    )
    # §11.2: ETL runtime writes run-log rows but never owns schema.
    op.execute("GRANT USAGE ON SCHEMA ops TO etl_writer")
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE "
        "ON ops.etl_run, ops.dq_result, ops.dataset TO etl_writer"
    )


def downgrade() -> None:
    from alembic import op

    op.execute("DROP TABLE IF EXISTS ops.dataset")
    op.execute("DROP TABLE IF EXISTS ops.dq_result")
    op.execute("DROP TABLE IF EXISTS ops.etl_run")
