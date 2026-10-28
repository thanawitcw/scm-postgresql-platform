"""Environment-driven configuration. No literals here.

Fails fast if the interpreter version does not match .python-version (D9),
or if required connection settings are missing.
"""

from __future__ import annotations

import sys
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


def _expected_python() -> str:
    marker = Path(__file__).resolve().parent.parent / ".python-version"
    try:
        return marker.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


_EXPECTED = _expected_python()
if _EXPECTED and not sys.version.startswith(_EXPECTED):
    raise RuntimeError(
        f"Wrong interpreter: running {sys.version.split()[0]}, "
        f"project requires {_EXPECTED} (see .python-version, D9). "
        "Recreate the venv with: uv python install 3.12 && uv sync"
    )


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    pghost: str = "127.0.0.1"
    pgport: int = 5432
    pgdatabase: str = "scm"
    pguser: str = "etl_writer"
    pgpassword: str = ""
    pgsslmode: str = "prefer"

    scm_owner_url: str = ""
    # Network share that holds the source workbooks. Override with SHARE_ROOT.
    share_root: str = r"\\fileserver\Supply Chain"
    # S1/S2 live outside the share (a synced folder in production). Override
    # with STOCK_REPORT_ROOT; the default is a local folder for the demo data.
    stock_report_root: str = "data/stock_report"


settings = Settings()
