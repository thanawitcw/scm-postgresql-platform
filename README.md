# SCM Data Platform (PostgreSQL + Python ETL)

A small, production-shaped data platform for supply-chain reporting. It ingests
eleven source files per business day, validates them **before** any write,
loads them idempotently into a dimensional model, enforces data-quality rules,
and exposes reporting views for BI tools.

The design goal was to replace a set of ad-hoc Excel/Power BI refreshes with one
auditable pipeline: every load is a transaction, every run is logged, and a
re-run on the same file is a no-op.

---

## Highlights

- **Layered model** — `stg` (file-faithful landing) → `core` (dimensions and
  facts) → `rpt` (reporting views).
- **Validate-then-load** — `etl validate` checks all sources and aborts before
  any write.
- **Idempotent** — one transaction per load (`DELETE` + `INSERT` + DQ check,
  single `COMMIT`); same file + same hash = no-op.
- **Data quality gates** — not-empty, uniqueness, referential checks, unmapped
  value reports; failures are logged with the offending dataset.
- **Run log** — `ops.etl_run` records target, status, row counts, timings, and
  error text; `ops.dq_result` records rule outcomes.
- **Promotion history** — SCD-style change log with effective periods and a
  best-price view.
- **Operations** — PowerShell task scheduler entry, nightly `pg_dump` backup with
  retention, restore drill, and a Teams/Power Automate notification hook.
- **Tests** — unit tests for parsing/normalisation plus an optional live-DB
  regression suite.

---

## Architecture

```
                 ┌─────────────────────────────────────────────┐
 source files    │  etl validate  (read-only, fail fast)       │
 (xlsx / csv /   └──────────────────────┬──────────────────────┘
  Access)                              │
      │                                ▼
      ▼                    ┌───────────────────────┐
 ┌─────────┐   extract     │  stg.*  (raw shape)   │
 │ sources │ ────────────► │  file-faithful rows   │
 └─────────┘               └───────────┬───────────┘
                                       │ transform + DQ
                                       ▼
                           ┌───────────────────────┐
                           │  core.*  dims/facts   │
                           │  star schema          │
                           └───────────┬───────────┘
                                       │ views
                                       ▼
                           ┌───────────────────────┐
                           │  rpt.*  reporting     │
                           │  (SELECT only role)   │
                           └───────────────────────┘
```

### Model

| Layer | Objects |
|---|---|
| `stg` | `stg_csv_daily_sales`, `stg_dc_daily`, `stg_demandfc_history`, `stg_goods_receipt`, `stg_oos_status`, `stg_product_list`, `stg_progrid_raw`, `stg_supplier_list`, `stg_weekly_stock` |
| `core` | `dim_channel`, `dim_date`, `dim_dc`, `dim_item`, `dim_supplier`, `fact_daily_sales`, `fact_daily_dc_stock`, `fact_demandfc_history`, `fact_goods_receipt`, `fact_oos_status`, `fact_running_progrid`, `fact_weekly_stock` |
| `ops` | `dataset`, `dq_result`, `etl_run`, `promotion_change_log` |
| `rpt` | `v_fact_progrid_bestprice`, `v_freshness`, `v_promotion_changes`, `v_promotion_effective_periods`, `v_unmapped_values` |

Schema changes are managed exclusively by Alembic (`alembic/versions/0001`–`0011`);
loads never issue DDL.

---

## Tech stack

- Python 3.12 (managed by `uv`, see `.python-version` / `uv.lock`)
- PostgreSQL 18 (native Windows service)
- SQLAlchemy 2.x + Alembic, `psycopg` v3
- pandas / openpyxl / pyodbc (Access sources)
- Typer CLI, pytest
- PowerShell for scheduling, backup, and environment checks

---

## Quick start

Prerequisites: PostgreSQL 18 running locally, Python 3.12, and (for Access
sources) the Microsoft Access Database Engine x64.

```powershell
# 1. dependencies
powershell -ExecutionPolicy Bypass -File ops\setup.ps1
#    or: uv python install 3.12 && uv sync

# 2. connection settings (never commit them)
Copy-Item .env.example .env
# uncomment and set PGHOST/PGPORT/PGDATABASE/PGUSER/PGPASSWORD in .env

# 3. schema
$env:SCM_OWNER_URL = "postgresql+psycopg://scm_admin:<pw>@127.0.0.1:5432/scm"
.\.venv\Scripts\python.exe -m alembic upgrade head

# 4. connectivity + source validation
.\.venv\Scripts\python.exe -m etl health
.\.venv\Scripts\python.exe -m etl validate

# 5. load a business date
.\.venv\Scripts\python.exe -m etl run --date 2026-10-27
```

