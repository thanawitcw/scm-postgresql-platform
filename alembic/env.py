"""Alembic environment. Uses SCM_OWNER_URL (schema-owner role), never the ETL runtime role."""

from __future__ import annotations

import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)


def get_url() -> str:
    url = os.environ.get("SCM_OWNER_URL", "")
    if not url:
        raise RuntimeError(
            "SCM_OWNER_URL is not set. Migrations run as scm_admin "
            "(admin+owner merged, D13), never as etl_writer. See .env.example."
        )
    return url


def run_migrations_offline() -> None:
    context.configure(url=get_url(), literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = create_engine(get_url())
    with engine.connect() as connection:
        context.configure(connection=connection)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
