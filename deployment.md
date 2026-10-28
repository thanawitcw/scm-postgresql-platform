# SCM Data Platform — Deployment Runbook

This document contains machine-level installation and operational steps intentionally kept out of
`ARCHITECTURE.md`. The architecture document defines the required deployment state; this runbook defines
how to establish it on the Windows host.

## PostgreSQL 18 native installation

**One-time machine setup (requires administrator):**

1. Install **PostgreSQL 18 x64** from the official EDB installer. Uncheck "Install Stack Builder"
   unless extra tools are specifically required. Keep the default data directory or set an explicit one,
   e.g. `C:\pgdata\18`.
2. During setup, uncheck **"Open port for remote access"** initially. Add it back only when the Phase 0
   consumer-access decision requires it.
3. Keep the **superuser password** in Windows Credential Manager or a password manager. Never put it in
   `.env` or a script.
4. Install the **Microsoft Access Database Engine 2016 Redistributable (x64)** for the product master.
   Python bitness and driver bitness must match.
5. Add PostgreSQL's `bin` directory to PATH, then open a new terminal.

**Service:**

```powershell
Get-Service postgresql-x64-18
Restart-Service postgresql-x64-18
```

Recommended configuration:

| Setting | Where | Why |
|---|---|---|
| Start type `Automatic` | `services.msc` | Available at boot without anyone logging in. |
| Listen address | `postgresql.conf` → `listen_addresses` | `localhost` by default; add a LAN IP only when required. |
| Port | `postgresql.conf` → `port` | `5432` by default; change only when needed. |
| Logging | `log_destination`, `logging_collector` | Preserve diagnosable failures after the fact. |
| Archive | `archive_mode`, `archive_command`, `archive_timeout` | Point-in-time recovery. |

A config change needs `Restart-Service postgresql-x64-18` and a new `psql` session.

## Access database driver

The product master is read through ODBC because the ETL now runs natively on Windows:

```python
import pyodbc

CONN = (
    "DRIVER={Microsoft Access Database Engine 2016};"
    "DBQ=\\\\fileserver\\Supply Chain\\iTMS\\SHM_TMS_001_Master.accdb;"
)

with pyodbc.connect(CONN) as conn:
    df = pd.read_sql(f"SELECT * FROM {table}", conn)
```

The ACE driver is a one-time machine installation, not a pip package. Wrap it behind the `Source` ABC
so the driver choice remains invisible to the pipeline.

## Windows service and network setup

Follow the Phase 0 consumer-access decision in `ARCHITECTURE.md`. Do not expose PostgreSQL more broadly
than required. Use explicit firewall scopes and `pg_hba.conf` CIDRs.

## Deployment verification

Before production use, verify:

- PostgreSQL service starts automatically after reboot.
- Authentication uses `scram-sha-256`.
- `reporting` cannot read `stg`, `core`, or `ops`.
- ETL runtime credentials do not have schema-owner privileges.
- Required UNC paths are accessible to the account that runs the scheduled task.
- ACE OLEDB/ODBC access works with the Python interpreter bitness.
- Backup and restore procedures have been tested.
