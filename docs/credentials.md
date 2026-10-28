# SCM Data Platform — Credentials & Role Runbook

> **Rule R5: no secret is ever written in this repo.** This file contains
> locations, procedures, and placeholders only. If you find a real password
> in any committed file, rotate it immediately and remove it from history.

## 1. Role inventory (3 roles + built-in `postgres`)

| Role | Kind | Privileges | Reachable from |
|---|---|---|---|
| `scm_admin` | human admin + Alembic migration role (D13: merged with `scm_owner`, which was dropped) | SUPERUSER, LOGIN; owns `scm`, `scm_test` | loopback only (`pg_hba.conf` rejects it on `0.0.0.0/0`, `::/0`) |
| `etl_writer` | ETL runtime | LOGIN, not superuser; SELECT/INSERT/UPDATE/DELETE on `ops.*` (+ `stg`/`core` tables as later migrations grant) | loopback only (same reject lines) |
| `reporting` | consumer group (D6) | NOLOGIN group; per-person logins are members; SELECT on `rpt` only (granted in Phase 5) | `10.0.0.0/24` on database `scm` |
| `postgres` | built-in superuser | SUPERUSER | loopback only (reject lines kept) |

## 2. Where each secret lives

| Secret | Stored in | Never in |
|---|---|---|
| `scm_admin` password | Windows Credential Manager (or team password manager) | repo, `.env`, scripts |
| `etl_writer` password | Windows Credential Manager; injected as `PGPASSWORD` env var by Task Scheduler | repo, `.env`, scripts |
| Per-person `reporting_*` passwords | password manager, one entry per analyst (Phase 5) | shared chat, repo |
| `SCM_OWNER_URL` (Alembic, uses `scm_admin`) | shell env var at migrate time, e.g. `$env:SCM_OWNER_URL="postgresql+psycopg://scm_admin:<pw>@127.0.0.1:5432/scm"` | `.env`, `alembic.ini` |
| QNAP/share SMB credential | Windows Credential Manager (Windows handles UNC auth) | config files — this was a live leak in the old compose file |

`.env` (from `.env.example`) is git-ignored and is the only file that may
hold local dev values. CI/scheduler use env vars, not files.

## 3. Set / rotate a password

Run as `scm_admin` over loopback (replace `<new-pw>`; generate with a
password manager, e.g. 24+ random chars):

```sql
-- ETL runtime credential:
ALTER ROLE etl_writer WITH LOGIN PASSWORD '<new-pw>';

-- Human admin credential:
ALTER ROLE scm_admin WITH LOGIN SUPERUSER PASSWORD '<new-pw>';
```

Then verify the affected path before closing the session:

```powershell
# ETL runtime path (must print OK ... + a SUCCESS row in ops.etl_run):
$env:PGHOST="127.0.0.1"; $env:PGPORT="5432"; $env:PGDATABASE="scm"
$env:PGUSER="etl_writer"; $env:PGPASSWORD="<new-pw>"
.\.venv\Scripts\python.exe -m etl run --date <today-YYYY-MM-DD>

# Consumer enforcement (R6 — this MUST FAIL; if it succeeds, stop and fix grants):
psql -h 127.0.0.1 -U <person-login> -d scm -c "SELECT * FROM core.dim_item"
```

If `scm_admin`/`postgres` passwords are both lost: as a Windows admin,
temporarily set `local all all trust` in
`C:\Program Files\PostgreSQL\18\data\pg_hba.conf`,
`Restart-Service postgresql-x64-18` (never `pg_ctl` on a service cluster —
§0.2 trap), reset the passwords, revert to `scram-sha-256`, restart again,
and reload-verify with `SELECT * FROM pg_hba_file_rules`.

## 4. Network / auth layout (reference, not procedure)

- `listen_addresses = 'localhost, 10.0.0.10'`, port `5432` (D2, D3).
  `localhost` must stay — ETL, `psql`, and `pg_reload_conf()` use loopback.
- `ssl = off` (D12 — outstanding before Phase 5: self-signed cert +
  `sslmode=require`, needs elevated step for the service-account key ACL).
- `pg_hba.conf`: loopback `scram-sha-256`; explicit per-role `reject` lines
  for `scm_admin`, `etl_writer`, `postgres` on `0.0.0.0/0` and `::/0`
  (one line per role — brace lists do not expand on PG 18.4, §0.1);
  `reporting` allowed on `scm` from `10.0.0.0/24`.
- After any `pg_hba.conf` edit: `SELECT pg_reload_conf();` (no restart),
  then check `SELECT line_number, user_name, address, auth_method, error
  FROM pg_hba_file_rules;` — a non-null `error` or a `{`/`}` in
  `user_name` means the rule is not doing what it looks like.

## 5. Migration checklist (new machine / rebuild)

1. Install PostgreSQL 18 x64 (service `Automatic`), ACE x64 driver, add
   `C:\Program Files\PostgreSQL\18\bin` to PATH (`docs/deployment.md`).
2. Recreate roles (names + flags in §1), set passwords per §3.
3. Apply `pg_hba.conf` + `listen_addresses` per §4, reload, verify.
4. `powershell -ExecutionPolicy Bypass -File ops\setup.ps1`
   (Python 3.12, locked deps from `uv.lock`).
5. `$env:SCM_OWNER_URL=...; .\.venv\Scripts\python.exe -m alembic upgrade head`
6. Run §3 verifications (ETL OK as `etl_writer`, R6 denial for consumers).
7. Register scheduler: `ops\register_task.ps1`; configure nightly
   `ops\backup.ps1`; perform and time one restore drill (§11.4).
8. DHCP reservation for `10.0.0.10` before Phase 5 cutover (§0.3).