Source locations are environment-driven. By default the ETL looks for the
network share at `\\fileserver\Supply Chain` and the synced promotion files under
`data/stock_report`; override with `SHARE_ROOT` and `STOCK_REPORT_ROOT`.
Nothing machine-specific is hardcoded in the repository.

### CLI

| Command | Purpose |
|---|---|
| `python -m etl health` | Database connectivity check |
| `python -m etl validate` | Validate every source without writing |
| `python -m etl run --date YYYY-MM-DD` | Dimensions + daily load |
| `python -m etl load-month --channel CJ --month YYYY-MM` | Backfill a sell-out month (or `--file`) |
| `python -m etl load-latest --channel CJ` | Load the newest month file if changed |
| `python -m etl load-progrid` | Snapshot + load promotion grids |
| `python -m etl load-gr [--file NAME]` | Goods receipts (latest annual file) |
| `python -m etl load-plan [--file NAME]` | Demand forecast plan (latest snapshot) |
| `python -m etl load-weekly-stock` | Weekly stock workbook |
| `python -m etl load-dc-stock --file <name>` | Daily DC stock |
| `python -m etl load-oos-status` | Out-of-stock status |

---

## Project layout

```
postgreSQL/
├── alembic/                 # migration environment + versioned migrations
├── etl/
│   ├── cli.py               # Typer commands
│   ├── config.py            # env-driven settings (pydantic-settings)
│   ├── validate.py          # pre-load source checks
│   ├── runlog.py            # ops.etl_run / ops.dq_result logging
│   ├── db/                  # engine, transaction, load helpers
│   ├── history/             # SCD / change-log writers
│   ├── pipelines/           # dim_* and fact_* transforms
│   ├── quality/             # DQ rules and checks
│   └── sources/             # one adapter per source format
├── sql/
│   ├── dq/                  # assertion SQL
│   └── rpt/                 # reporting views
├── ops/
│   ├── setup.ps1            # bootstrap the locked venv
│   ├── daily_run.ps1        # validate → load all → notify
│   ├── register_task.ps1    # Task Scheduler registration
│   ├── backup.ps1           # pg_dump + 14-day retention
│   └── move_pgdata_to_d.ps1 # one-time cluster move
├── tests/
│   ├── unit/                # parsing / normalisation
│   └── integration/         # live-DB regression (opt-in)
├── docs/                    # credentials runbook
├── ARCHITECTURE.md          # design decisions and full build record
├── deployment.md            # machine setup runbook
└── SCM_Project_Structure.html  # visual schema/status overview
```

---

## Testing

```bash
.\.venv\Scripts\python.exe -m pytest -q
```

The integration test is skipped unless a loaded database is available:

```powershell
$env:SCM_INTEGRATION = "1"
.\.venv\Scripts\python.exe -m pytest tests\integration -q
```

---

## Operations

- `ops\daily_run.ps1` — validate, run every step, log to `ops\logs\daily_YYYYMMDD.log`,
  and post a green/red card when `SCM_TEAMS_WEBHOOK` is set. Registered for
  weekdays at 10:00 via `ops\register_task.ps1`.
- `ops\backup.ps1` — compressed `pg_dump` to `D:\pg_backup`, 14-day retention;
  fails fast when `PGPASSWORD` is missing instead of hanging on a prompt.
- `docs\credentials.md` — role model (`scm_admin`, `etl_writer`, `reporting`) and
  rotation procedure. Secrets live in Windows Credential Manager, never in the repo.

---

## Repository notes

- No business data, logs, database dumps, or credentials are committed.
  `ops/snapshot/`, `ops/logs/`, `.env`, and `.venv/` are git-ignored.
- `ARCHITECTURE.md` contains the full decision log and is the best starting point
  for *why* the system looks like this; `deployment.md` covers the Windows host.
- Status at the time of writing: 11 sources wired, ~7.7M fact rows, all steps
  green on a daily schedule.
