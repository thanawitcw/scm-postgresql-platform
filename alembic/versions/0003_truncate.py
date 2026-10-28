"""Phase 1 fix: stg truncate-then-load needs the TRUNCATE privilege.

GRANT SELECT/INSERT/UPDATE/DELETE does not cover TRUNCATE — it is a
separate grantable privilege. Without it every stg load fails.
"""

revision = "0003_truncate"
down_revision = "0002_phase1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    from alembic import op

    op.execute("GRANT TRUNCATE ON ALL TABLES IN SCHEMA stg TO etl_writer")
    op.execute(
        "ALTER DEFAULT PRIVILEGES IN SCHEMA stg "
        "GRANT SELECT, INSERT, UPDATE, DELETE, TRUNCATE ON TABLES TO etl_writer"
    )


def downgrade() -> None:
    from alembic import op

    op.execute("REVOKE TRUNCATE ON ALL TABLES IN SCHEMA stg FROM etl_writer")
