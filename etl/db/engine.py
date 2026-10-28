"""Connection factory. Pool sized for a single-host scheduled ETL."""

from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine

from etl.config import settings


def dsn() -> str:
    return (
        f"postgresql+psycopg://{settings.pguser}:{settings.pgpassword}"
        f"@{settings.pghost}:{settings.pgport}/{settings.pgdatabase}"
        f"?sslmode={settings.pgsslmode}"
    )


def get_engine() -> Engine:
    # Small pool: the ETL is a single scheduled process, not a web server.
    return create_engine(dsn(), pool_size=5, max_overflow=0, pool_pre_ping=True)
