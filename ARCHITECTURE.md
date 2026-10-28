# SCM Data Platform — Architecture & Build Plan

**Status:** Built and loading daily (Phases 0–4b complete, 2026-10-06; ~7.3M fact rows).
Greenfield design phase is over — this document is now the running record.
**Owner:** Supply Chain Analytics
**Database:** PostgreSQL 18, native Windows service, local, no cloud.
**Purpose:** Single source of truth for the Supply Chain team, consumed by Power BI and other reporting tools.

---

## 0. Build decisions log

Decisions taken during implementation that differ from, or refine, the design below.
Each records what was ruled and why, so it is not re-litigated. Nothing here weakens
a rule in §6.

| # | Question | Ruling | Rationale |
|---|---|---|---|
| **D1** | PostgreSQL major version | **18.4**, not 16 | The §2 rationale for 16 was licensing and the SQL Server Express size cap, not features. A native 18.4 service (`postgresql-x64-18`) was already installed, Automatic, with `scram-sha-256` in force. Reinstalling to 16 would cost a day and add a second installed version. All §2 / §11.1 / §12 references to 16 are amended to 18. |
| **D2** | Port | **5432** | The native install had been placed on 5433 while Docker held 5432. Docker is now torn down (§2.1 reassigned it to a sandbox role), so the documented default applies. |
| **D3** | `listen_addresses` | `'localhost, 10.0.0.10'` | §11.3's default is localhost; LAN exposure is now approved (D5), so the host's LAN address is added. **`localhost` must be retained** — the ETL, `psql` and `pg_reload_conf()` all connect over loopback, and `pg_hba.conf` confines the privileged roles to it. Omitting it was an observed build error: the server came up unreachable on 127.0.0.1. |
| **D4** | Superuser identity | **`scm_admin`**, accepted from loopback only | §11.2 forbids the ETL running as `postgres`. `scm_admin` replaces it as the human admin account and `postgres` is rejected from every non-loopback address. |
| **D5** | Consumer access boundary | **Corporate LAN, `10.0.0.0/24`** | Resolves the §11.3 / §15 gate. ≤5 supply-chain analysts who know SQL, following the precedent already set by Business Development's SQL Server at `10.0.0.20`. Scope is one subnet, never `0.0.0.0/0`. |
| **D6** | Consumer credentials | **One login per person**, all members of a `reporting` group role | A shared `reporting` password among 5 people makes it impossible to attribute a query or revoke one person's access without rotating everyone's. |
| **D7** | Privileged roles from the LAN | **Rejected outright** | `pg_hba.conf` carries explicit `reject` lines for `scm_admin`, `scm_owner`, `etl_writer` and `postgres` on `0.0.0.0/0` and `::/0`. First-match-wins places these after the loopback rules, so they are reached only by connections that did not match loopback. Password strength for those roles becomes a secondary concern, not the control. **One line per role — a brace list does not work here:** see §0.1. |
| **D12** | Transport encryption | **NOT YET ENABLED — outstanding before Phase 5** | `ssl = off` and no certificate exists on the host. Note the precise exposure: `scram-sha-256` never sends the plaintext password, so the credential itself is not directly leaked. But without TLS, a passive sniffer on `10.0.0.0/24` captures the SCRAM handshake (salt, iterations, `StoredKey`) and can mount an **offline dictionary attack**, and every query and result crosses the wire unencrypted. Mitigation is a self-signed server certificate plus `sslmode=require` on the clients; self-signed is sufficient because the protection needed is against eavesdropping, not impersonation of the host by a third party. Requires a private-key ACL granting read access to the service account (`NT AUTHORITY\NetworkService`), so it needs an elevated step. |
| **D8** | Legacy database data | **Not migrated.** Fresh `scm`; `scm_so_vs_fc` and `trial_oos` deleted | Both were `public`-schema `CREATE TABLE AS` output with no declared grain, no constraints and no lineage. Every table is re-derivable from the share, and the new model rebuilds all of it. `ops/migration_from_docker.md` Path B was considered and rejected — it would import tables that violate R2 in order to discard them. |
| **D9** | Python | **3.12**, uv-managed, `.python-version` committed | Wheels are guaranteed for pandas, `psycopg` and `pyodbc`. The machine's system Python is 3.14, where driver wheel availability is not guaranteed. |
| **D10** | PostgreSQL driver | **`psycopg` v3** (`psycopg[binary]`), superseding `psycopg2-binary` | psycopg2 is in maintenance mode. SQLAlchemy 2.x supports both, so this changes the dependency, not the code. |
| **D11** | Docker | **Torn down**, Desktop retained | All 4 containers, 5 databases, 4 volumes and 4 images deleted (~5 GB). Docker Desktop stays installed per §2.1's reassignment: a disposable sandbox for rehearsing major-version upgrades and risky migrations. |
| **D13** | Admin/owner merge | **`scm_owner` dropped; `scm_admin` (superuser, loopback-only) owns the databases and runs Alembic** | Single-admin team: two superuser names for one person added confusion without adding control. The boundary that matters — ETL runtime (`etl_writer`) ≠ admin, consumers (`reporting`) read `rpt` only — is unchanged. `pg_hba.conf` reject lines for `scm_owner` removed; `SCM_OWNER_URL` now points at `scm_admin`. |
| **D14** | Duplicate `CJ_Item` in S8 | **One dim row per `cj_item` by winner rule**: `EXISTING` lifecycle first, then latest `cj_status_as_of`, then lowest `shm_item`/`rec_no` | Measured 2026-10-06: 70 `CJ_Item` values repeat across 176 rows (pack-size variants, e.g. 500/550ml, Existing vs Discontinuous). Without a winner the fact join fans out. All source rows stay in `stg`; 3 NULL-`cj_item` (NPD) rows never enter the dim. `Group` now has 4 values (`EXISTING` 687 / `DISCONTINUOUS` 1668 / `NPD` 226 / `PRE-DISCONTINUED` 31); `is_active` is true only for `EXISTING`. **Retired 2026-10-06: dim_item reshaped to 2 columns (`cj_item` PK, `supplier_name` with `,`-concatenated names); variants collapse by aggregation, winner logic removed.** |
| **D15** | Sellout grain + orphan items | **Fact grain is (date × item × channel × pro_type × pro_theme × price × is_main)** — the source is promo-attributed, not one-row-per-date×item as §5.1.1/§8 claimed. **Orphans load with NULL `item_key` + DQ WARN** (5 items, 72 rows over Aug–Sep); the uniqueness key uses natural `cj_item`, not the nullable FK, so orphans can never collide | Shared (date, item) keys CJ↔TD Aug-2026 = **12,631**, exactly the §5.1.1 measurement — the one-fact/two-channel model is confirmed to the row. Dropping orphans would silently undercount sellout; failing on them would wedge the load on master-data gaps. |
| **D16** | Phase-3 load rulings | **(a)** Cancellation = Policy A `never_ran` predicate (cancelled AND `valid_from` ≥ upload date excluded; cancelled-mid-flight keeps its full window). The Phase-3 checklist line "3 `\\xa0cancel` rows must be excluded" is superseded for old-window rows — they classify as cancelled but load; the regression test asserts classification + zero `never_ran` leakage instead. **(b)** Promo-above-RSP (77 promos, ≤2.4×) is DQ WARN, not ERROR. **(c)** Change-log diff pairs multi-promo slots by content order (`rn`), not bare `(slot, date)`. **(d)** `dim_date` extended down to 2024-01-01 (promo windows reach 2024-09-03). **(e)** No `0006` migration: the dim_date 2025-2030 rebuild is recorded in the revised `0002` + manual rebuild, so `0007` chains directly on `0005` | Fact 565,371 daily rows from 15,820 source rows; best-price view 443,415; second identical upload yields 0 log rows. Tie-break spot-checked on live data (7-DAYS@59 beats MAIL@69). |
| **D17** | Access source file | **`SHM_TMS_001_AllUser_New.accdb` (9 MB), not `SHM_TMS_001_Master.accdb`** | The master had grown to 1.9 GB with a live `.laccdb` lock; two full copies per run stalled past 5 min. Same 59/17 columns, same 2,721/512 rows verified before switching. 3 days stale at switch (2026-10-02 vs live). |
| **D18** | Progrid column trim | **S1 10 cols, S2 9 cols** — `division`/`supplier`/`description`/`pro_id_raw` dropped from `stg` (0006 revised, table dropped + rebuilt) | All price/window/type columns retained, so grain, tie-break, slot_key and change detection are unaffected. M5 `pro_id` forensics now live in the file archive only. Copy path (`copy_df` reindex) made explicit `= None` lines redundant — removed. |
| **D19** | Sellout date-format drift | **7 of 43 files are `M/D/YYYY`** (CJ 2026-02/03; TD 2025-10/11/12, 2026-01/09); parser branches on `/` vs `-`, rejects mixed files, asserts single-month containment + CLI asserts file-month match | Proven `M/D` (not `D/M`) by month-file consistency before coding. Also fixed the `load-month` January glob (`2025_1` matched Oct/Nov/Dec) with exact-prefix filtering. |
| **D20** | S6 retired | **`Weekly_Assortment` is gone — no code, no tables, never built** | S11 (weekly stock) and S12 (OOS status) carry assortment + OOS per DC per week. Remaining §references are design record. |
| **D21** | `ref_pro_type` removed | **Dropped live + from 0006; `pro_type` is free text; `v_unmapped_values` uses an inline canonical set** | The table had been dropped by an unknown admin action (FK + seed file too); restored from backup, then removed deliberately per owner ruling. Unknown values surface via the view, never defaulted — same guarantee, no lookup table (amends M3 for this column). |
| **D22** | Canonical `pro_type` set, 2026-10-06 | **MAIL, 7-DAYS, EDLP, OTHER, WED, NPD, กระชาก** — `7-DAYS_*`, `3-DAYS`, `14-DAYS` are unmapped by definition | Owner classified the Thai value (was 125 unmapped rows). The three demoted values reappeared in the view (322/252/120 rows) pending their own rulings. |
| **D23** | DC5 zero-fill | **Numerics → 0, dates/text → NULL** where the DC5 block is absent (pre-2026-05-18 weekly, 2025 OOS) | `stg` keeps NULL (file-faithful); the 0-fill applies `stg → fact` via `COALESCE`, reviewable in SQL. |
| **D24** | Negative DC stock | **Clamped to 0 in the fact** (`GREATEST(qty, 0)`); `stg` keeps raw negatives | Explicitly reverses the backorder-signal preservation: owner's Power BI model expects non-negative stock. Recorded so it is not re-litigated as a bug. |
| **D25** | Non-200 `Material` quarantine | **Excluded from the fact + DQ WARN** (1,833 rows over ~250 files since 2026-02-25), kept in `stg` | Failing all of history on systematic contamination was impractical; failing loudly per file stands for NEW unknown codes via the drift guard. Same D15 philosophy as orphan items. |
| **D26** | Mislabeled files | **Content-vs-filename assert on S7** (justified by `DC_End23-07-2025.xlsx` carrying 24.07 data; owner reissued) | The earlier "no date-vs-filename check" assumed trusted files; the incident proved a consistency assert is different from deriving dates. `SENTINEL '0'` dates drop + WARN; exact-dupe rows drop + WARN; conflicting dupes fail. |
| **D27** | Change-log key fix | **`rn` (pair rank) added to `ops.promotion_change_log` key** | Uneven multi-promo slots (extra row on one side of the diff) collided on `(uploaded_at, slot, date)` with a bare `UniqueViolation`. Table was empty, so zero data risk. |
| **D28** | MAIL census update | **5,323 → 5,310** (owner revised the grid 2026-10-06) | Distribution otherwise clean (10 values, no case variants); regression test census updated per its own drift rule. |
| **D29** | Steady-state vs history split | **Daily commands load latest-only (`load-latest`, `load-plan`, `load-gr`, per-date DC, latest weekly/OOS); backfills live in `etl/history/` (sellout, dc_daily, goods_receipt, sales_plan, weekly_stock, oos_status), excluded from the CLI app and the schedule** | A scheduled run can never backfill; a backfill never happens without an explicit manual invocation. `ops/daily_run.ps1` (validate-first, 10 steps) + `SCM-ETL-Daily-1000` (Mon–Fri 10:00) + Teams GREEN/RED cards + `etl validate` pre-load gate. |
| **D30** | Migration practice, this build | **In-place migration edits + drop/rebuild instead of new versions** (0006 trim, ref removal, `rn` key, index retargets) | Deviates from append-only migrations §11.5 deliberately: single database, single admin, every change verified live immediately after. A second database stamped at an older revision would need the same drop+rebuild to converge — acceptable with one DB, must be revisited the day there are two. |
| **D31** | New sources S11/S12 | **`Weekly_Stock` (Sahamit, 92 Monday files) → `fact_weekly_stock` (wide, 272,534 rows); `OOS_Stock_Status` (89 files) → `fact_oos_status` (grain date×item×shm, 86,445 rows)** | Neither was in the §5.1.1 inventory. Weekly is append-only (reloaded weeks fail loudly); OOS_raw mirror keeps all columns incl. DOH/notes per owner ruling. `TierCompany` rides on the weekly fact. `stock_date` from filenames (`%d-%m-%Y`, Monday-asserted); OOS `Stock_Date` is an in-data ISO/serial/datetime mix with an explicit parser. |
| **D32** | ISO date rule, enforced | **Every date parse is ISO-strict unless the source contract states an explicit non-ISO format** — sellout `M/D` branch (D19), dotted S7 `%d.%m.%Y`, and the daily-CSV progrid exports (D39: S1 `%m/%d/%Y`, S2 `%d-%b-%Y`) | The xlsx-era progrid `/`-reject guard was retired with the workbook source; CSV exports are parsed with explicit formats, so drift still fails loudly. GR failed its first non-ISO file the same way. Stored columns are `date` type throughout. |
| **D33** | Extract-failure run logging | **`extract_guarded`: source-read failures write FAILED `etl_run` rows naming pipeline + target + datasource** | Previously a drift/missing-file error escaped with no run-log row at all (proven with a 2099-01 probe). Wired into every CLI command. |
| **D34** | Owner lock files | **Skipped centrally (`etl/sources/scan.py`): `~$*` is transient, never data** | Every share scan uses `iter_data_files`. Known non-data by exact name (`Template Daily DC Stock.xlsx`) is skipped loudly; anything else unexpected fails. |
| **D35** | S10 raw mirror | **No business transform**: `cus_code→cj_item` + type parsing only; `pro_type` case/whitespace variants kept verbatim for `rpt`-time normalization | Grain `(forecast_month × plan_date × item × channel × type × theme)`, 0 dupes over 593,298 rows / 9 snapshots (re-measured 2026-10-14; was 517,214 / 8 at build), 0 orphans. Thousands separators stripped at parse (parsing, not transform). |
| **D36** | `--file` reload unification | **Every file-unit command takes `--file "<exact name>"`** (sellout `--month`/`--file`, DC `--date`→`--file`, GR/plan/weekly/OOS `--file`; progrid rebuilds whole every run so needs none) | Single-unit reloads are snapshot-scoped (plan month, GR year, weekly week, DC/OOS date) — a one-file call can never wipe siblings. Caught live: GR single-year load needed a year-scoped reconciliation fix before it could pass. |
| **D37** | Fact audit columns removed | **`loaded_at` + `source_file` dropped from `fact_daily_sales`, `fact_daily_dc_stock`, `fact_goods_receipt`** (pipeline INSERT + overwritten 0004/0007/0009 + live `ALTER TABLE … DROP COLUMN`, each reloaded and count-verified) | Per-row audit on window-replaced process facts duplicated the run log at 6M+ rows. Provenance stays answerable: every `stg` keeps `uploaded_at` + `source_file`, `ops.etl_run`/`ops.dataset` record each load. `fact_demandfc_history` keeps both — its `rpt.v_freshness` row reads `max(loaded_at)`. Same D30 in-place practice. |
| **D38** | Sales-plan rename | **`stg.stg_sales_plan` → `stg.stg_demandfc_history`, `core.fact_sales_plan` → `core.fact_demandfc_history`** (pipeline constants, CLI/history `target_table`, overwritten `0010` (file since renamed `0010_demand_fc_plan.py`), `rpt.v_freshness`, live `RENAME` + constraint rename + view re-apply, latest snapshot reloaded) | The name now says what it is (§4): demand-forecast history, not the S10 folder label. Table rename only — module file, pipeline ids (`fact_sales_plan[_hist]`), `load-plan` command and S10 dataset key unchanged. |
| **D39** | ProGrid source → daily CSV exports | **S1 `raw_progrid_food_nf_import\ProGrid_Data_YYYYMMDD.csv`, S2 `raw_progrid_pcb\ProGrid_PCB_YYYYMMDD.csv`** in the owner's OneDrive `Data for Stock Report` folder (`STOCK_REPORT_ROOT`), replacing the two share workbooks. Both folders accumulate one full snapshot per business date; `load-progrid` archives **only the newest file per source**, validates the copy, then loads it. When both newest files match the recorded name + sha256, the run is a no-op. Modules renamed `csv_promotion_grid[_pcb].py`; archive stays Option C | UTF-8 with BOM. S1 `Start`/`End` are `M/D/YYYY` and S2 `DD-Mon-YYYY`; S2's header/cells are padded (` Pro_Price `, ` Status `) and are trimmed at parse. S1 is 21 columns, S2 18; the 10/9-column contract (D18) and price mapping are unchanged. Latest = max `YYYYMMDD` in the filename, never mtime (D29). Census after the first CSV load: S1 10,952 + S2 5,189 source rows, MAIL 5,339 (D28). |

### 0.1 A PostgreSQL 18.4 `pg_hba.conf` trap: brace lists do not expand

`pg_hba.conf` is documented as accepting multiple usernames either comma-separated or brace-enclosed. **On this PostgreSQL 18.4 build the brace form does not expand.** It splits on the commas but keeps the braces as literal characters:

```sql
host  all  {scm_admin,scm_owner,etl_writer,postgres}  0.0.0.0/0  reject
```

parses to four usernames of which only the inner two are correct:

```sql
SELECT user_name FROM pg_hba_file_rules WHERE line_number = 124;
--  {"{scm_admin",scm_owner,etl_writer,"postgres}"}
--     ^^^^^^^^^^^ literal brace        ^^^^^^^^^^^^^^ literal brace
```

The consequence is a **silently incomplete security rule**: `scm_owner` and `etl_writer` were correctly rejected from the LAN, while `scm_admin` and `postgres` matched no rule at all. The server still refused those connections — it fails closed — so the failure was invisible, and it would have stayed invisible right up until the day someone added a broader rule that `postgres` *did* match.

**Therefore: one `host` line per role, never a brace list.** After any edit, verify what the server actually parsed:

```sql
SELECT line_number, user_name, address, auth_method, error FROM pg_hba_file_rules;
```

A row with a non-null `error` is a rule the server did not understand; a `user_name` containing `{` or `}` is a rule that is quietly not doing what it looks like it is doing.

### 0.2 Build-order traps hit while standing this up

Recorded because each one cost a cycle and each would recur.

| Trap | Symptom | Fix |
|---|---|---|
| `listen_addresses = '<lan-ip>'` alone | `psql -h 127.0.0.1` gets **connection refused** — the server is up but unreachable over loopback | Always include `localhost`: `listen_addresses = 'localhost, 10.0.0.10'`. Loopback is also where `pg_hba.conf` confines the privileged roles |
| `pg_ctl restart` on a service-managed cluster | Kills the service's postmaster and starts a **detached** one owned by your shell. The Windows service reports `Stopped`; the replacement dies with the shell, taking the database **offline** | Use `Restart-Service postgresql-x64-18` from an elevated shell. Never `pg_ctl` against a cluster owned by the service manager |
| Verifying privileges with `psql` from PowerShell | PowerShell wraps stderr in `NativeCommandError`, so a `FATAL:` arrives as a *string* rather than a non-zero exit code, and naive matching reports success | Match on `FATAL`/`ERROR` in the combined output, not on `$LASTEXITCODE` alone |
| Assuming ownership implies `CONNECT` | `REVOKE … FROM PUBLIC` removes `CONNECT` from everyone, including non-owner non-superusers. `etl_writer` then fails with *"user does not have CONNECT privilege"* | Grant `CONNECT` explicitly to **every** login that needs it, after the `REVOKE` |

### 0.3 Host constraints (recorded, not yet fully resolved)



| Finding | Consequence | Mitigation |
|---|---|---|
| The host is a **Wi-Fi laptop on DHCP** at `10.0.0.10/24` | The §11.1 requirement that the service is available to consumers collides with a host that sleeps, roams and whose address can change on lease renewal. A changed address breaks every saved consumer connection string. | **DHCP reservation for `10.0.0.10` is required before Phase 5 cutover.** Not yet in place. Wired Ethernet preferred. Until then this host is a build/validation target, not a production host. |
| Consumer workstation addresses are not known individually | The `10.0.0.0/24` scope is the widest subnet-level grant consistent with §11.3. | Per-person `/32` grants can replace it once the consumer list is known; `reporting` logins are created per person regardless (D6). |

---

## 1. Scope and boundaries

### In scope (Supply Chain)

| Domain | Content | Source |
|---|---|---|
| **Actual sales (CJ sellout)** | Daily sellout by CJ item, SO qty/amount, promotion attribution | S3, S4 |
| **Promotion** | Promotion Grid / progrid, promo price per piece, promo type & theme, validity windows | S1, S2 |
| **Product master** | CJ item, category, sub-category, group, supplier, status lifecycle | S8, S9 |
| **Plan / forecast** | Sales plan by item × day, fused with promotion prices | S10 |
| **Sellout vs plan** | The variance question the platform exists to answer | derived |
| **Inventory & supply** | DC daily stock, goods receipt, weekly assortment / OOS | S7, S5, S6 |

Inventory is in scope but sequenced **after** the sellout-vs-plan contract (Phase 4b), because it is
not required to answer the variance question. Its source IDs are in §5.1.1.

**There is no customer or channel dimension.** What the sources call `Cus_code` is the CJ item;
verified three independent ways in §5.1.1. `Sales Channel` in S4 and `sales_channel` in S10 are the
only real channel attribute.

### Out of scope (Business Development)

SalesDB, owned by the Business Development team, covers a different reporting need with a different
grain and audience. **This platform does not read from, write to, or replicate SalesDB.**

The two systems coexist. This platform owns CJ sellout, promotion, product master, and plan. If a
future requirement needs BD data, that is a *separate* integration with an explicit owner and SLA —
never a cross-database join from a dashboard.

---

## 2. Architectural decisions

| Decision | Choice | Rationale |
|---|---|---|
| Engine | PostgreSQL 18 | Local + genuinely free + unrestricted growth. SQL Server Express caps at 10 GB/database; Developer is not production-licensed. |
| Deployment | **Native Windows service** (`postgresql-x64-18`) | See §2.1. A real service that auto-starts at boot. No virtualization layer, no container runtime to babysit. |
| ETL runtime | **Native Python venv** + locked dependencies | Decoupled from the database. Reproducibility comes from `uv.lock`, not from an image. |
| Orchestration | Python CLI + Windows Task Scheduler | `python -m etl run --date` is idempotent, re-runnable, and callable by hand during incidents. |
| Transforms | **SQL, not pandas** | Pushes work to the engine, avoids OOM, makes logic reviewable and testable, and keeps the code small as data grows. |
| Migrations | Alembic | Schema is versioned code, reviewable in diffs, reversible. No table may be created by a data load. |
| Contract | `rpt` schema of read-only views | The only surface consumers may touch. |
| Access `.accdb` reads | `pyodbc` + ACE OLEDB provider | Windows-native. Replaces the `mdb-export` shell-out, which only worked because the ETL ran in a Linux container. |
| Network shares | Windows UNC paths | `\\host\share\...` natively. No CIFS mount, no stored SMB credential, no copy-through. |
| Secrets | Environment / `.env` (git-ignored) | No credential in source or committed config. |
| Lineage | `ops.etl_run` + `ops.dq_result` | Every table knows when it last loaded, how many rows, and whether it passed checks. |

### 2.1 Why native PostgreSQL instead of Docker Compose

Docker is a reasonable default and it was working. It is the wrong tool *here*, for specific reasons.

| Concern | Docker Compose on Windows | Native Windows service |
|---|---|---|
| **Availability** | Docker Desktop must be running. Auto-updates, WSL2 repair after Windows Update, manual starts. | Auto-starts at boot. Managed in `services.msc`. Nothing to launch. |
| **Corporate VPN** | Docker Desktop's WSL2 backend conflicts with corporate VPN clients (FortiClient, Cisco, GlobalProtect) — the tunnel routes around the WSL virtual adapter. Fixes mean registry edits or dropping WSL2 integration. | No intermediate layer. |
| **Data location** | Inside a WSL2 virtual disk that grows and does not shrink. Backing it up requires Docker/volume knowledge. | `D:\pgdata` on a normal disk (moved from C: 2026-10-06, `ops\move_pgdata_to_d.ps1`). Any Windows backup tool works. |
| **Client tooling** | `docker exec psql` | `psql.exe`, `pg_dump.exe` on `PATH`. |
| **Failure diagnosis** | Two layers to reason about (container, then Postgres). | One, with standard Windows event logging. |
| **Network shares** | CIFS mount into the container; SMB credential stored in compose. | Native UNC access. |
| **Reproducibility** | ✅ Provided by the image | ⚠️ Must be provided by a lockfile — see §2.2 |

The decisive factor is availability. A source of truth for a whole team cannot sit behind a
virtualization layer that conflicts with the employer's VPN client, and cannot depend on a
desktop application being open.

> **Docker is not removed from the toolbox — it is reassigned.** Keep it as a disposable sandbox for
> testing PostgreSQL major-version upgrades or rehearsing a risky migration before touching the real
> instance. That is the genuinely valuable use of a container here.

### 2.2 What replaces container isolation

A container gave reproducible dependencies for free. Native execution does not — you must supply it
deliberately, or "it worked on Tuesday" returns.

- **Lock dependencies hard.** `pyproject.toml` + a committed `uv.lock` (or `poetry.lock`). Pin exact
  versions, including transitive ones. `requirements.txt` with `>=` ranges is not a lockfile.
- **One venv per project, never the system Python.** `.venv\` in the repo root, git-ignored.
- **Record the interpreter version** in `.python-version` and check it at startup in `etl/config.py`.
- **Bootstrap script** (`ops\setup.ps1`) that creates the venv and installs from the lockfile, so a
  rebuild is one command rather than archaeology.
- **Integration tests** run against the local service in a `test` database, created and dropped by
  Alembic. Container isolation is not required for test isolation — a separate database is.

### The pandas boundary rule

> **Set-based transformations should be performed in PostgreSQL by default.** pandas is allowed for
> source-specific parsing, normalization, or transformations that cannot reasonably be expressed in SQL,
> with the resulting data loaded into staging. Large relational transformations remain in SQL.

This is the single most important rule. The current pipeline builds a products × days cross join in
memory (tens of millions of rows) and hand-inserts data. That is unscalable and untestable. Date
explosion, dedup, price resolution, and joining are all *set operations* — do them in the database
where they are indexed, streaming, and reviewable.

**Applied to the verified sources, pandas is confined to per-source parsing:**

| Permitted in pandas (source-side) | Must be SQL (set-based) |
|---|---|
| xlsx sheet read, BOM strip, header trim, `D/M/YYYY` + `%d.%m.%Y` date parsing, sentinel NULLing | Date explosion of promotion validity windows (`generate_series`) |
| `Status` normalisation (strip NBSP, case-fold) — retains `status_raw` | `DISTINCT ON` price tie-break (lowest price; MAIL preferred on equal price) |
| `Cus_code` → `cj_item` rename; type casts | Deduplication on declared grain |
| Column selection by name across the 264 stock files | Any join between sources |
| Quarantine classification (non-`200` Material) | Aggregations for any `rpt` view |

**No Excel → CSV pre-conversion step.** Read xlsx directly via `openpyxl`. A converter would create a
second representation with no owner, schema, or DQ check — a silent-drift shadow copy — and it would
not remove the real work, which is *which sheet, which header row, which columns*, and that must live
in versioned, tested code regardless. It also defeats §5.1 failure behaviour: drift detection
requires parsing the file the business actually publishes.

---

## 3. Layer model

```
   SOURCE SYSTEMS
   CSV · XLSX · Access .accdb · (future: SAP exports, vendor APIs)
        │
        ▼
   ┌─────────────────────────────────────────────────────────┐
   │  stg  —  staging                                         │
   │  Raw, close to source, disposable. No constraints.        │
   │  Fully replaceable. Never read by a dashboard.            │
   │  Target: chunks not rows, always truncate-then-load.      │
   └─────────────────────────────────────────────────────────┘
        │
        ▼
   ┌─────────────────────────────────────────────────────────┐
   │  core  —  base layer (engine-owned)                      │
│  dim_item · dim_date · dim_supplier · dim_dc · dim_channel              │
│  fact_daily_sales · fact_running_progrid · fact_daily_dc_stock          │
│  fact_goods_receipt · fact_weekly_stock · fact_demandfc_history            │
│  fact_oos_status                                                        │
   │  Typed explicitly. PK, FK, NOT NULL, CHECK constraints.   │
   │  Alembic-managed DDL. Never touched by ad-hoc SQL.        │
   └─────────────────────────────────────────────────────────┘
        │
        ▼
   ╔═════════════════════════════════════════════════════════╗
   ║  rpt  —  THE CONTRACT (versioned, read-only)             ║
   ║  rpt.v_* views. The ONLY surface consumers may read.     ║
   ║  Sales measures, plan variance, promo attribution,        ║
   ║  plus rpt.v_freshness for self-service date checks.       ║
   ╚═════════════════════════════════════════════════════════╝
        │
        ▼
   POWER BI · OTHER REPORTING TOOLS · AD-HOC SQL
```

### Why the contract layer is the keystone

- Consumers depend on views, not tables. Add a dashboard, or the tenth pipeline, and nothing leaks.
- Business calculations live in **versioned SQL files** under `sql/rpt/`, not in DAX. They are
  reviewable, testable, and portable.
- A view can be **deprecated** by adding a `v2` and repointing consumers, instead of breaking them.
- If the engine ever changes, only `stg/` and `core/` are rewritten. `rpt/` and every dashboard survive.

---

## 4. Naming conventions

**Rule: the schema says what it is. Never prefix names with the schema.**

| Object | Pattern | Example |
|---|---|---|
| Dimension table | `dim_<entity>` | `core.dim_item` |
| Fact table | `fact_<grain>` | `core.fact_daily_sales` |
| Staging table | `stg_<source>_<entity>` | `stg.stg_csv_daily_sales` |
| Contract view | `v_<audience>_<subject>` | `rpt.v_supply_chain_so_vs_plan` |
| FK column | `<entity>_key` (surrogate) or `<entity>_id` (natural) | `item_key` / `cj_item` |
| Measure column | `snake_case`, explicit unit suffix | `so_qty_piece`, `so_amt_in_vat` |

**Grain is declared in the name.** `fact_daily_sales` is one row per (date × cj_item × channel).
If that is not true, the name is wrong, and the name being wrong is how double-counting happens.

### 4.1 Identifier style — lower `snake_case`, never quoted

**Every identifier in every schema is lower `snake_case`. Never mixed case, never quoted.**

This is not a preference. PostgreSQL folds unquoted identifiers to lower case, so these are the
**same identifier**:

```sql
SELECT sales_qty FROM t;      -- resolves to  sales_qty
SELECT "Sales_Qty" FROM t;    -- a DIFFERENT column
SELECT "sales_qty" FROM t;    -- works, but nothing else in the codebase needs quotes
```

One mixed-case column therefore propagates double quotes into every view, every DQ assertion, every
Alembic migration, and every ad-hoc query a human types. One missed quote is not a lint warning — it
is a runtime error that only appears in production. The cost of `snake_case` is zero; the cost of
`Mixed_Case` is permanent and diffuse.

This also aligns with what the pipeline already does: `get_data_progrid.py` calls
`df.columns.str.lower()` before loading, and `get_all_hist_dc_stock.py` does the same. Lowercase is
already the de facto standard — this section only makes it explicit and binding.

| Element | Style | Correct | Rejected |
|---|---|---|---|
| Column | lower `snake_case` | `promo_price_per_piece` | `PromoPrice_per_Piece`, `promoPricePerPiece` |
| Table / view | lower `snake_case` | `fact_running_progrid` | `RunningProGrid`, `Fact_Running_ProGrid` |
| Constraint | lower `snake_case`, prefixed by object type | `fk_fact_sales_item_key` | `FK_Fact_Sales_Item_Key` |
| Index | lower `snake_case` | `idx_running_progrid_date_item` | `IDX_RunningProgrid_Date_Item` |
| Schema | lower `snake_case` | `ops`, `core`, `rpt` | `OPS`, `Core` |

**Never quote an identifier.** Double quotes in DDL or DML are a review flag.

**These rules apply to identifiers only — column, table, constraint, index, schema names. They do not
apply to *data*.** Enum **values** are business vocabulary, and are governed by §4.3, which has the
opposite intent. Conflating the two is a mistake: `7-Days` is a value and stays `7-Days`; `pro_type`
is a column and becomes `pro_type`.

### 4.2 Source columns require a declared rename map

Every source arrives with its own casing and punctuation, and it is **not** preserved. `stg` holds
lower `snake_case` columns only; the mapping is part of the source contract (§5.1) and is applied on
ingest.

| Source | Original | → `stg` / `core` | Note |
|---|---|---|---|
| S1, S2 | `Cus_code` | `cj_item` | **Semantically renamed**, not just cased — it is the item, not a customer (§5.1.1) |
| S1, S2 | `RSP Unit (Carton / Pack / Piece)` | `rsp_unit` | spaces, parentheses, slashes stripped |
| S1, S2 | `Pro_Type` | `pro_type` | column is `snake_case`; **value upper-cased** (`7-Days`→`7-DAYS`) — §4.3 |
| S1 | `PromoPrice_per_Piece` | `promo_price_per_piece` | |
| S1 | `Pro_Price` | `pro_price_rsp_gap` | **distinct name**, never coalesced (§5.1.1) |
| S2 | `Pro_Price` | `promo_price_per_piece` | different source, different original name |
| S1, S2 | `Start` / `End` | `valid_from` / `valid_to` | `end` is an SQL keyword — see §4.4 |
| S3, S4 | `Date` | `sales_date` | |
| S3, S4 | `ProPrice In.Vat` | `pro_price_in_vat` | unit suffix per §4.5 |
| S3, S4 | `Total_SO_Qty` | `so_qty_piece` | unit suffix; **not** `so_qty` |
| S3, S4 | `Total_SO_Amt (In.Vat)` | `so_amt_in_vat` | unit suffix; **not** `so_amt` |
| S7 | `Calendar Day` | `stock_date` | string `'01.01.2026'` parsed with `%d.%m.%Y` |
| S7 | `Plant` | `dc_key` (FK) | `D001`→`DC1` etc. via `dim_dc` |
| S5 | ` GR_Qty ` | `gr_qty_piece` | leading/trailing spaces stripped |
| S8 | `Supplier Name` | *(not loaded here)* | lives on `dim_supplier` (Rule M2) |
| S8, S9 | `Cus_code` → n/a | `cj_item` / `shm_item` | Access `CJ_Item`, `SHM_Item` |

The rename map lives beside the source module, is reviewed with it, and is asserted at load time by
the schema-drift guard (Phase 2/3). A new source column with no mapping **fails the load** — it is
never silently ignored.

### 4.3 Enum values: identifier style upper case, business spelling otherwise

**Two rules, applied in this order, and they do not conflict:**

1. **Case is normalised — every enum value is upper case.** Applied uniformly, not value-by-value.
2. **Everything else about the spelling is the business's.** Hyphens, digits, wildcards and non-Latin
   script are preserved exactly.

So `7-Days` becomes **`7-DAYS`** — *not* `7_days`. `snake_case` is an identifier rule (§4.1) and does
not reach data; rewriting `7-DAYS` to `7_DAYS` would break joins against Power BI, the Access master,
and anything the business exports.

```sql
sql/seeds/ref_pro_type.sql          -- DELETED (D21). Was: canonical values,
                                    -- upper case, business spelling otherwise.
                                    -- The 2026-10 census below stands as evidence;
                                    -- enforcement moved to rpt.v_unmapped_values.
    ('MAIL',      'Mail / circular promotion',       5_323),
    ('7-DAYS',    'Weekly short-duration promotion', 3_914),
    ('EDLP',      'Everyday low price',              3_637),
    ('OTHER',     'Unclassified',                    2_443),
    ('WED',       nil,                                 292),
    ('NPD',       nil,                                  59),
    ('7-DAYS_*',  'Wildcard / template row — investigate', 48),
    ('3-DAYS',    nil,                                  40),
    ('14-DAYS',   nil,                                  23),
    (<41 Thai-script values>, 'Owner ruling required — §15',  41)
```

#### Measured effect: 13 raw values → 10 canonical

| Raw value | Rows | → canonical | Merge |
|---|---|---|---|
| `MAIL` | 5,180 | `MAIL` | **merged** |
| `Mail` | 143 | `MAIL` | **merged** → 5,323 |
| `7-Days` | 3,903 | `7-DAYS` | **merged** |
| `7-DAYS` | 11 | `7-DAYS` | **merged** → 3,914 |
| `EDLP` | 3,637 | `EDLP` | — |
| `Other` | 2,442 | `OTHER` | **merged** |
| `other` | 1 | `OTHER` | **merged** → 2,443 |
| `WED` | 292 | `WED` | — |
| `NPD` | 59 | `NPD` | — |
| `7-Days_*` | 48 | `7-DAYS_*` | — **stays distinct** |
| Thai-script | 41 | *(unchanged)* | — |
| `3-Days` | 40 | `3-DAYS` | — |
| `14-Days` | 23 | `14-DAYS` | — |

Exactly three collapse groups, and every one of them is a genuine case-only duplicate. Two
distinctions survive that a sloppier rule would have destroyed:

- **`7-DAYS_*` remains distinct from `7-DAYS`.** Case folding does not touch the `*`, so the wildcard
  rows are still separable. They remain flagged for an owner ruling (§15) rather than folded in.
- **`OTHER` and the Thai-script value stay separate.** The Thai value is non-Latin, so upper-casing
  leaves it untouched; it remains an unmapped DQ `WARN`, which is the point. Mapping it to `OTHER`
  would hide a classification the business may consider meaningful.

#### Why upper case rather than leaving the variants

| | Upper case (chosen) | Leave variants (rejected) |
|---|---|---|
| Tie-break correctness | `pro_type = 'MAIL'` matches **all 5,323** MAIL rows | 143 rows silently rank as `OTHER` and resolve to the wrong price |
| Lookups and joins | one row per concept; `ref_pro_type` joins cleanly | every join must be `ILIKE` or `UPPER()`-wrapped |
| New variant discovered later | folds automatically; no code change | a silent break, exactly as `Mail` was |
| Business legibility | `7-DAYS` still reads as a promotion type | — |

The decisive point is the third row: `Mail` and `7-DAYS` were not found by hand, they were found by
census. A rule that requires enumerating variants will keep discovering them after every load.

#### Application

Applied on ingest to **every** enum-bearing column, uniformly:

```python
def normalise_enum(v) -> str | None:
    """NFKC → trim → upper. Folds case and invisible characters ONLY.
    Preserves hyphens, digits, wildcards, and non-Latin script."""
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return None
    s = unicodedata.normalize("NFKC", str(v)).strip().upper()
    return s or None
```

- `pro_type`, `pro_theme`, `rsp_unit`, `status`, `sales_channel`, `dc_code`, `supplier_type`
- **`status` is case-FOLDED to lower** (`normalise_status`: NFKC → strip all whitespace → `casefold`), so the cancellation test is `status_norm = 'cancel'` — the NBSP trap still applies. (An earlier revision said upper-cased; the code has always folded lower and the tests assert lowercase.)
- `pro_id_raw` and `status_raw` are **exempt**: they are forensic captures of what the source said and
  are never normalised, so a disputed value can always be traced back.
- Unmapped values are never silently defaulted. An unknown `pro_type` is a DQ `WARN` that surfaces an
  unmapped business concept; it loads and appears in `rpt.v_unmapped_values`.

### 4.4 Reserved words are never column names

`start`, `end`, `date`, `user`, `order`, `group`, `table` are SQL keywords or type names. Using one
forces quoting, which §4.1 forbids.

| Never use | Use instead |
|---|---|
| `start` / `end` | `valid_from` / `valid_to` |
| `date` | `sales_date`, `stock_date`, `report_date`, `week_date` — **always** name the date's role |
| `group` | `item_group`, `status_group` |
| `user` | `owner_name`, `user_key` |
| `order` | `order_no`, `po_no` |

The legacy scripts quote `"end"` in the `generate_series` call. That is exactly the friction §4.1 and
§4.4 exist to remove.

### Units and semantics

- Money columns carry a suffix: `_in_vat` or `_ex_vat`. Never bare `amount`, `amt`, or `value`.
- Quantity columns carry a unit: `_piece`, `_carton`, `_pack`.
- Dates are `date` type, never strings. Timestamps are `timestamptz`, stored UTC.
- A column holding a non-additive ratio is named `<numerator>_rate` or `<x>_pct` and **lives in
  `rpt`, never in a fact** (Rule M4).

---

## 5. Repository layout

```
scm/
├── pyproject.toml               # deps + entrypoint
├── uv.lock                      # COMMITTED — this is the reproducibility contract
├── .python-version
├── .env                         # git-ignored
├── .env.example                 # committed
├── alembic.ini
├── Makefile                     # make migrate / make run / make backup / make test
│
├── sql/
│   ├── rpt/                  # VERSIONED contract view definitions  ← business logic
│   │   ├── v_fact_progrid_bestprice.sql   # resolved 1-row-per-(date×item×unit)
│   │   ├── v_promotion_changes.sql        # change-log aggregates
│   │   ├── v_promotion_effective_periods.sql
│   │   ├── v_unmapped_values.sql          # inline canonical set (D21/D22)
│   │   └── v_freshness.sql                # per-table freshness (all tables)
│   └── dq/                   # reusable data-quality assertion helpers
│       └── assert_not_empty.sql
│
├── etl/
│   ├── __init__.py
│   ├── cli.py                # typer/argparse entrypoint
│   ├── config.py             # pydantic-settings; env-driven, no literals
│   ├── db/
│   │   ├── engine.py         # connection factory, pool settings
│   │   ├── transaction.py    # atomic load helpers  ← the important one
│   │   └── log.py
│   ├── runlog.py             # ops.etl_run writer, heartbeat
│   ├── quality/
│   │   ├── checks.py         # row_count, not_null, unique, range, freshness
│   │   └── rules.py          # per-table expectations
│   ├── sources/              # ONE MODULE PER SOURCE SYSTEM
│   │   ├── base.py           # Source ABC: extract() -> DataFrame
│   │   ├── xlsx_reader.py    # shared pinned-sheet xlsx reader (no format conversion)
│   │   ├── csv_sellout.py                # S3
│   │   ├── csv_sellout_td.py             # S4
│   │   ├── csv_promotion_grid.py          # S1 daily CSV → fact_running_progrid (10 cols, D18/D39)
│   │   ├── csv_promotion_grid_pcb.py      # S2 daily CSV → same fact (9 cols, D39)
│   │   ├── progrid_archive.py             # S1+S2 newest-file archive + validation (§5.1.1 Option C)
│   │   ├── csv_goods_receipt.py           # S5 (ISO-only dates)
│   │   ├── xlsx_dc_daily.py               # S7 (5 cols, dotted dates, D23–D26)
│   │   ├── xlsx_weekly_stock.py           # S11 (wide, Monday-asserted)
│   │   ├── xlsx_oos_status.py             # S12 (serial-date parser)
│   │   ├── csv_sales_plan.py              # S10 (raw mirror, D35)
│   │   ├── scan.py                        # ~$ lock exclusion, one rule (D34)
│   │   └── access_product_master.py       # S8 + S9, pyodbc + ACE, copy-to-temp first
│   └── pipelines/            # ONE MODULE PER TARGET TABLE
│       ├── dim_date.py
│       ├── dim_item.py                 # 2-col map, string_agg suppliers (D14 retired)
│       ├── dim_supplier.py
│       ├── dim_dc.py
│       ├── fact_daily_sales.py         # Pattern A per (channel, month) + load-latest
│       ├── fact_running_progrid.py        # date explosion of non-cancelled promos
│       ├── fact_daily_dc_stock.py         # date-exact replace, clamp, quarantine
│       ├── fact_goods_receipt.py          # full replace, SUM-dedup, DC resolve
│       ├── fact_weekly_stock.py           # APPEND-only weeks (D29)
│       ├── fact_sales_plan.py             # append mode, key-exact replace (D38: tables renamed to *_demandfc_history)
│       └── fact_oos_status.py             # date-exact replace, raw wide
│   ├── history/              # one-time backfills, NOT scheduled (D29)
│   │   ├── sellout.py                  # missing (channel, month) windows
│   │   ├── dc_daily.py                 # missing file-dates (folders + root)
│   │   ├── goods_receipt.py            # all annual files
│   │   ├── sales_plan.py               # all snapshots
│   │   ├── weekly_stock.py             # missing weeks
│   │   └── oos_status.py               # missing weeks
│   ├── validate.py             # pre-load gate: 9/9 PASS, zero DB writes (D29)
│
├── tests/
│   ├── unit/                 # pure transform logic, no DB
│   ├── integration/          # against a local `scm_test` database
│   └── fixtures/             # tiny versioned sample files
│
├── ops/
│   ├── setup.ps1             # create venv, install from lockfile
│   ├── backup.ps1            # pg_dump + WAL archiving check
│   ├── restore_runbook.md
│   ├── daily_run.ps1         # steady-state runner: validate-first, 10 steps, Teams card (D29)
│   ├── logs\                 # daily_YYYYMMDD.log per run (git-ignored)
│   └── register_task.ps1     # SCM-ETL-Daily-1000, Mon–Fri 10:00
│
└── docs/
    ├── data_catalog.md
    ├── add_a_new_source.md
    └── deployment.md        # machine/service installation and deployment runbook
```

**Network share paths.** Source files are read from Windows UNC paths, not from a mounted volume.
The share root is `\\fileserver\Supply Chain\` and resolves to these source roots:

```text
\\fileserver\Supply Chain\Back_Office_SCM\Data For Stock\
\\fileserver\Supply Chain\Back_Office_SCM\Data for PowerBI\
\\fileserver\Supply Chain\iTMS\SHM_TMS_001_AllUser_New.accdb
```

**S1/S2 are the exception (D39).** The promotion grids are published as daily CSVs into the owner's
OneDrive folder, not the share:

```text
%USERPROFILE%\Data for Stock Report\raw_progrid_food_nf_import\
%USERPROFILE%\Data for Stock Report\raw_progrid_pcb\
```

The parent is the `STOCK_REPORT_ROOT` setting (`stock_report_root`), so a different workstation only
needs that one value overridden. Everything else still resolves under `share_root`.

Map the share in Windows Explorer once and let Windows handle credentials, or store the SMB
credential in the Windows Credential Manager. **Do not** put an SMB password in a config file — that
was a live problem in the previous compose file, where the QNAP password was committed in plaintext.


### 5.1 Source contracts

Every source is an explicit input contract. The source module and its documentation must define, at minimum:

| Field | Required definition |
|---|---|
| Source | System, file, table, or endpoint name |
| Owner | Business/technical owner responsible for the source |
| Location | UNC path, database, API, or other source location |
| Frequency | Expected delivery/refresh cadence |
| Expected filename | Pattern or selection rule where files are involved |
| Expected columns | Required columns and source data types/semantics |
| Expected grain | One row per what, before any aggregation |
| Date coverage | Business-date field and expected min/max coverage |
| Expected volume | Normal row-count range or historical baseline |
| Encoding / format | File format, encoding, delimiter, workbook/sheet, etc. |
| Timezone | Applicable timezone for timestamps/dates |
| Failure behavior | Whether the pipeline should fail, warn, or skip when the contract is breached |
| SLA | Expected delivery time/window and escalation owner |

A source-contract violation is an upstream data-quality event, not a reason to silently coerce the data
to fit the target schema. Schema drift, missing files, unexpected date coverage, and abnormal volume
should produce an actionable run-log/DQ result.

### 5.1.1 Verified source inventory

The following was established by direct inspection of the share (headers, sheet names, row counts,
value distributions, date ranges). It is the baseline the source modules are written against.

| # | Source | File / sheet | Format | Grain (pre-aggregation) | Verified extent |
|---|---|---|---|---|---|
| S1 | Promotion grid — all divisions | `Data for Stock Report\raw_progrid_food_nf_import\ProGrid_Data_YYYYMMDD.csv` (**newest only**) | CSV, UTF-8 BOM, header row 1, 21 cols | one promo per (`Cus_code`, `Pro_ID`) | **10,952 rows** (2026-10-21 export); Divisions: Foods 5,238 · NF 5,145 · Import-Foods 269 · Import-NF 245 · PCB 55; `Start`/`End` `M/D/YYYY` |
| S2 | Promotion grid — PCB | `Data for Stock Report\raw_progrid_pcb\ProGrid_PCB_YYYYMMDD.csv` (**newest only**) | CSV, UTF-8 BOM, header row 1, 18 cols | one promo per (`Cus_code`, `Pro_ID`) | **5,189 rows** (2026-10-21 export); `Division` = `PCB` (100%); padded header/cells, `Start`/`End` `DD-Mon-YYYY` |
| S3 | CJ sellout | `Data for PowerBI\CJ_Sellout_with_promotion_name\YYYY_M.Daily_<Mon>.csv` | **UTF-8 with BOM**, comma | promo-attributed rows per (`Date`, `CJ_Item`, price-point) — D15 | `2025_01` → `2026_10`, **22 files**, 1,406,770 rows; 7 files `M/D/YYYY` (D19) |
| S4 | TD sellout | `Data for PowerBI\TD_SellOut\YYYY_M.Daily_<Mon>.csv` | UTF-8 with BOM, comma | same, `sales_channel='TD'` | `2025_01` → `2026_09`, **21 files**, 337,785 rows |
| S5 | Goods receipt | `Data for PowerBI\Data_GR_CJ\YYYY_Summary_GR.csv` | CSV | one row per (`Report_Date`, `PO_CJ_NO`, `CJ_Item`, `GR_DC`) | `2025_Summary_GR.csv` (45,245), `2026_Summary_GR.csv` (42,225); ISO dates (owner-fixed) |
| S6 | Weekly assortment | **RETIRED (D20)** — file gone; S11/S12 cover assortment + OOS | — | — | — |
| S7 | DC daily stock | `Data For Stock\DC_DailyStock\YYYY\N.Mon\DC_EndDD-MM-YYYY.xlsx` + root dailies, sheet `Sheet1` | xlsx, 7 cols, 2 blank spacer cols | one row per (`Calendar Day`, `Plant`, `Material`) | **651 files** (2025 full year + 2026→09-30 + root Oct 01–13); 4,337,268 rows over 649 days (re-measured 2026-10-14); `Material` 99.96% `200`-prefixed; root `Template` + `~$` locks excluded by rule (D34) |
| S11 | Weekly stock (new) | `Data for PowerBI\Weekly_Stock\YYYY_M.Mon DD-MM-YYYY.xlsx`, sheet `Sheet1` | xlsx, 59 cols (71 once DC5 arrived 2026-05-18) | one row per (`Product`) per Monday file | **92 files**, 2025-01-06 → 2026-10-05; 272,534 rows |
| S12 | OOS stock status (new) | `Data for PowerBI\OOS_Stock_Status\YYYY_M.Mon DD-MM-YYYY.xlsx`, sheet `Sheet1` | xlsx, 65 cols (78 with DC5) | one row per (`Stock_Date`, `CJ_Item`, `SHM_Item`) — pack variants share CJ items | **89 files**, 2025-01-06 → 2026-09-28; 86,445 rows; `Stock_Date` is ISO/serial/datetime mixed |
| S8 | Product master | `iTMS\SHM_TMS_001_AllUser_New.accdb` → `tbl_Master_003_Product_List` | Access, 59 cols | one row per CJ item | **2,721 rows** |
| S9 | Supplier master | `iTMS\SHM_TMS_001_AllUser_New.accdb` → `tbl_Master_004_Supplier_List` | Access, 17 cols | one row per `Supplier_Code` | **512 rows** (first row is a blank placeholder) |
| S10 | Demand forecast history (was "Sales plan", D38) | `Data for PowerBI\progrid_sales_plan_as_of\YYYY_MM.RunningProGrid_AsOf_FCMonth.csv` | CSV | one row per (`forecast_month_key`, `sales_plan_date`, `cus_code`, channel, type, theme) — 0 dupes over 593,298 rows | `2026_01` → `2026_09`, 593,298 rows; raw mirror, no transform (D35); thousands separators at parse |

#### `cus_code` is a CJ item, not a customer — rename at staging

Established three independent ways:

1. Progrid `Cus_code` intersects **619 of the 1,172** distinct `CJ_Item` values in S3.
2. The S10 plan CSV `cus_code` value `20000779` is identical to S8 `CJ_Item` value `20000779`.
3. The legacy script assigned `sales_plan['cus_code'] = sales_plan['cj_item']` directly.

**Consequence:** there is no customer dimension. `dim_customer` is removed from §8, `fact_daily_sales`
has no customer grain, and every staging table must rename the source column `cus_code` → `cj_item` so
the join contract is unambiguous. The rename is part of the contract, not a cosmetic change — the
legacy `master_sales_plan.cus_code = cj_item` assignment is exactly the kind of silent
same-meaning-different-name that §4 exists to prevent.

A side benefit: the promotion price now joins to sellout on `(sales_date, cj_item)` **one-to-one**,
with no customer dimension to fan out across. §8's price resolution is therefore sound.

**S3 + S4 — one fact, `sales_channel` discriminator.** **Decision:** TD sellout shares `cj_item` as
the join key with CJ sellout and is loaded into the **same** `stg.stg_csv_daily_sales` and the same
`core.fact_daily_sales`, tagged by `sales_channel` (`'CJ'` from S3, `'TD'` from S4). The grain is
therefore `(sales_date × cj_item × sales_channel)` — TD rows are **not** a separate fact, **not** a
join-time enrichment of CJ, and **not** unioned blind.

The 12,631-of-18,600 shared `(date, cj_item)` keys for Aug-2026 are therefore **expected**, not a
duplicate-key violation: the `sales_channel` column is what makes them distinct. A `unique` DQ
assertion on `(sales_date, cj_item, sales_channel)` will pass; an assertion on `(sales_date, cj_item)`
alone will correctly fail and must not be written.

Because `sales_channel` is a low-cardinality, stable, business-owned value, it gets a real dimension
(`core.dim_channel`) rather than a bare text column on the fact.

#### Source-specific handling

**S1/S2 — archive the newest daily CSV before reading it (Option C).** Both promotion grids are now
published as **one full CSV per business date** (`ProGrid_Data_YYYYMMDD.csv`, `ProGrid_PCB_YYYYMMDD.csv`)
into accumulating OneDrive folders (D39). The archive therefore selects the **newest file per source**
— by the date in the filename, never mtime — and keeps that copy as the version record. Older daily
files remain the owner's own history; the pipeline never loads them.

| | Measured (2026-10-21) |
|---|---|
| S1 `ProGrid_Data_20261021.csv` | 4.0 MB |
| S2 `ProGrid_PCB_20261021.csv` | 1.9 MB |
| One full upload snapshot | **5.9 MB** |
| 30 snapshots retained | **~0.18 GB** |

Storage is not a constraint, so the archive is always worth taking. Layout:

```text
ops\snapshot\<uploaded_at>\ProGrid_Data_YYYYMMDD.csv
ops\snapshot\<uploaded_at>\ProGrid_PCB_YYYYMMDD.csv
ops\snapshot\<uploaded_at>\manifest.json     ← hashes, row counts, uploaded_at, source_key
```

**The load reads the archived copy, never the live file.** A file can change between the moment the
pipeline starts and the moment it is read; reading the validated copy makes the load reproducible and
makes "which version produced these rows" answerable. Order inside `load-progrid`:

1. Select the newest `YYYYMMDD` file in each folder (a stray non-matching `.csv` fails loudly).
2. Copy that S1 + S2 pair into a **staging** directory (`<uploaded_at>.partial\`).
3. **Validate the copy before accepting it** — the source module fully parses it (headers, strict
   dates, prices) and the row count must sit within historical bounds. A copy caught mid-save is
   worse than no copy, because it looks valid and is not.
4. Atomically promote to `ops\snapshot\<uploaded_at>\` and write `manifest.json`.
5. Load `stg` → `core` **from the archived copy**.
6. Diff this archive against the previous one → `ops.promotion_change_log`.

Failure at step 3 aborts before anything is written. The previous archive remains the current state.
If both newest files match the recorded `ops.dataset` name + sha256, the command stops after step 1
(`SKIP ... unchanged`): no snapshot churn and no rebuild.

**Why diff the archive rather than a database projection:** comparing two versions of the source is
exact by construction. Rebuilding `valid_from`/`valid_to` from daily rows via `MIN(sales_date)` /
`MAX(sales_date)` is only approximately right, and fails silently when promotions interleave (§9.1
Rule M10). The diff is a *comparison* of two files, not a transformation, so the pandas boundary rule
does not apply — the explode and tie-break transforms remain in SQL.

**S1/S2 — one canonical promo price (RESOLVED).** **Ruling:**

| Source | Source column | → canonical `promo_price_per_piece` |
|---|---|---|
| S1 `ProGrid_Data_YYYYMMDD.csv` | `PromoPrice_per_Piece` | **yes** |
| S2 `ProGrid_PCB_YYYYMMDD.csv` | `Pro_Price` | **yes** |
| S1 `ProGrid_Data_YYYYMMDD.csv` | `Pro_Price` | **no — a different field; not loaded** |

The two files express the same business quantity under different column names, so the legacy script's
asymmetric rename (`Pro_Price` → `PromoPrice_per_Piece` for S2, native `PromoPrice_per_Piece` for S1)
was **correct**. What looked like a conflict — S1's own `Pro_Price` differing from its
`PromoPrice_per_Piece` on 174 of 10,952 rows — is S1 carrying **two different fields**, only one of
which is the promo price. Those 174 rows are 172 `Division = Foods` and 2 `NF`; e.g. `Foods1_3` has
`Pro_Price=20` while `PromoPrice_per_Piece=3` at `RSP=22`. They are not data errors.

Verification caveat: S1 and S2 are almost entirely disjoint promotion sets — S2 is `Division = PCB`
only (5,189 rows) while S1 holds just 55 PCB rows — so only a handful of rows match across both
files. The mapping rests on the business ruling above; at this sample size the data cannot
distinguish the two candidate pairings.

`stg` still carries S1's unused `Pro_Price` in a **distinctly named nullable column** so the field is
not silently dropped and can be re-examined without a re-extract. It is never coalesced into
`promo_price_per_piece`.

**S1/S2 — the tie-break rule (CORRECTED — the documentation was wrong, not the code).**
**Ruling: lowest price wins. `MAIL` is preferred only when two promotions carry the *same* price.**

| Priority | Criterion | Rationale |
|---|---|---|
| 1 | `promo_price_per_piece` **ascending** | The customer pays the lowest offered price. A cheaper promotion is a better deal regardless of its type label |
| 2 | `CASE pro_type WHEN 'MAIL' THEN 1 WHEN 'EDLP' THEN 2 ELSE 3 END` | Tie-break on **equal price only**: prefer MAIL over EDLP over everything else |
| 3 | `rsp` ascending | Final deterministic tie-break so `DISTINCT ON` is reproducible |

An earlier revision of this document stated the rule as *"MAIL > EDLP > other, then lowest price"* —
type rank first. That was **wrong**, and it was the legacy query in `get_data_progrid.py` that was
correct. The two disagreed on **13,231 of 23,943 contested days (55.3%)**, and the correct resolution
is to keep the query as written and correct this document.

```sql
-- CORRECT. Unchanged from the legacy query.
SELECT DISTINCT ON (sales_date, cj_item, rsp_unit)
       cj_item, rsp, rsp_unit, pro_type, pro_theme, promo_price_per_piece, sales_date
FROM   core.fact_running_progrid
ORDER BY sales_date, cj_item, rsp_unit,
         promo_price_per_piece ASC,
         CASE pro_type WHEN 'MAIL' THEN 1
                       WHEN 'EDLP' THEN 2
                       ELSE 3 END ASC,
         rsp;
```

`DISTINCT ON` requires `ORDER BY` to lead with the `DISTINCT ON` expressions — `sales_date, cj_item,
rsp_unit` — which it does.

**One real defect remains, and it is a data-quality one, not a style choice.** `pro_type = 'MAIL'` is
case-sensitive, but the source contains **143 rows of `Mail`** alongside 5,180 of `MAIL`. Verified as
the same promotion type — 62 shared `Cus_code` values, identical `Pro_Theme` (`Price Off`), same source
file. Those 143 rows fall through to `ELSE 3` and are ranked as `Other`, so on an equal price they lose
to promotions that should not have won.

**Resolved by the §4.3 case rule.** Every enum value is upper-cased on ingest, so the tie-break
compares `pro_type = 'MAIL'` against **all 5,323** MAIL rows — 5,180 plus the 143 that were previously
invisible. The same pass merges `7-DAYS`→`7-DAYS` and `other`→`OTHER`.

Upper-casing is safe here precisely because it stops at case: `7-DAYS_*` remains distinct from
`7-DAYS`, and the 41 Thai-script values are untouched. A `snake_case` rule would have collapsed both.

One value still needs an owner ruling: **`7-Days_*` (48 rows)**. The trailing `*` may be a wildcard, a
template row, or a typo. It loads as `7-DAYS_*` and is flagged rather than folded into `7-DAYS` — see
§15.

**S1/S2 — `Status` semantics, and the retroactive-cancellation question.** `Status` answers *"will
this promotion be cancelled?"* — it is a **forward-looking cancellation flag** set by the promotion
owner. Rule: a row is eligible for the sales plan only if its status is **not** a cancellation.

Because the flag is forward-looking, one case needs an explicit rule. See
**"When does `Cancel` take effect?"** below.

**S1/S2 — file selection and header names are pinned, not discovered.** The export contract is a
filename pattern (`ProGrid_Data_YYYYMMDD.csv`, `ProGrid_PCB_YYYYMMDD.csv`), and only the newest match
is loaded (D39). Columns are selected by exact source name; S2's header pads three names
(` Pro_Price `, ` Period `, ` Status `), so headers are trimmed before matching and cells are trimmed
at parse. A stray `.csv` that does not match the pattern fails the scan loudly instead of being
silently ignored. There are no sheets, Thai sheet names, or slicer workbooks in this path any more.

#### When does `Cancel` take effect?

`Status` answers *"will this promotion be cancelled?"* — it is a **forward-looking flag** set by the
promotion owner, and the pipeline's only job is to exclude cancelled promotions from the sales plan.
The subtlety is that the flag and the validity window can overlap in time, and there are two very
different situations that both look like "Status = Cancel".

Worked example — promotion P: item `20022553`, `MAIL`, price `59`, window **2025-01-16 → 2025-01-22**:

| # | When we load | What the file says | Meaning |
|---|---|---|---|
| 1 | 2025-01-10 | `Status` = blank, window is in the future | Planned. Prices 16–22 Jan at 59 go into the plan. Correct. |
| 2 | 2025-01-20 | `Status` = **`Cancel`**, and 16–20 Jan has **already happened** | Pulled early — stock ran out. But the shop **did** sell at 59 for five days. |
| 3 | 2025-02-05 | `Status` = `Cancel`, entire window is in the past | Never actually cancelled at a known moment — just flagged after the fact. |

Situation **2** is the one that needs a rule. The price of 59 was real on 16–20 Jan: it is on
receipts, it is in last month's published variance report. When the promotion owner flags the
cancellation on 20 Jan, does that flag reach backwards and erase the price from 16 Jan?

Three possible policies, and they are genuinely different business positions:

| Policy | Rule | What happens to history |
|---|---|---|
| **A. Retain** *(recommended)* | Never delete an archived promotion for a later cancellation. The archive and the change log preserve both states | The 16–22 Jan prices stay resolvable from the January archive. Last month's report stays reproducible. Promotions that never ran (flagged `Cancel` *before* `valid_from`) are still excluded from the resolved grid |
| **B. Retract** | A cancellation deletes price rows across `[valid_from, valid_to]` | The 59 disappears from 16–20 Jan. **Every historical variance figure that used it silently changes** on the next re-run, and last month's published number no longer reproduces |
| **C. Truncate at cancellation** | Keep prices up to the cancellation date, drop the remainder | Most faithful, but **the source has no cancellation-date field**, so this is not implementable without an owner change |

**Policy A is the default**, for three reasons:

1. It is the only policy reproducible from the source as it exists. The grid carries `Status` but no
   `cancelled_at` timestamp, so B and C cannot distinguish "cancelled before it ran" from "cancelled
   mid-flight" except by comparing `Status` against `valid_from` at load time — which is unreliable,
   because the grid is a **single file overwritten in place** with no version history.
2. A published number that changes when you re-run a past month is worse than a number you know was
   provisional. Under B, re-running January in March silently rewrites January's variance.
3. Exclusion still works where it matters. A promotion cancelled *before* `valid_from` never had its
   prices loaded, so the sales plan is still clean.

**The one implementation rule that makes A work:**

```sql
-- load-time classification, evaluated against the validity window
is_cancelled := status_norm IN ('cancel')          -- after NBSP strip + case fold
never_ran    := is_cancelled AND valid_from >= uploaded_at::date

-- never_ran  -> exclude from core.fact_running_progrid entirely (never priced)
-- otherwise  -> load the full window into fact_running_progrid
```

`is_cancelled` rows are excluded from `fact_running_progrid` **as it stands today**, which means a
promotion cancelled *mid-flight* loses the days it actually ran. That contradicts Policy A: those
days were genuinely sold at a promo price and appear on receipts. The archive (§5.1.1) preserves both
states — the January archive holds the promotion as active, and `ops.promotion_change_log` records the
transition to `Cancel` — so the history is recoverable even though the resolved grid no longer prices
it. Whether the resolved grid should retain those days is an open decision (§15).

`status_at_load` is not carried on the fact. It is implied by `ops.promotion_change_log`: the
`ADDED` row written when a promotion first appears, and the `MODIFIED` row recording when its status
turned to `cancel`. Because the archive retains every prior version of the file, "what did we believe
when we loaded this?" is answered by opening the archive directory for that `uploaded_at`.

**S1/S2 — `Status` is dirty, but only `Cancel` is semantically meaningful.** **Ruling:** all other
status values are treated as **active**. `Revised1` / `Revised2` / `Revised3` (**1,031 rows** in S1),
`adhoc` / `Adhoc` (45), `New` / `new` (357), sentinel `0` (7,076), `None` (99), and `'\xa0'` (155) all
mean "not cancelled" for the purposes of the sales plan.

Observed values across both files:

| Raw value | Count (S1) | Treated as |
|---|---|---|
| `0` | 7,076 | active |
| `Cancel` | 1,402 | **cancelled** |
| `cancel` | 436 | **cancelled** (case variant) |
| `Revised1` / `2` / `3` | 1,031 | active |
| `New` / `new` | 357 | active |
| `'\xa0'` | 155 | active |
| `adhoc` / `Adhoc` | 45 | active |
| `None` | 99 | active |
| `'\xa0cancel'` | 3 | **cancelled** — see the trap below |

**The trap:** a naive `status.str.lower() == 'cancel'` **silently passes the 3 `'\xa0cancel'` rows
through** as active, because the value is `'\xa0cancel'` — a non-breaking space followed by `cancel`
— which is not equal to `'cancel'`. Normalisation must strip NBSP and other whitespace before
case-folding:

```python
def normalise_status(raw) -> str | None:
    if raw is None or (isinstance(raw, float) and pd.isna(raw)):
        return None
    s = unicodedata.normalize("NFKC", str(raw))   # NFKC folds NBSP -> space
    s = re.sub(r"\s+", "", s).strip().casefold()  # then remove all whitespace
    return s or None                              # '' and '0' both collapse to active

IS_CANCELLED = {"cancel"}                        # exact membership. Nothing else.
```

Do **not** use `str.contains("cancel")` — it would also match a future `Not Cancelled` or
`Cancel Pending` value and quietly invert the rule.

**S3/S4 — channel, not separate fact.** S4 is byte-compatible with S3 except that `Product_Name` is
replaced by `Sales Channel` (constant `TD`). For Aug-2026, **12,631 of 18,600** S4 keys also exist in
S3 (CJ 20.1M qty vs TD 2.5M qty). S10 carries `sales_channel` with value `CJ`. Both channels therefore
land in **one fact table** with a `sales_channel` dimension, loaded as two source modules into one
staging table. Additive vs subset is now **resolved**: both channels load into the same fact, tagged
by `sales_channel`, and TD shares `cj_item` as the join key — so the shared keys are expected, not a
duplicate-key violation.

**S3/S4 — sentinels that must become NULL.** `ProPrice In.Vat = 0` appears in **18,970 of 62,337**
Aug-2026 rows (~30%); `Main_Promotion = '-'` means "none". `Total_SO_Qty > 0` with
`Total_SO_Amt (In.Vat) = 0` occurs on 66 rows. These are missing values, not zero-valued business
facts, and must be NULLed at staging or price-realisation and promo-lift measures divide by zero.
**Read every CSV as `utf-8-sig`** — the BOM is present, and a plain `utf-8` read yields `\ufeffDate` as
the first column name. Thai `Product_Name` is genuine UTF-8 and must not be "corrected".

**S5 — dates are `D/M/YYYY`.** `Report_Date`/`Received_Date` arrive as `1/5/2026`. Thai convention is
day-first; an M/D read silently shifts dates by four months, so the format must be stated explicitly
in the parse (`format='%d/%m/%Y'`) rather than inferred. Header ` GR_Qty ` has **leading and trailing
spaces** and must be stripped. Annual file granularity ⇒ full refresh per load.

**S6 — RETIRED (D20).** Was: one rolling file, not year-scoped. No code or tables were ever built for it; assortment/OOS arrive via S11/S12 instead. The R7 full-refresh exception below is kept as precedent.
onward and is rewritten weekly (modified 2026-10-01). **Resolved: rolling window** — each weekly
delivery carries recent weeks only, not full history. Full refresh, a documented **R7 exception**,
because the source is a single overwriting file.

**S7 — `Material` is a CJ item; no bridge table needed.** Across ~1.92M rows, `Material` prefixes are
`200` × 1,920,137, `100` × 1,450, `300` × 275 — **0.09%** contamination, all in the root-level
`DC_End*.xlsx` files; the 264 monthly-nested files under `DC_DailyStock\2026\` are 100% clean `200`.
A separate SAP-material → CJ-item bridge is therefore **not required**. Non-`200` rows must be
quarantined with a DQ `ERROR`, not silently loaded — §14 bans silent coercion.

Also in S7: `Calendar Day` arrives as the **string** `'01.01.2026'` (parse with `format='%d.%m.%Y'`,
which §4 otherwise forbids as a string date); the two-column header has blank spacer columns
(`Plant` label, then unnamed, `Material` label, then unnamed) so columns must be selected by name, not
position; `Plant` values are `D001`/`D002`/`D004`/`D005` → map to `DC1`/`DC2`/`DC4`/`DC5`; and
`stock_qty` is **negative** on real rows (`-7358`) — a genuine backorder condition, so the
`measure_range` check needs a documented tolerance rather than a hard failure. Note the legacy script
applied `GREATEST(stock_qty, 0)`, which **destroys** that signal; do not repeat it.

The legacy script also used `if_exists='append'` into staging across all 264 files with `TRUNCATE`
commented out, so re-running **duplicates every row**. Banned (§14).

**S7 — `2026` month-folder tree only.** **Decision:** the authoritative stock history is
`Data For Stock\DC_DailyStock\2026\N.Mon\DC_EndDD-MM-YYYY.xlsx` — **234 files, `1.Jan` → `9.Sep`,
covering 2026-01-01 → 2026-09-21**. The 9 root-level `DC_End*.xlsx` files are **excluded** from the
load. Two consequences to accept explicitly:

- **Stock lags the business by ~9 days.** The root-level files run to `DC_End30-09-2026` but are out
  of scope, so `fact_daily_stock` cannot answer "stock as of yesterday". `rpt.v_freshness` must
  compare `max(stock_date)` against a **stock-specific** staleness threshold, not the 2-day sales one.
- **The 0.09% non-`200` `Material` contamination is entirely in the excluded root-level files.** The
  month-folder tree is 100% `200`-prefixed. The quarantine rule stays anyway — it is a guard, not a
  workaround, and a future year's files may not be as clean.

The legacy script already read this path (`DC_DailyStock/2026/*/*.xlsx`), so the decision matches
prior behaviour.

**S6 — rolling window, full refresh.** **Decision:** `2026_Assortment.csv` is a rolling window, not a
history. `fact_assortment_weekly` is therefore a **full refresh** (documented R7 exception) and its
**retention is bounded by the source.** Consequences to document, not design around:

- **No year-over-year OOS analysis is possible** from this source. Rows older than the window are
  gone permanently once the file rolls.
- Historical completeness must be asserted in DQ (`row_count_within_history`), because a shrinking
  window is indistinguishable from a broken extract if nothing checks it.
- If OOS history is ever required, it needs a source that retains it — `OOS_FromBI` (§15) or an
  archival export from the owner.

**S8 — the status lifecycle Phase 1 needs is already here.** `tbl_Master_003_Product_List` carries
`Group` (`Existing` / `Discontinuous`), `CJ_Status`, `CJ_Status_as_of`, `TD_Status`, `TD_Status_as_of`,
`DCM_Status`, and `First_SO_Date`. Phase 1's "items going Discontinuous must age out, not vanish" is
served by `Group` + `CJ_Status_as_of` without inventing anything. It also carries `Cat`, `Sub_Cat`,
`Supplier_Code`, and both identifier systems `SHM_Item` (e.g. `1101001`) and `CJ_Item`
(e.g. `20000779`).

**S8/S9 — table names are capitalised.** The real names are `tbl_Master_003_Product_List` and
`tbl_Master_004_Supplier_List`; the legacy script used lowercase, which works only because ACE is
case-insensitive. Use the real names. `tbl_Master_004_Supplier_List` row 1 is a **blank placeholder**
(`Supplier_Code = ''`) and must be filtered. Its name columns are `Supplier Name` (with a space),
`Supplier_Name`, and a Thai-named column — select `Supplier_Code` + `Supplier Name` explicitly.

**SHM ↔ CJ mapping already exists in two places,** so no new bridge is needed if one is ever wanted:
`OOS_FromBI\OOS_FromBI.xlsx` (`SHM_Item` 2600009 ↔ `CJ_Item` 20064758) and S8
(`SHM_Item` 1101001 ↔ `CJ_Item` 20000779). `OOS_FromBI` was last modified **2026-06-15** and is
superseded in intent by S6; it also carries `SHM_Status (As of StockDate)` / `CJ_Status (As of
StockDate)` — as-of status history, which is a stronger lifecycle signal than S8's single current
value. Whether it is a source or dead is an **open question (§15)**.

**S10 — deferred to the next phase, but recorded.** `forecast_month_key`, `fc_lead_time_months`,
`sales_plan_date`, `demand_owner`, `cus_code`, `sales_channel`, `pro_type`, `pro_theme`,
`promoprice_per_piece`, `forecast_cartons`, `forecast_qty`. Confirms `cus_code` = CJ item and
`sales_channel` = `CJ`. Latest file is `2026_09` while the current month is 2026-10 — **one month
stale**, which needs an SLA in §5.1 before Phase 4 can be trusted.

**Note on the source Access database.** Reading `.accdb` previously shelled out to `mdb-export`,
which is a Linux binary and only worked because the ETL ran inside a container. Now that the ETL is
a native Windows process, read it through ODBC:

```python
import pyodbc
# Driver must be installed once: Microsoft Access Database Engine (x64), matching bitness.
CONN = (
    "DRIVER={Microsoft Access Database Engine 2016};"
    "DBQ=r'\\\\fileserver\\Supply Chain\\iTMS\\SHM_TMS_001_AllUser_New.accdb';"
)
with pyodbc.connect(CONN) as conn:                      # read-only by default
    df = pd.read_sql(f"SELECT * FROM {table}", conn)
```

Two operational notes: the ACE driver must match the **bitness of your Python** (x64 Python needs
the x64 driver), and it is a one-time machine install, not a pip package. Wrap it behind the
`Source` ABC so the driver choice is invisible to the pipeline.

**S8/S9 — copy before opening.** `SHM_TMS_001_AllUser_New.accdb` is **516 MB and a `.laccdb` lock file is
present**, meaning it is open by another process right now. ACE against a live network share will
either fail or read a torn file. Verified working procedure — copy to local temp first, then open
read-only:

```python
import shutil, tempfile, os, pyodbc

def extract_accdb_table(src: Path, table: str) -> pd.DataFrame:
    tmp = Path(tempfile.gettempdir()) / f"{src.stem}__extract.accdb"
    shutil.copy2(src, tmp)                     # ~516 MB, ~21 s on this share
    try:
        conn = pyodbc.connect(
            f"DRIVER={{Microsoft Access Driver (*.mdb, *.accdb)}};DBQ={tmp};"
        )
        return pd.read_sql(f"SELECT * FROM [{table}]", conn)
    finally:
        conn.close()
        tmp.unlink(missing_ok=True)
```

Measured cost: 516 MB copy in 21 s. That is per-run overhead, so it belongs in the **daily** load, not
the hourly one — and the copy must land on a local disk, never on the share.

**S8 — confirm this is the right file.** The same folder holds `SHM_TMS_001_AllUser_New.accdb`
(9 MB), `back_up_2026-09-30.accdb` (62 MB), and `back_up_2026_09_23.accdb` (61 MB). 516 MB is large
for master data, and the database carries 32 tables including `tbl_POHeader`, `tbl_AllPO_Details`,
`tbl_ActualQty` and transport tables — so it is a **mixed operational database**, not a clean product
master. Only S8 and S9 are in scope; the `tbl_Master_*` prefix is the selection rule, and the backup
files are **excluded by an explicit allowlist of filenames, never by a glob** (a glob would pull in
`back_up_*.accdb`).

**iTMS is out of scope apart from S8/S9.** The same folder also holds
`Data_Load_To_PODetails_(Manual).xlsx`, `DO_Import.xlsx`, `SHM_PO.txt`, and an `Access_History\`
directory. These are purchasing/transport. Recorded here so a later phase does not mistake them for
undeclared sources.

---

## 6. The non-negotiable rules

These are the invariants that make the platform trustworthy. Every pull request is reviewed against them.

| # | Rule | Why it exists |
|---|---|---|
| **R1** | **One transaction per table load.** DELETE/TRUNCATE and the INSERT that replaces it are in the *same* transaction. | The single most important rule. Prevents a crash leaving a table empty or half-written. |
| **R2** | **No `to_sql`, no `CREATE TABLE AS` for `core` or `rpt`.** DDL comes from Alembic. | A load must never *define* a schema. Otherwise types drift run-to-run and the contract silently changes. |
| **R3** | **Every load writes `ops.etl_run` + DQ results, and fails loudly.** | A silent partial load is worse than a failed one. |
| **R4** | **Idempotent.** Re-running the same date produces the same state. | Enables safe retries, backfills, and catch-up runs. |
| **R5** | **No secrets in source or committed config.** | |
| **R6** | **Consumers read only `rpt`.** Base tables are internal. | Makes the contract enforceable and migration cheap. |
| **R7** | **Incremental by default.** Full refresh is a documented, exceptional act. | Bounds the blast radius and the runtime. |
| **R8** | **A new table is a new module + a migration, not an edit to an existing file.** | This is what keeps the codebase navigable as sources multiply. |

---

## 7. Transaction patterns

This is the mechanic that everything else rests on.

### Pattern A — Incremental date window (default, R7)

Corrects a closed range of days. Row-level locks only; readers unaffected; views untouched.

```python
from etl.db.transaction import incremental_load

incremental_load(
    engine,
    table="core.fact_daily_sales",
    date_column="sales_date",
    window=(date(2026, 8, 1), date(2026, 8, 31)),
    source_sql="""
        SELECT  sales_date, item_key, sales_channel,
                so_qty_piece, so_amt_in_vat, pro_price_in_vat, promotion_key
        FROM    stg.stg_csv_daily_sales
        WHERE   sales_date BETWEEN %(d_from)s AND %(d_to)s
        QUALIFY-ish dedup applied in SQL
    """,
    dq=[not_empty, unique_on(["sales_date","item_key","sales_channel"])],
)
```

The helper opens **one** transaction and does:

```sql
BEGIN;
  DELETE FROM core.fact_daily_sales
   WHERE sales_date BETWEEN :d_from AND :d_to;
  INSERT INTO core.fact_daily_sales (...)
  SELECT ... FROM stg.stg_csv_daily_sales WHERE ...;
  -- DQ assertions run HERE, inside the tx; any failure RAISEs and rolls back
COMMIT;
```

> **The bug to never write again:** `conn.execute(DELETE); conn.commit(); to_sql(...)`.
> If the process dies between those two lines, the date range is simply gone. Commit once, at the end.

### Pattern B — Full refresh (small tables only)

```sql
BEGIN;
  TRUNCATE core.dim_item;              -- transactional & fast, but holds ACCESS EXCLUSIVE
  INSERT INTO core.dim_item SELECT ... ;
COMMIT;
```

### Pattern C — Large full refresh, readers must not block

For a table too big to `TRUNCATE` in a single short transaction, build a shadow table and swap.

> ⚠️ **The gotcha that bites everyone:** if an `rpt` view depends on the table, plain
> `DROP TABLE` **fails** with `cannot drop ... because other objects depend on it`.
> You must `DROP ... CASCADE`, which destroys the views, or reapply every `sql/rpt/*.sql` file.
> That is expensive and easy to forget. **Prefer Pattern A or B; reserve Pattern C for
> deliberate full rebuilds, and always reapply `sql/rpt/` afterwards.**

```python
with engine.begin() as conn:                      # ONE transaction
    conn.execute(text("CREATE TABLE core.fact_x__new (LIKE core.fact_x)"))
    conn.execute(text("INSERT INTO core.fact_x__new SELECT ... FROM stg.stg_x"))
    run_dq(conn)                                    # raises on failure
    conn.execute(text("DROP TABLE core.fact_x__new CASCADE"))
    conn.execute(text("ALTER TABLE core.fact_x RENAME TO fact_x__old"))
    conn.execute(text("ALTER TABLE core.fact_x__new RENAME TO fact_x"))
    conn.execute(text("DROP TABLE core.fact_x__old CASCADE"))
    apply_rpt_views(conn)                           # reapply sql/rpt/*.sql
    conn.execute(text("GRANT SELECT ON core.fact_x TO reporting"))
```

Watch for: index-name collisions on `LIKE` (avoid `INCLUDING INDEXES`; create indexes explicitly
before the swap), and **grants** — a new table has none until you reapply them.

---

## 8. Core model (initial)

```sql
-- ── Dimensions ──────────────────────────────────────────────────────────────

core.dim_date          (date_key PK, date, year, quarter, month, week_iso,
                        day_of_week, is_weekend, is_holiday, thai_year, buddhist_year,
                        month_start, month_name, week_start, day_of_month)
                        -- generated forward by sql/seeds/dim_date_generate.sql

core.dim_item          (cj_item PK, supplier_name)
                        -- 2-column map. One row per cj_item; every supplier carrying
                        -- the item is concatenated alphabetically (','-separated),
                        -- resolved via dim_supplier, raw code as fallback.
                        -- Facts join on the natural cj_item (no surrogate).

core.dim_promotion     (NOT BUILT — promotion concepts live directly on
                        `fact_running_progrid`; `pro_type` is free text since D21.
                        If a concept dimension is ever needed, Rule M5 still applies.)
                        -- PROMOTION CONCEPT grain (Rule M5). No customer FK:
                        -- the source's "Cus_code" IS the cj_item (see 5.1.1)
                        -- pro_type/pro_theme/rsp_unit resolve via ref_* lookup tables (Rule M3)

core.dim_supplier      (supplier_key PK, supplier_code NATURAL UNIQUE, supplier_name,
                        supplier_type, status, credit_term, country, division)
                        -- from S9 tbl_Master_004_Supplier_List; blank placeholder row excluded

core.dim_channel        (channel_key PK, sales_channel NATURAL UNIQUE, channel_name)
                        -- 'CJ' and 'TD'. Low-cardinality but stable and business-owned, so it
                        -- gets a dimension (Rule M3) rather than a bare text column on the fact.

core.dim_dc            (dc_key PK, plant_code NATURAL UNIQUE, dc_name)
                        -- S7 maps D001/D002/D004/D005 -> DC1/DC2/DC4/DC5. There is no D003.

bridge_item_status     (NOT BUILT — status lifecycle served by S8 `Group` +
                        `CJ_Status_as_of`; see Rule M6 note in §8 design notes.
                        Kept as the design if full SCD2 is ever needed.)

-- ── Facts ───────────────────────────────────────────────────────────────────

core.fact_daily_sales      GRAIN: one row per (sales_date × cj_item × sales_channel ×
                            pro_type × pro_theme × pro_price × is_main) — promo-attributed (D15)
                            NO customer dimension - see 5.1.1
                            Joins on natural cj_item (dim_item is a 2-column map);
                            channel via dim_channel; sales_date via dim_date
                            Measures: so_qty_piece, so_amt_in_vat, pro_price_in_vat
                            Source S3 (sales_channel='CJ') + S4 (sales_channel='TD') into one table

core.fact_daily_stock     (BUILT AS `core.fact_daily_dc_stock`, grain one row per
                           (stock_date × dc × cj_item), 4,337,268 rows over 649 days.
                           Measures: stock_qty (negatives clamped to 0, D24),
                           stock_value. Source S7; non-200 quarantined (D25).)

core.fact_demandfc_history  (RENAMED from `core.fact_sales_plan` 2026-10-14, D38;
                           raw mirror grain
                           one row per (forecast_month × plan_date × item × channel ×
                           type × theme), 593,298 rows over 9 snapshots (202601–202609).
                           No transform, D35; audit columns kept.)

core.fact_goods_receipt   GRAIN: one row per (report_date × po_no × cj_item × dc)
                           Inbound quantity/value from S5. Full replace per load;
                           latest-year daily path + history loader (D29).

core.fact_weekly_stock    (NEW, S11) wide grain one row per (stock_date × cj_item),
                           Total/DC1/DC2/DC4/DC5 blocks as columns, 272,534 rows over
                           92 Mondays. APPEND-only weeks (D29); DC5 0-fill (D23).

core.fact_oos_status      (NEW, S12) raw wide mirror, grain one row per
                           (stock_date × cj_item × shm_item), 86,445 rows over
                           89 weeks. Date-exact replace; DC5 numerics 0-fill (D23).

core.fact_assortment_weekly  (NOT BUILT — S6 retired, D20)
```

**Design notes**

- `dim_item.cj_item` is the natural key that reconciles to SalesDB, the source CSVs, and the product
  master — and the PK facts join on directly (no surrogate since the 2-column reshape).
- **There is no `dim_customer`.** The promotion grid's `Cus_code` and the plan CSV's `cus_code` are
  both the **CJ item**, verified three ways in §5.1.1. This also means the resolved promotion price
  joins to `fact_daily_sales` on `(sales_date, cj_item)` one-to-one — no fan-out across customers.
- `dim_item.shm_item` is retained because S8 carries a second identifier system (`SHM_Item`, e.g.
  `1101001`) and `OOS_FromBI` exposes the same pairing. It is not used for any current join; it is
  captured so the mapping is not lost.
- `dim_item` needs no new status machinery: S8 already supplies `Group` (`Existing` /
  `Discontinuous`), `CJ_Status`, and `CJ_Status_as_of`, which is what Phase 1's "items going
  Discontinuous must age out, not vanish" requires.

---

## 8.1 The promotion pipeline (S1 + S2) — final shape

Four tables. This is deliberately the smallest object set that keeps change detection **exact**.

```
╔══════════════════════════════════════════════════════════════════════════╗
║  SOURCE   ...\Data for Stock Report\raw_progrid_*                        ║
║           S1 ProGrid_Data_YYYYMMDD.csv   newest only   10,952 rows       ║
║           S2 ProGrid_PCB_YYYYMMDD.csv    newest only    5,189 rows       ║
║           DAILY FULL SNAPSHOTS · older files never loaded (D39)          ║
╚══════════════════════╤═══════════════════════════════════════════════════╝
                       │ ① copy newest S1+S2 → ops\snapshot\<uploaded_at>\ (5.9 MB)
                       │ ② VALIDATE copy (strict headers, dates, prices, rows)
                       │ ③ promote atomically, write manifest.json
                       ▼
┌──────────────────────────────────────────────────────────────────────────┐
│ 1  stg.stg_progrid_raw              TRUNCATE → bulk load                │
│    EVERY source row, including cancelled. No business logic.             │
│    price: S1.PromoPrice_per_Piece · S2.Pro_Price → promo_price_per_piece  │
│           S1.Pro_Price → pro_price_rsp_gap (distinct nullable column)     │
│    cus_code → cj_item   ·   status NFKC-folded, is_cancelled derived      │
│    uploaded_at (archive stamp)  ·  source_file                            │
└─────────────────────────────────────┬────────────────────────────────────┘
                                      │
╔═════════════════════════════════════▼═════════════════════════════════╗
║  ONE TRANSACTION  (§7 Pattern A — one BEGIN, one COMMIT)               ║
╠═══════════════════════════════════════════════════════════════════════╣
║ ④ snapshot previous → TEMP prev        ◄── MUST precede the DELETE      ║
║                                                                       ║
║ ⑤ core.fact_running_progrid      GRAIN (sales_date × cj_item ×         ║
║                                          rsp_unit × promo_row_id)       ║
║    Pure generate_series(valid_from, valid_to) of every NON-CANCELLED   ║
║    source row. NOT a resolution — a faithful expansion.                ║
║    16,055 source rows → 571,133 daily rows.                            ║
║    Retains losers and zero-day promos by construction.                  ║
║                                                                       ║
║ ⑥ rpt.v_fact_progrid_bestprice     GRAIN (sales_date × cj_item ×        ║
║                                          rsp_unit)   [VIEW — Rule M11]  ║
║    DISTINCT ON, tie-break: LOWEST PRICE first; on equal price           ║
║    MAIL > EDLP > other; then rsp. 250,846 rows (9.4% fewer than ⑤).     ║
║                                                                       ║
║ ⑦ DQ: not_empty · unique(sales_date, cj_item, rsp_unit) · date_range   ║
║       · measure_range(0 < price <= rsp) · referential(cj_item)          ║
║    Any ERROR RAISEs → whole transaction rolls back.                     ║
║                                                                       ║
║ ⑧ diff prev vs new ON (slot_key, sales_date) → ops.promotion_change_log║
║    Skipped entirely on first run (prev is empty → everything is "ADDED")║
║                                                                       ║
║ ⑨ DQ: changed rows vs 30-day historical band. >30% → ERROR.            ║
╚═══════════════════════════════════════════════════════════════════════╝
```

### 8.1.1 `ops.promotion_change_log`

A **table**, not a view. A view recomputes on every read and has no memory; once
`fact_running_progrid` is replaced there is nothing left for a view to compare against. It lives in
`ops` alongside `etl_run` and `dq_result` because it is provenance, not business fact — which also
keeps `core` pure and `rpt` view-only, without breaking R6.

```sql
CREATE TABLE ops.promotion_change_log (
    change_id             bigserial PRIMARY KEY,
    detected_at           timestamptz NOT NULL DEFAULT now(),
    uploaded_at           timestamptz NOT NULL,   -- the upload that CAUSED this
    previous_uploaded_at  timestamptz NOT NULL,   -- what it changed FROM
    slot_key              text NOT NULL,          -- cj_item | rsp_unit | valid_from
    cj_item               text NOT NULL,
    rsp_unit              text,
    sales_date            date NOT NULL,          -- DAILY grain
    change_type           text NOT NULL,          -- ADDED | MODIFIED | REMOVED
    changed_fields        text[],                 -- {pro_type, pro_theme, promo_price_per_piece, rsp}
    old_state             jsonb,                  -- forensic backup, not for BI
    new_state             jsonb,                  -- forensic backup, not for BI
    old_pro_type          text,                   -- flat BI-ready pairs (NULL on ADDED side)
    new_pro_type          text,
    old_pro_theme         text,
    new_pro_theme         text,
    old_promo_price_per_piece numeric,
    new_promo_price_per_piece numeric,
    old_rsp               numeric,
    new_rsp               numeric,
    UNIQUE (uploaded_at, slot_key, sales_date, rn)
);
CREATE INDEX ON ops.promotion_change_log (slot_key, sales_date DESC);
CREATE INDEX ON ops.promotion_change_log (uploaded_at DESC);
```

Flat `old_*/new_*` pairs exist because Power BI cannot slice `jsonb`: business needs
`old price -> new price` and `old type -> new type` as plain columns. `ADDED` rows
leave `old_*` NULL, `REMOVED` rows leave `new_*` NULL.

**`valid_from` is excluded from the `changed_fields` comparison.** It is constant across all daily rows
of a promotion, so including it would mark all 30 days `MODIFIED` on every single change.

`reporting` receives `SELECT` on this one table — a scoped, documented exception to R6 for an audit
table. Everything else stays behind `rpt`.

### 8.1.2 Reading the log at promotion level

The log is daily because that is where the truth is; the business question is promotion-level. Aggregate
at **query time**, never at load time — at load time only the new side exists, so an aggregate computed
then cannot be compared against anything.

`change_summary` is ASCII-only (no em-dash: `—` mojibakes to `โ€` on Windows-874
clients) and compositional: `ADDED` | `PROMO CANCELLED` | comma list of
`PRICE UP/DOWN`, `TYPE/THEME CHANGED`, `DATES EXTENDED/CUT` in fixed order
(`RSP CHANGED` dropped from the summary by owner ruling 2026-10-21; the flat
`old_rsp`/`new_rsp` columns remain).
`DATES CUT` = days `REMOVED`; `DATES EXTENDED` = days `ADDED` onto an existing promo
(`valid_to` pushed out, same `slot_key`). Pure-new groups stay `ADDED`; pure-removed
groups stay `PROMO CANCELLED`. Full-change example:
`PRICE DOWN, TYPE CHANGED, THEME CHANGED, DATES EXTENDED, DATES CUT`.
No `WINDOW` jargon in display values (`window_cut` column name kept for compat).
See `sql/rpt/v_promotion_changes.sql` for the canonical definition (flat `old_*/new_*`
pairs + `old_state/new_state` forensic backup). The promo window of each version is
also flattened: `old_valid_from/old_valid_to` vs `new_valid_from/new_valid_to`
(extracted from the jsonb states), with `old_window_days/new_window_days` and a
`dates_verdict` (`DATES EXTENDED` | `DATES CUT` | `DATES UNCHANGED` | `NEW PROMO` |
`PROMO ENDED`) that can be checked against `change_summary` — `valid_from` is part
of `slot_key`, so old vs new differ only on `valid_to`.

Worked example — promotion revised between two uploads:

| `uploaded_at` | `sales_date` | change | detail |
|---|---|---|---|
| 2026-08-30 | 09-15 … 10-14 | ADDED | 30 rows, price 27, EDLP |
| 2026-09-10 | 09-15 … 09-21 | **MODIFIED** ×7 | `pro_type` EDLP→7-Days, price 27→20 |
| 2026-09-10 | 09-22 … 10-14 | **REMOVED** ×23 | — |

Thirty log rows, one view row: `PRICE DOWN, TYPE CHANGED, DATES CUT`, 30 days affected,
`pro_type` EDLP→7-Days, price 27→20, window `2026-09-15…10-14` → `2026-09-15…09-21`
(`old_window_days` 30 → `new_window_days` 7, `dates_verdict` = `DATES CUT`).

### 8.1.3 Effective periods — and why they are not the source rows

Gaps-and-islands over the resolved grid gives the intervals during which a promotion was the price
that **actually applied**. This is *not* the same as the source promotion list:

| Pattern | Measured | Effect |
|---|---|---|
| Same `(item, unit, pro_type)` chunked back-to-back in source | 216 groups / 2,962 rows | islands **merges** them into one period |
| Promos split because another type outranked them | 133 | islands **splits** one source row into several |
| Promos that won zero days | 3,054 | absent from the resolved grid entirely |

So `rpt.v_promotion_effective_periods` answers **"when was this promotion actually the price that
applied?"** — which is the question promotion-effectiveness analysis needs, and a *better* question
than "what did someone type in the spreadsheet". A promotion that lost 30 days to `MAIL` did not drive
those 30 days of sellout, and counting them inflates lift.

```sql
CREATE OR REPLACE VIEW rpt.v_promotion_effective_periods AS
WITH d AS (
    SELECT cj_item, rsp_unit, pro_type, pro_theme, promo_price_per_piece,
           sales_date,
           sales_date - (row_number() OVER (
               PARTITION BY cj_item, rsp_unit          -- or cj_item,rsp_unit,pro_type,pro_theme,price
               ORDER BY sales_date))::int AS island    -- to MERGE same-price chunks instead of splitting
    FROM   rpt.v_fact_progrid_bestprice
)
SELECT cj_item, rsp_unit, pro_type, pro_theme, promo_price_per_piece,
       min(sales_date) AS effective_from,
       max(sales_date) AS effective_to,
       count(*)        AS days_effective
FROM   d
GROUP BY cj_item, rsp_unit, pro_type, pro_theme, promo_price_per_piece, island
ORDER BY cj_item, effective_from;
```

The `PARTITION BY` decides the question being asked — `(item, unit)` gives the *resolved price*
periods, while adding `pro_type, pro_theme, promo_price_per_piece` merges back-to-back same-price
chunks. Pick deliberately; do not inherit the default.

---

## 9. The contract (`rpt`)

Versioned `.sql` files in `sql/rpt/`, applied by a migration. Business logic lives here.

| View | Grain | Purpose |
|---|---|---|
| `rpt.v_freshness` | one row per loaded table | `table_name`, `data_through`, `row_count`, `last_loaded_at`, `coverage_lag_days`, `is_stale` — per-source thresholds (daily 3d → plan 45d). Built 2026-10-06, applied live + at head migration |
| `rpt.v_fact_progrid_bestprice` | date × item × unit | Resolved promo price (lowest wins, MAIL on ties) — 443,247 rows |
| `rpt.v_promotion_changes` | promo × upload | Change-log aggregates at query time |
| `rpt.v_promotion_effective_periods` | promo × island | Gaps-and-islands effective periods |
| `rpt.v_unmapped_values` | unmapped `pro_type` | Inline 7-value canonical set (D21/D22) — currently `14-DAYS`, `7-DAYS_*`, `3-DAYS` |
| `rpt.v_supply_chain_so_vs_plan` | *(not built)* | Actual vs plan — needs `pro_type` normalization (plan raw vs folded) |
| `rpt.v_promotion_effectiveness` | *(not built)* | Promo lift vs baseline |
| `rpt.v_item_performance` | *(not built)* | Sell-through, price realisation, cover |

Note the `so_vs_plan` grain is **date × item × channel** — not date × item × customer. Consumers
grouping by channel get the TD and CJ series separately or summed; the grain includes
`sales_channel`, so both are available without a second fact.

### Freshness is part of the contract

Every consumer needs to know how current the data is without asking anyone. Publish it:

```sql
CREATE OR REPLACE VIEW rpt.v_freshness AS
SELECT 'core.fact_daily_sales'     AS table_name,
       (SELECT max(sales_date) FROM core.fact_daily_sales) AS max_sales_date,
       (SELECT count(*)          FROM core.fact_daily_sales) AS row_count,
       (SELECT max(finished_at) FROM ops.etl_run
         WHERE target_table = 'core.fact_daily_sales'
           AND status = 'SUCCESS')                              AS last_loaded_at,
       (SELECT max(sales_date) FROM core.fact_daily_sales)
         < (current_date - 2)                                     AS is_stale
UNION ALL
-- … one block per contract table
;
```

A dashboard can now surface "data as of 2026-08-31" and go red when it goes stale, with no
dashboard author needing database access.

### 9.1 Modelling and normalisation rules

The three-layer model in §3 already encodes the standard shape: **`stg` is deliberately denormalised,
`core` is normalised, `rpt` is deliberately denormalised again.** Normalising in `stg` is a mistake
(the source shape is the specification); denormalising in `core` is a worse one. The rules below make
the middle layer's contract explicit.

#### The spine

`cj_item` is the spine of this platform — it is the join key across sellout, promotion, plan, stock,
assortment, goods receipt, and the Access master (§5.1.1). Six conformed dimensions hang off it:

```
                 dim_date
                    │
dim_item ──────► fact_daily_sales ◄────── dim_channel
     │  ▲                │   ▲
     │  └── fact_running_progrid
     │                      │
     └── fact_daily_stock ──┴── dim_dc
     └── fact_assortment_weekly
     └── fact_goods_receipt ──── dim_dc
     └── dim_supplier (via supplier_code)
                  dim_promotion (+ schedule)
```

Small and clean, because there is no customer dimension. That is a direct dividend of the
`cus_code` finding, not luck.

#### Rule M1 — surrogate keys everywhere, natural keys constrained

Every dimension gets a `bigint identity` surrogate PK, and every natural key gets an explicit
`UNIQUE`. Already true for `dim_item.cj_item`; extend to `dim_supplier.supplier_code` and
`dim_dc.plant_code`. Facts reference **surrogate keys only**, never a text `cj_item`, so a rename or
re-code is a dimension update rather than a fact rewrite.

#### Rule M2 — no transitive dependencies in a dimension (3NF)

**`dim_item.supplier_name` is removed.** `supplier_name` is a function of `supplier_code`, not of
`cj_item` — so storing it on `dim_item` is a 3NF violation that denormalises on every future supplier
rename. `dim_item` keeps `supplier_key` (an FK); the name lives only in `dim_supplier`.

The same applies to `dim_item.cat` / `sub_cat`: these are genuinely per-item today, but if they ever
become per-supplier or per-division they need their own dimension. Do not pre-empt that.

#### Rule M3 — an enum belongs in a lookup table, not a CHECK and not free text

`pro_type`, `pro_theme`, `rsp_unit`, `sales_channel`, `supplier_type`, `status` all come from
business-owned value sets that **drift** — S1 already shows `New`/`new`, `adhoc`/`Adhoc`,
`Cancel`/`cancel`/`\xa0cancel`. These become seeded reference tables under `sql/seeds/`
(`ref_pro_type`, `ref_pro_theme`, `ref_rsp_unit`, …), referenced by FK, with the *source spelling*
retained in `stg` and the *canonical* value resolved on load.

A `CHECK (pro_type IN (...))` hardcodes the list into the schema and turns every new promotion type
into a migration. A lookup table turns it into a row.

#### Rule M4 — ratios are never stored

S6 arrives as `Total_ScmAssort`, `Total_OOSAssort`, `Total_CountOKROOS`, **`Total_PercOOS`**.
`Total_PercOOS` is a **non-additive ratio** (2.5–96.3% observed across rows). If it lands in a fact
table, Power BI's default `SUM` produces arithmetically meaningless totals that look plausible.
Store `oos_count` and `assortment_count`; derive `oos_rate` in the `rpt` view, where the aggregation
grain is explicit and reviewable.

#### Rule M5 — promotion identity is a surrogate, because the source has none

`Pro_ID` is **not a usable identifier**. Measured across S1 + S2 (16,141 rows, 2026-10-21 CSV):
10,952 rows in S1 with only 10,201 distinct `Pro_ID` — **751 collisions**, and 528 rows carry
`Pro_ID = 0`. The collisions are not benign duplicates: `Foods3_842` appears on several genuinely
different promotions for item `20030346` (e.g. `EDLP` 2025-12-25→2026-01-24 and `7-Days`
2025-10-30→2025-11-05).

Consequences, and how the design absorbs them:

| Problem | Resolution |
|---|---|
| `Pro_ID` cannot key anything across uploads | `pro_id_raw` is **not loaded** (D18): the source column stays in the archived file for forensics, never a key, never `UNIQUE`-constrained |
| The same item / type / theme / unit / price recurs across hundreds of periods | `dim_promotion` is a **concept** dimension keyed by a surrogate `promotion_key`; occurrences live in `fact_running_progrid` |
| No stable identity ⇒ change detection needs another anchor | `slot_key = cj_item \| rsp_unit \| valid_from` links a daily row to a promotion, and consecutive **archives** link uploads to each other (§5.1.1) |
| A revised `valid_from` reads as ADD + REMOVE, not MODIFY | Pair on `(cj_item, rsp_unit)` within one upload when exactly one add and one remove exist; otherwise leave unpaired and flag it |

If the promotion owner can be persuaded to publish a genuinely stable promotion ID in the grid, that
removes the last inference in this table and should be revisited (§15).

#### Rule M6 — SCD strategy is per-attribute, and status gets a bridge

The doc's instinct — "surrogate key lets history survive renames" — is right for *keys* but wrong as a
blanket rule for *descriptions*.

| Attribute | Strategy | Why |
|---|---|---|
| `cj_item`, `shm_item`, `supplier_key` | **Immutable** | Never updated in place |
| `cat`, `sub_cat`, `supplier_name` | **Type 1** (overwrite) | Descriptive; history is not needed for the variance question |
| `status` / `Group` | **Type 2 bridge**, `bridge_item_status(item_key, valid_from, valid_to, status)` | The one genuinely temporal attribute — Phase 1's "Discontinuous must age out, not vanish" |

Prefer the status **bridge** over full SCD2 on `dim_item`. Full SCD2 means every attribute update
mints a new surrogate key, which forces historical fact rows to be re-pointed at the correct version
— and on an incremental load you resolve that by joining on **business date**, not "current version".
Get it wrong and September's variance silently re-computes using today's category.

The bridge keeps `dim_item` stable and puts only the temporal fact in a small, purpose-built table.

#### Rule M7 — redundant source columns are resolved once, at load

Each of these appears in the sources and must be collapsed to one column in `core`:

| Source | Redundancy | Resolution |
|---|---|---|
| S8 product list | `Description` vs `CJ_Description` | One `item_name`; pick the one Power BI should show |
| S8 | `Group` / `CJ_Status` / `TD_Status` / `DCM_Status` + `*_as_of` | Three parallel status systems → one `dim_item.status` + a `status_system` discriminator; keep the rest in `stg` |
| S8 | `Sale Price_CJ` vs `Sale Price_DCM` vs `Buy_Price` | Different products — keep, but each needs a unit suffix per §4 |
| S9 supplier list | `Supplier Name` vs `Supplier_Name` vs a Thai-named column | Select `Supplier_Code` + `Supplier Name` explicitly; drop the rest |
| S1 | `Pro_Price` vs `PromoPrice_per_Piece` | **Unresolved (§15)** — carry both in `stg`, expose one in `core` only once ruled |
| S7 | `Plant` `D001` vs DC naming `DC1` | Resolved at load via `dim_dc` |

#### Rule M8 — physical design follows the access pattern, not the model

| Decision | Rule |
|---|---|
| Indexing | Composite on the declared join+filter path: `fact_daily_sales(sales_date, cj_item)`, `fact_daily_stock(stock_date, dc_key)`. A single-column index on a low-cardinality column (`sales_channel`, `pro_type`) is not used alone |
| Partitioning | Declarative range partitioning by `*_date` on facts, monthly. Not now — premature while `fact_daily_stock` is ~1.9M rows/year — but stock appends ~6,600 rows/day and will want it. Revisit with `EXPLAIN`, per §15 |
| Append direction | Facts are **append-mostly**. Avoid `UPDATE` on fact rows; corrections arrive as delete-then-reinsert over a date window (Pattern A) |
| `ANALYZE` | After every bulk load. A stale `stg` leads to a stale plan and a full-table scan on the next consumer |

#### Rule M9 — normalisation stops at `core`

`rpt` is **intentionally** denormalised: it pre-joins, pre-aggregates, and embeds the business
arithmetic. Two boundaries hold the line:

- No measure in `rpt` that is not derived from a `core` measure.
- No business constant (a tax rate, a target, a rounding rule) embedded in `rpt` SQL *and* in DAX.
  Pick one — SQL — so the number cannot differ between a dashboard and a person running the query by
  hand.

#### Rule M10 — never rebuild a source-shaped record from daily rows

`valid_from`/`valid_to` reconstructed as `MIN(sales_date)` / `MAX(sales_date)` over an exploded fact
is only correct when every daily row belongs to the same source row. It stops being true the moment
promotions interleave — and **53.5% of `(item, unit)` slots have overlapping promotion windows**.

When it fails it fails **silently**. Two measured failure modes:

- A promotion that won 61 days across a 91-day span (item `20038070`, `EDLP`) reconstructs to
  `09-15 → 10-14`, indistinguishable from one that won every day. The information that it lost 30
  days mid-window is unrecoverable.
- A competitor whose winning days are disjoint (20–25 and 40–45) reconstructs as 20–45 **continuous**
  — fabricating 14 days of price that never applied.

**The rule:** if a source-shaped record is needed, store it. Archive the source file (§5.1.1) and diff
archives; do not re-derive. If a daily table must be reconstructed, the reconstruction must be covered
by a test that asserts row-count equality against the source.

#### Rule M11 — a derived table earns its place by semantics, not by size

`fact_progrid_bestprice` removes only **9.4%** of rows from `fact_running_progrid` (250,846 of
276,791), because just 23,943 days have more than one competing promotion. It is **not** a performance
object. It exists so consumers never need to know the tie-break rule.

That value does not require a table. It is expressed as `rpt.v_fact_progrid_bestprice`, a view over
`fact_running_progrid` with the `DISTINCT ON` tie-break inline. Promote it to a materialised table only
when `EXPLAIN` on a real query justifies it — per §15, not per intuition.

---

## 10. Observability

```sql
CREATE TABLE ops.etl_run (
    run_id        uuid PRIMARY KEY,
    pipeline      text NOT NULL,          -- 'fact_daily_sales'
    target_table  text NOT NULL,
    run_date      date NOT NULL,          -- business date being processed
    started_at    timestamptz NOT NULL,
    finished_at   timestamptz,
    status        text NOT NULL,          -- RUNNING | SUCCESS | FAILED | SKIPPED
    rows_in       bigint,
    rows_out      bigint,
    duration_ms   integer,
    error_text    text
);
CREATE INDEX ON ops.etl_run (target_table, started_at DESC);

CREATE TABLE ops.dq_result (
    run_id      uuid REFERENCES ops.etl_run(run_id),
    check_name  text NOT NULL,
    severity    text NOT NULL,            -- WARN | ERROR
    passed      boolean NOT NULL,
    observed    text,
    expected    text,
    checked_at  timestamptz DEFAULT now()
);
```

### 10.1 `uploaded_at` and pipeline timestamps

Two different questions, deliberately kept apart:

| Timestamp | Source | Question it answers |
|---|---|---|
| `uploaded_at` | source file mtime on the share | **When did the business publish this dataset?** |
| `business_date` | the `sales_date` / `stock_date` column | **What period does the data describe?** |
| pipeline timestamps | `ops.etl_run` | **When did we run, and did it fail?** |

`uploaded_at` is **never a business date.** Files on this share are re-touched long after delivery:

| File | Content period | File mtime | Gap |
|---|---|---|---|
| `2025_1.Daily_Jan.csv` | 2025-01 | **2026-05-06** | 16 months |
| `2025_9.Daily_Sep.csv` | 2025-09 | 2025-12-10 | 3 months |
| `2025_12.Daily_Dec.csv` | 2025-12 | 2026-02-03 | 2 months |
| `2026_9.Daily_Sep.csv` | 2026-09 | 2026-10-01 | 1 day |
| `ProGrid_Data_YYYYMMDD.csv` | business date `YYYYMMDD` | file export time | one full snapshot per business date (newest only) |

A January-2025 extract modified in May 2026 means *corrected or re-exported*, not *delivered in May*.
Collapsing these into one column is how a stale extract gets reported as fresh.

#### Dataset registry: `ops.dataset`

One row per dataset (S1…S10, §5.1.1), carrying when it was last uploaded and whether its content
actually changed.

```sql
CREATE TABLE ops.dataset (
    dataset_key   text PRIMARY KEY,        -- 'S3', 'S7', 'S8' ... matches §5.1.1
    dataset_name  text NOT NULL,           -- 'CJ_Sellout_with_promotion_name'
    file_name     text,                    -- '2026_9.Daily_Sep.csv', or NULL if file-less (S8/S9)
    file_path     text NOT NULL,
    uploaded_at   timestamptz,             -- source file mtime. NULL only for S8/S9.
    business_date_min date,
    business_date_max date,
    content_hash  text,                    -- sha256 of the file. Detects silent re-export.
    last_run_id   uuid REFERENCES ops.etl_run(run_id),
    last_seen_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ON ops.dataset (uploaded_at DESC);
```

`content_hash` is what makes an overwrite of the same business date visible. The progrid sources publish
one file per business date but the owner can re-export that date in place, so filename + hash together
decide whether a run is a no-op: same newest name + same sha256 ⇒ `SKIP ... unchanged`; a changed hash
under the same name ⇒ reload and diff.

S8/S9 (`Access`) have no single file mtime that means anything — the `.accdb` is rewritten constantly
by many users — so `uploaded_at` is NULL there and freshness is judged by `business_date` coverage and
run status instead. Do not fake it with the `.accdb` mtime.

#### Pipeline trace: `ops.etl_run`

Already defined above, and it answers the failure question directly. `status` ∈
`RUNNING | SUCCESS | FAILED | SKIPPED`, with `started_at`, `finished_at`, `duration_ms`, and
`error_text`.

```sql
-- "Why did yesterday's numbers change, and did the load fail?"
SELECT pipeline, run_date, status, rows_out, duration_ms, error_text,
       started_at AT TIME ZONE 'Asia/Bangkok' AS started_bkk
FROM   ops.etl_run
WHERE  started_at >= now() - interval '30 days'
  AND  status <> 'SUCCESS'
ORDER  BY started_at DESC;
```

Two habits make this actually useful:

- **Write the `RUNNING` row first**, then update it on completion. A process killed mid-load leaves a
  `RUNNING` row with no `finished_at` — which is precisely how you detect a crash, and you cannot
  detect it if the row is only written at the end.
- **Never overwrite `error_text`.** On failure, append rather than replace, so the first and the most
  recent failure text both survive.

#### Where `uploaded_at` goes

| Layer | Carries it? | Why |
|---|---|---|
| `ops.dataset` | **Yes** — per dataset | The provenance record |
| `stg.*` | **Yes** — per row | A row must be traceable to the file version that produced it |
| `core.fact_*` / `core.dim_*` | **Mostly** — as `loaded_at timestamptz` + `source_file`, **except** the three process facts `fact_daily_sales`, `fact_daily_dc_stock`, `fact_goods_receipt`, which carry neither (D37) | `stg` truncates; `core` does not. Where the fact keeps no audit columns, "which extract is this row from?" is answered by `stg` (`uploaded_at`/`source_file` kept on every staging table) + `ops.etl_run` / `ops.dataset` |
| `rpt.v_*` | `MAX(uploaded_at)` exposed | A dashboard must be able to say "as of extract X" |

One `timestamptz` + one `text` per fact row is a negligible cost for that answer — kept on dims,
`fact_demandfc_history`, and the remaining facts; dropped only where the row-level answer duplicated
the run log at millions of rows (D37).

#### Freshness is two-sided

```sql
-- per contract table in rpt.v_freshness
   max(business_date)                    AS data_through,   -- period covered
   max(uploaded_at)                      AS data_as_of,     -- when published
   now()::date - max(business_date)      AS coverage_lag    -- completeness
   now() - max(uploaded_at)              AS publish_lag     -- delivery delay
```

They fail differently and escalate to different people. `coverage_lag` catches a missing month;
`publish_lag` catches a source that stopped publishing while still technically loading. A single
`is_stale` boolean hides which one broke.

**Each `rpt` view declares its own expected lag**, driven by its source's delivery cadence (§5.1) — not
one global constant. `fact_daily_stock` in particular runs to 2026-09-21 (§5.1.1), so a 2-day
threshold would mark it permanently red and train everyone to ignore the alert.
```

**The run log is the provenance record for a source of truth. `ops.etl_run` is the canonical load-history record; `rpt.v_freshness` derives its load timestamp from successful runs rather than maintaining a second load-history table.** When someone asks "why does March
show a dip?", the answer must be a query, not a guess.

```sql
SELECT pipeline, run_date, status, rows_out, duration_ms, error_text
FROM   ops.etl_run
WHERE  run_date >= current_date - 30
ORDER  BY started_at DESC;
```

**Mandatory DQ checks per load** — cheap, and each one has already prevented a real class of bug:

| Check | Catches |
|---|---|
| `not_empty` | Silent empty extract (the most common failure) |
| `row_count_within_history` | A 90% drop in volume = partial file or upstream change |
| `not_null` on key columns | Missing dimension keys before they become broken joins |
| `unique` on declared grain | Double-counting introduced by a bad join |
| `date_range` sanity | A file parsed with the wrong date format |
| `referential` coverage | New items arriving with no `dim_item` row |
| `measure_range` | Negative quantities, absurd prices, currency-scale errors |

Alerts: on `FAILED` status or a breached `ERROR`-severity check, notify the owning team. Reuse the
multi-channel approach already proven in the SalesDB automation (desktop, email, Teams, LINE) — but
source its config from the same non-committed secrets path.


### 10.1 Source-to-report reconciliation

Technical DQ is necessary but not sufficient. Each pipeline must include business reconciliation that
proves the load preserved the important measures and coverage from source through the reporting contract.

| Reconciliation | Example |
|---|---|
| Source → staging | Row count, quantity, and amount by source period |
| Staging → core | Quantity/amount totals and expected aggregation differences |
| Core → rpt | Contract-view totals agree with the underlying facts |
| Current vs previous load | Volume and measure deltas are within expected historical bounds |
| Source period coverage | Minimum/maximum business date is complete for the requested run |
| Item coverage | Source items missing from `dim_item` are identified |
| Promotion coverage | Unresolved or overlapping promotion records are identified |

Each reconciliation should declare the comparison grain, measures, acceptable tolerance, and failure
severity. A pipeline may pass structural DQ while still producing an incorrect business result; reconciliation
is the control for that failure mode.

---

## 11. Deployment, security, and backup

The platform is about to become shared. These are no longer optional.

### 11.1 Installing and running PostgreSQL natively

PostgreSQL 18 runs as the native Windows service `postgresql-x64-18` and is configured to start
automatically at boot. The detailed machine-installation procedure, PATH setup, service commands, and
ACE OLEDB installation steps belong in the deployment runbook: `docs/deployment.md`.

The architecture-level requirements are:

- PostgreSQL 18 x64 is installed as a native Windows service.
- Remote access is disabled initially; network exposure follows the Phase 0 consumer-access decision.
- The PostgreSQL superuser password is stored in a password manager or Windows Credential Manager, never in source or scripts.
- The Microsoft Access Database Engine x64 is installed for the product-master source, matching Python bitness.
- Service startup, logging, archive configuration, and restart procedures are documented in the deployment runbook.

### 11.2 Remove trust authentication and separate roles

The previous deployment used `POSTGRES_HOST_AUTH_METHOD: trust`, which means **no password is ever
checked** — every connection, from anywhere that can reach the port, is a superuser. It was
tolerable while one script ran on one machine. It is unacceptable for a shared system.

```sql
-- pg_hba.conf: require scram-sha-256 for every non-local connection
# TYPE  DATABASE      USER       ADDRESS         METHOD
local   all           all                        scram-sha-256
host    all           all        127.0.0.1/32    scram-sha-256
host    all           all        ::1/128         scram-sha-256
# host    all           all        10.0.0.0/8     scram-sha-256    ← only if remote access is needed
```

Reload with `SELECT pg_reload_conf();` (no restart needed for `pg_hba.conf`).

Then create separate roles so no application connects as superuser and no consumer can read base
tables:

```sql
CREATE ROLE etl_writer   LOGIN PASSWORD :'etl_pw';
CREATE ROLE reporting    LOGIN PASSWORD :'rpt_pw';   -- read-only consumers

-- Schema-owner/deployment role: used by Alembic migrations, not by the scheduled ETL runtime.
-- Grant only the DDL privileges required by the migration process.

-- ETL runtime: data-load privileges only; it should not need to define the schema.
GRANT USAGE ON SCHEMA stg, core, ops TO etl_writer;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA stg, core, ops TO etl_writer;

-- Consumers see the contract and nothing else  (enforces R6)
GRANT USAGE  ON SCHEMA rpt    TO reporting;
GRANT SELECT ON ALL TABLES   IN SCHEMA rpt TO reporting;
ALTER DEFAULT PRIVILEGES IN SCHEMA rpt GRANT SELECT ON TABLES TO reporting;
REVOKE ALL ON SCHEMA stg, core, ops FROM reporting;
```

The exact DDL grants for the migration role belong in the deployment procedure. The important boundary is
that runtime ETL credentials are not also schema-owner credentials.

Verify enforcement holds: `psql -U reporting -d scm -c "SELECT * FROM core.dim_item"` must **fail**.
If it succeeds, R6 is not actually enforced.

### 11.3 Network exposure

Default to `listen_addresses = 'localhost'`. Expose on the LAN only when the Phase 0 consumer-access
decision requires a consumer outside this PC, and then:

- Use a firewall rule scoped to the specific subnets that need access — not "any".
- Grant the `reporting` role, never `postgres`.
- Prefer an SSH tunnel or VPN where available rather than opening 5432 broadly.
- `pg_hba.conf` should name explicit CIDR blocks, never `0.0.0.0/0`.

The Phase 0 consumer-access decision is the architecture gate for this section. A local database cannot
serve people who are not on the same machine or network. If the approved scope is corporate LAN access,
§11.3 applies. If consumers are beyond the LAN, the local-only/no-cloud constraint must be revisited before
production deployment.

### 11.4 Backups — the definition of "source of truth"

Without a *tested* restore, the platform is not a source of truth; it is the only copy.

| Requirement | Target |
|---|---|
| Full logical dump (`pg_dump -Fc`) | Nightly, retained 14 days |
| WAL archiving + `archive_timeout` | Continuous, retained 30 days → **point-in-time recovery** |
| Off-volume copy | One copy to a second physical disk or the QNAP share |
| **Restore drill** | **Quarterly, documented, timed** |

```ini
# postgresql.conf — point-in-time recovery
archive_mode = on
archive_command = copy "C:\pgdata\16\pg_wal\%f" "D:\pg_backup\wal\%f"
archive_timeout = 300          # forces a segment every 5 min; bounds RPO
wal_level = replica
max_wal_senders = 0            # not replicating, just archiving
```

```powershell
# ops\backup.ps1 — nightly logical dump
$env:PGPASSWORD = (Get-Secret pg_dump)      # from Credential Manager, not a literal
& "C:\Program Files\PostgreSQL\16\bin\pg_dump.exe" `
    -U postgres -Fc -d scm -f "D:\pg_backup\scm_$(Get-Date -f yyyyMMdd).dump"
```

**Proposed recovery targets:** RPO 5 min, RTO 1 hour. These are proposed targets and require business-owner
confirmation before production sign-off. Record the approved targets and the last successful restore-test
date in `docs\`; **a backup you have never restored is a hypothesis.** Copying the `pgdata` directory is not
a backup — use `pg_dump` / `pg_basebackup`, because a raw directory copy taken while the service is
running is not transactionally consistent.

## 12. Build phases

Each phase ends with a *working, verified* state. Do not start a phase until the previous one is
green.

### Phase 0 — Foundations — DONE 2026-10-06 (scheduler live: `SCM-ETL-Daily-1000` Mon–Fri 10:00, validate-first, Teams cards)
- [ ] Resolve the consumer access boundary: **same machine**, **corporate LAN**, **VPN/remote network**, or **internet/cloud**. This decision determines PostgreSQL listening configuration, firewall scope, authentication, Power BI connectivity, availability, monitoring, and backup requirements (§11.3).
- [x] Install PostgreSQL 18 natively; service running `Automatic` (§11.1)
- [ ] `pg_hba.conf` set to `scram-sha-256`; `postgres` superuser password stored in a password manager
- [ ] Install ACE OLEDB provider (x64) for the product master (§5)
- [ ] Repo skeleton, `pyproject.toml`, **committed `uv.lock`**, `.python-version`, `.env.example`
- [ ] `ops/setup.ps1` — creates `.venv\`, installs from the lockfile (§2.2)
- [ ] `etl/config.py` (env-driven, no literals; asserts the interpreter version), `etl/db/engine.py`
- [ ] `etl/db/transaction.py` — the three patterns from §7
- [ ] `etl/runlog.py`, `etl/quality/` checks
- [ ] `ops.dataset` + `ops.etl_run` written by the run-logger; `RUNNING` row written **before** work starts so a crash is detectable; `error_text` appended, never overwritten
- [ ] `uploaded_at` captured from file mtime for S1–S7, S10; NULL for S8/S9 (`.accdb` mtime is meaningless) — §10.1
- [ ] `content_hash` on `ops.dataset` so an in-place progrid overwrite is visible
- [ ] Alembic initialised, `sql/dq/assert_not_empty.sql`
- [ ] `Makefile`: `migrate`, `run`, `test`, `backup`
- **Exit:** `make migrate && make run` succeeds against a fresh database, with a run-log row.
- **Keep Docker running on the side** as a sandbox for testing upgrades and rehearsing migrations.

### Phase 1 — Reference & product (S8, S9) — DONE (source: `AllUser_New.accdb`, D17)
- [ ] `dim_date` seeded to 2020+ (all date attributes incl. Thai/Buddhist year)
- [ ] `etl/sources/access_product_master.py` — **copy the `.accdb` to local temp before opening** (§5.1.1); explicit table allowlist `tbl_Master_003_Product_List`, `tbl_Master_004_Supplier_List`; never glob (would pull `back_up_*.accdb`)
- [ ] `dim_item` from S8 (Access → stg → core), including `shm_item`
- [ ] `dim_supplier` from S9, with the blank placeholder row filtered
- [ ] Status lifecycle from `Group` + `CJ_Status` + `CJ_Status_as_of` — items going Discontinuous must not vanish, they must age out
- [ ] `dim_dc` seeded from the S7 mapping `D001→DC1, D002→DC2, D004→DC4, D005→DC5`
- **Exit:** `dim_item` refreshes incrementally, idempotently; missing-key check passes; a second run over the 516 MB `.accdb` changes nothing.

### Phase 2 — Actual sales (S3, S4) — DONE (43/43 month-windows; `load-latest` daily + `load-month` fix + history loader)
- [ ] Source modules `csv_sellout.py` (S3) and `csv_sellout_td.py` (S4) — **one staging table**, `sales_channel` discriminator
- [ ] Read every CSV as `utf-8-sig`; strip the BOM so the first column is `date`, not `\ufeffdate`
- [ ] NULL the sentinels: `ProPrice In.Vat = 0` → NULL, `Main_Promotion = '-'` → NULL (§5.1.1)
- [ ] `stg.stg_csv_daily_sales`, explicit DDL, truncate-then-load
- [ ] `core.fact_daily_sales` via Pattern A, incremental by month window — **DELETE + INSERT in ONE transaction** (the legacy script committed between them; §7)
- [ ] Schema-drift guard: assert expected source columns exist, fail with a clear message
- [ ] `sales_channel` dimension seeded (`CJ`, `TD`); assert `unique` on (`sales_date`, `cj_item`, `sales_channel`) and **never** on (`sales_date`, `cj_item`) alone — shared keys are expected
- **Exit:** re-running a month twice gives identical row counts; a bad file fails the load, not the DB.

### Phase 3 — Promotion (S1, S2) — DONE (trimmed 10/9 cols D18; `rn` log-key fix D27; MAIL census D28; daily-CSV source D39)
- [ ] `ops\snapshot\` archive: select the **newest** `*_YYYYMMDD.csv` per source, copy it, **validate the copy** (strict parse + row count within bounds), promote atomically, write `manifest.json` with hashes. **Load from the archived copy, never the live file** (§5.1.1)
- [ ] Source modules read the newest daily CSV (`ProGrid_Data`, `ProGrid_PCB`), columns selected **by name**; S1 `M/D/YYYY`, S2 `DD-Mon-YYYY`, S2 padded headers trimmed
- [ ] Price mapping: S1 `PromoPrice_per_Piece` → `promo_price_per_piece`; S2 `Pro_Price` → `promo_price_per_piece`; S1's unused `Pro_Price` → distinct nullable `pro_price_rsp_gap`, never coalesced
- [ ] `Cus_code` → `cj_item` on ingest — this is the join contract
- [ ] `sql/seeds/ref_pro_type.sql` seeded with the 10 canonical values from §4.3: `MAIL`, `7-DAYS`, `EDLP`, `OTHER`, `WED`, `NPD`, `7-DAYS_*`, `3-DAYS`, `14-DAYS`, plus the 41 Thai-script values
- [ ] **Every** enum-bearing column (`pro_type`, `pro_theme`, `rsp_unit`, `status`, `sales_channel`, `dc_code`, `supplier_type`) normalised with the single §4.3 helper — NFKC + trim + upper. Applied by rule, **not** by enumerating known variants
- [ ] `pro_id_raw` / `status_raw` **exempt** from normalisation — forensic captures only
- [ ] Regression test asserting `count(*) FILTER (WHERE pro_type = 'MAIL') = 5_339` and `count(DISTINCT pro_type) = 10`. Under raw source values the count is lower and `Mail` loses its tie-break precedence to `OTHER`
- [ ] `Status` upper-cased to `CANCEL`; the **3 `'\xa0cancel'` rows must be excluded** — NFKC must run before the comparison, and the test uses exact equality, never `.str.contains()`
- [ ] Unmapped enum values → DQ `WARN` + `rpt.v_unmapped_values`. Never defaulted to `OTHER`
- [ ] Regression test asserting the **3 `'\xa0cancel'` rows** are excluded after NFKC + upper — a naive `.str.lower() == 'cancel'` passes them through as active
- [ ] `stg.stg_progrid_raw` — **every** source row including cancelled. Truncate + load. Explicit DDL
- [ ] `core.fact_running_progrid` via Pattern A: `generate_series(valid_from, valid_to)` of every **non-cancelled** row, with a per-load `promo_row_id` so daily rows can be grouped back. Snapshot previous into a temp table **before** the `DELETE`
- [ ] `rpt.v_fact_progrid_bestprice` — `DISTINCT ON` with the tie-break: **lowest `promo_price_per_piece` first**, then `MAIL`→1 / `EDLP`→2 / else 3 on **equal price**, then `rsp`. A view, not a table (9.4% row reduction — Rule M11)
- [ ] Regression test: `pro_type = 'MAIL'` matches **5,339** rows after normalisation, and `count(DISTINCT pro_type) = 10`. Both assertions fail on raw source values
- [ ] `ops.promotion_change_log` — daily diff on `(slot_key, sales_date)`, `valid_from` excluded from `changed_fields`. **Skipped on first run** when the previous snapshot is empty
- [ ] `rpt.v_promotion_changes` — aggregate the log to promotion level **at query time**
- [ ] `rpt.v_promotion_effective_periods` — gaps-and-islands over the resolved grid; `PARTITION BY` chosen deliberately (Rule M10)
- [ ] DQ gates: `not_empty` · `unique(sales_date, cj_item, rsp_unit)` · `date_range` · `measure_range(0 < price <= rsp)` · `referential(cj_item)` · **change-volume band** (>30% of the grid in one upload = ERROR)
- **Exit:** no Python cross-joins remain; the tie-break resolves every contested day to the lowest price with MAIL preferred on equal price; cancelling a past promotion leaves last month's published numbers unchanged.

### Phase 4 — Plan & the contract (S10) — LOADS DONE, CONTRACT OPEN (fact loaded raw; `so_vs_plan` view needs pro_type normalization)
- [ ] `core.fact_demandfc_history` (products × days generated in SQL, not pandas) from S10, with `sales_channel`
- [ ] SLA defined for the plan source — **latest file is `2026_09`, one month stale** (§5.1.1)
- [ ] `rpt.v_freshness` published first
- [ ] `rpt.v_supply_chain_so_vs_plan` — actual vs plan variance
- [ ] `rpt.v_promotion_effectiveness`
- [ ] `docs/data_catalog.md` — grain, columns, refresh cadence, owner, for each `rpt` view
- **Exit:** a new report can be built by a Power BI author using only `rpt` + `v_freshness`.

### Phase 4b — Inventory & supply (S5, S6, S7) — DONE as S5 + S7 + S11 + S12 (S6 retired D20; S10 also loaded)

Deliberately *after* the sellout-vs-plan contract, because none of it is required for the variance
question the platform exists to answer. These land as inputs that later phases join against.

- [ ] `etl/sources/xlsx_dc_stock.py` (S7) — 264 monthly-nested files under `DC_DailyStock\2026\`; select columns **by name** (two blank spacer columns)
- [ ] Parse `Calendar Day` as `'%d.%m.%Y'` — it arrives as the **string** `'01.01.2026'`
- [ ] **Quarantine non-`200`-prefixed `Material`** (1,725 of ~1.92M rows) with a DQ `ERROR`; do not load, do not coerce
- [ ] Do **not** apply `GREATEST(stock_qty, 0)` — negative stock is a real backorder signal and clamping destroys it; document the `measure_range` tolerance instead
- [ ] One transaction per file load (Pattern A). **Never** `append` 264 times with `TRUNCATE` commented out — that duplicates every row on re-run (§14)
- [ ] Scope restricted to `DC_DailyStock\2026\N.Mon\` (234 files → 2026-09-21). Root-level `DC_End*.xlsx` **excluded** — declare a stock-specific staleness threshold in `rpt.v_freshness`
- [ ] `core.fact_daily_stock` via Pattern A
- [ ] `etl/sources/csv_goods_receipt.py` (S5) — parse `D/M/YYYY` explicitly, strip the ` GR_Qty ` header spaces, filter to annual files; full refresh
- [ ] `core.fact_goods_receipt` — full refresh (annual source files)
- [ ] `etl/sources/csv_assortment.py` (S6) — full refresh, **documented R7 exception** (single rolling file)
- [ ] `core.fact_assortment_weekly`
- [ ] `etl/sources/xlsx_oos_from_bi.py` — only if `OOS_FromBI` is ruled a live source rather than superseded by S6 (§15). It carries `SHM_Status (As of StockDate)` as-of status history.
- **Exit:** `fact_daily_stock` is idempotent across a full 264-file re-run; stock reconciles to source by date and DC; negative quantities survive to `core`.

### Phase 5 — Production hardening
- [ ] Backup + WAL archiving; **first restore drill completed and timed**
- [ ] `etl_writer` / `reporting` roles created; R6 verified by a *failing* access attempt (§11.2)
- [ ] Network exposure decision resolved and applied (§11.3)
- [ ] Alerting on failed runs and stale data
- [ ] Task Scheduler tasks: daily incremental, plus a separate re-runnable backfill entrypoint
- [ ] `tests/integration` covering each pipeline against a small fixture
- **Exit:** kill the ETL mid-run and confirm no partial data is ever visible.

### Phase 6 — Extending
Repeat per new source. See `docs/add_a_new_source.md`; the short version is §13.

---

## 13. Adding a new source (the extensibility answer)

The pipeline will not stay at three scripts. Adding source *n+1* must be mechanical:

**1 — Source module** in `etl/sources/` implementing the ABC:

```python
class Source(ABC):
    name: str
    def extract(self, run_date: date) -> Iterator[DataFrame]:  # chunked
        ...
```

**2 — Staging table** via Alembic migration. Explicit DDL, close to the source shape.

**3 — Pipeline module** in `etl/pipelines/` that calls `incremental_load()` and declares its DQ
rules. It is a *new file*. It never edits an existing pipeline (R8).

**4 — Register it** in the run order in `etl/cli.py`, with its dependency (which dimensions it
needs, and must therefore run after).

**5 — Extend the contract** by adding a `sql/rpt/v_*.sql` file.

**6 — Document it** in `docs/data_catalog.md`.

A structurally similar new source should be a small, isolated change and should not require edits to
unrelated pipelines. Measure the actual implementation effort across the first several sources before
establishing a one-day benchmark; unexpected coupling should be treated as an architecture smell and
reviewed before adding the next source.

### Run order (dependency DAG)

```
dim_date ─────────────────────────────┐
dim_item ─────────────────────────────┤
dim_supplier ─────────────────────────┤
dim_dc ───────────────────────────────┤
                                      ├──> fact_daily_sales ──┐
ops\snapshot S1 + S2 ──────────────────┼──> fact_running_pro ─┤   (best-price is a VIEW)
                                      │                        ├──> rpt views
stg DC stock (S7) ────────────────────┼──> fact_daily_stock ──┤
stg goods receipt (S5) ───────────────┼──> fact_goods_receipt ─┤
stg assortment (S6) ───────────────────┴──> fact_assortment ───┤
master sales plan (S10) ──────────────────────> fact_demandfc_history ──┘

   └──> ops.promotion_change_log (diff of consecutive archives) ──> rpt.v_promotion_changes
```

`dim_customer` is absent by design (§5.1.1). Implement the DAG as data, not as an `if` chain, so it
can be re-ordered and parallelised later.

---

## 14. Anti-patterns to reject in review

| Anti-pattern | Why it is banned |
|---|---|
| `commit()` between DELETE and INSERT | Data loss window. Use R1. |
| `to_sql` creating a `core` table | Schema becomes a function of the data. Use Alembic. |
| `CREATE TABLE AS SELECT` for a contracted table | No constraints, no declared types, silent drift. |
| `if_exists='replace'` in a pipeline | Drops and recreates; breaks grants, views, and readers. |
| `pd.merge` on an axis=1 cross product | OOM at scale. Explode dates in SQL with `generate_series`. |
| Hardcoded credentials | R5. |
| An SMB password in a config or compose file | Windows UNC paths and Credential Manager instead. |
| `trust` auth, or connecting the ETL as `postgres` | No password is ever checked, or a leak compromises everything. |
| `listen_addresses = '*'` with a broad firewall rule | Open the port only to the subnets that need it (§11.3). |
| Copying the `pgdata` directory as a "backup" | Not transactionally consistent. Use `pg_dump` / `pg_basebackup`. |
| Copying `pgdata` between install methods | Physical directories are not portable. Logical dumps only (§11.5). |
| Unpinned or loosely-ranged Python dependencies | Native execution has no image to fall back on — commit a lockfile (§2.2). |
| `DROP TABLE` without CASCADE awareness | Fails when an `rpt` view depends on it. |
| Silent `except: pass` / bare `print` | A swallowed error is an invisible data outage. |
| Bare `TRUNCATE` on a large fact table outside a transaction | Non-atomic, and blocks readers. |
| Committing a schema change and a data load together | Unreviewable, unrevertible. |
| Globbing `*.accdb` / `*.xlsx` in a source directory | Pulls `back_up_*.accdb` and other non-contract files. Use an explicit filename allowlist. |
| A pre-conversion step (Excel → CSV) before load | Creates a second representation with no owner, schema, or DQ check. A silent-drift shadow copy. Read xlsx directly with `openpyxl`. |
| Coalescing `Pro_Price` and `PromoPrice_per_Piece` | They disagree on 174 rows. Coalescing picks a winner silently. Carry both, rule explicitly. |
| `Status.str.lower() == 'cancel'` without stripping NBSP | Passes the 3 `\xa0cancel` rows. Non-promoted statuses (`Revised1..3`) also leak through. |
| `GREATEST(stock_qty, 0)` | Destroys the negative-quantity backorder signal. |
| `to_sql(if_exists='append')` across many files with `TRUNCATE` disabled | Duplicates every row on re-run. Violates R4. |
| Joining to a source column named `cus_code` as if it were a customer | It is the CJ item. Verified in §5.1.1. |
| `pd.read_csv(...)` without `encoding='utf-8-sig'` on these sources | BOM makes the first column `\ufeffdate`. |
| Opening the `.accdb` in place on the network share | 516 MB with a live `.laccdb` lock — fails or reads a torn file. Copy to local temp first. |
| `DROP TABLE` before diffing the previous state | The "before" is destroyed and change detection becomes impossible. Snapshot into a temp table first. |
| `pro_type = 'MAIL'` compared case-sensitively | **143 rows of `Mail`** fall through to `ELSE 3`, lose tie-break precedence to `OTHER`, and resolve to the wrong price. Fixed by the §4.3 upper-case rule, applied by pattern rather than by enumerating variants |
| Rewriting a business enum value to match a style rule | `7-Days` → `7_days` breaks joins against Power BI, the Access master, and anything the business exports. Enum values are data, not identifiers (§4.1). |
| Ranking `pro_type` above price in the tie-break | Picks MAIL over a **cheaper** competing promotion. The rule is *lowest price first*; `MAIL` breaks ties on equal price only. |
| Reconstructing `valid_from`/`valid_to` from daily rows via `MIN/MAX` | Silently wrong when promotions interleave — 53.5% of slots overlap (Rule M10). |
| Naming an append-only log `rpt.v_*` | A view has no memory. A log is a table; a view may only read it. |
| Aggregating a change log at load time | Only the new side exists then, so the aggregate cannot be compared against anything. Aggregate in the view. |

---

## 15. Decisions to revisit later

Not now — recorded so they are not re-litigated:

**Blocking — these change the model, not just a staging table:**

_None outstanding. All were resolved during design; see §5.1.1._

**Resolved during design (§5.1.1):**

| Question | Ruling |
|---|---|
| Change-detection substrate | **Option C — archive the newest daily file.** Both sources now publish one full CSV per business date; older files are the owner's own history and are never loaded. The archive keeps exactly the version each load read. 5.9 MB/upload; 30 snapshots ≈ 0.18 GB |
| Which price column is authoritative? | S1 `PromoPrice_per_Piece` and S2 `Pro_Price` both map to `promo_price_per_piece`. S1's own `Pro_Price` is a different field, not loaded (kept in `stg` under a distinct nullable name) |
| Tie-break ordering | **Lowest price wins; `MAIL` preferred only on equal price.** This is what `get_data_progrid.py` already does — an earlier revision of this document stated the rule backwards and has been corrected |
| `pro_type` case | **Resolved by rule, not enumeration.** §4.3 upper-cases every enum value, so `pro_type = 'MAIL'` matches all **5,339** rows including the `Mail` variants previously mis-ranked as `Other`. Case-only duplicates fold automatically; a new variant cannot silently break a join |
| Upper-casing an enum value | `7-Days` → `7-DAYS`. Safe: it stops at case, so `7-DAYS_*` stays distinct and Thai-script values are untouched |
| `snake_case` applied to an enum **value** | `7-DAYS` → `7_days` breaks joins against Power BI and the Access master. `snake_case` is an identifier rule (§4.1) and does not reach data |
| Rewriting an enum value beyond case | Stripping a hyphen or translating a Thai-script value to `OTHER` hides a classification the business may consider meaningful. Preserve the spelling; declare the meaning in `ref_*` |
| `Status = Revised1/2/3`, `adhoc`, `0`, `New` | All treated as **active**. Only `Cancel` (any case, NBSP-normalised) is a cancellation |
| Does a cancellation apply retroactively? | **Policy A — retain.** Cancelled *before* `valid_from` never gets priced; one cancelled *after* keeps its historical prices and is flagged |
| TD vs CJ sellout | Same fact, `sales_channel` discriminator, shared `cj_item` key |
| Which stock layout? | `DC_DailyStock\2026\N.Mon\` only — 234 files → 2026-09-21. Root-level dailies excluded |
| Assortment history? | Rolling window. Full refresh, documented R7 exception. No YoY OOS possible |
| Is `fact_progrid_bestprice` a table? | **A view.** It removes only 9.4% of rows — it exists for semantics, not size (Rule M11) |

**Non-blocking:**

- **Is `OOS_FromBI.xlsx` live or superseded?** Still open — but weaker: S12 (`OOS_Stock_Status`, 89 weeks in-fact) may already answer it. Compare before building anything new.
- **Should `7-Days` outrank `EDLP`?** Still open (tie-break ranks only MAIL and EDLP; `7-Days` ranks with `Other`).
- **Should `7-Days_*` (and now `3-Days`, `14-Days`) be folded anywhere?** Open since design; all three surface in `rpt.v_unmapped_values` (252/120/322 rows). `กระชาก` was classified 2026-10-06 (D22) — the process works, these await the same ruling.
- **Should cancelled-mid-flight promos keep their pre-cancellation days?** (unchanged — Policy A says yes; filter drops them; needs fix-or-accept)
- **Should cancelled-mid-flight promos keep their pre-cancellation days?** Policy A says yes; the
  current `is_cancelled` filter drops them entirely, so the days they actually ran at a promo price are
  lost. This contradicts Policy A and needs either a fix or a documented acceptance.
- **Should zero-day promos be visible?** 3,054 non-cancelled promos win no day and are therefore
  invisible in the resolved grid. If a consumer must see them, they need a path to the archive or to
  `stg` — not to the daily fact.
- **What is the SLA on the sales plan source?** Latest file is `2026_09` against a current month of
  2026-10 — one month stale. Plan-vs-actual reporting is only as current as this file.
- **Who actually consumes this, and from where?** Resolve early (§11.3). "Local, no cloud" and "the
  whole supply chain team" are in tension: a local service cannot serve anyone off this machine or
  network. If consumers turn out to be beyond the LAN, either a read replica / second instance, or
  the no-cloud constraint, has to give. Everything about access control, monitoring, and backups
  depends on the answer.
- **Refresh cadence** — daily incremental is assumed. A near-real-time path would need a different
  ingestion pattern.
- **Aggregates / materialised views** — justified only after `EXPLAIN` shows the `rpt` views are the
  bottleneck. Premature now.
- **Power BI DirectQuery vs Import** — Import with a scheduled refresh is the default assumption.
  DirectQuery changes the indexing and contract-layer calculus.
- **PostgreSQL major-version upgrades** — rehearsed in the Docker sandbox first, then a
  `pg_dump`/`pg_restore` into a fresh 17.x cluster, and only then a service cutover. Native installs
  do not support in-place major upgrades.
- **Data catalog tooling** (OpenMetadata, dbt) — worth revisiting once there are ~10 `rpt` views and
  manual lineage stops being sufficient.
- **iTMS purchasing/transport tables** (`tbl_POHeader`, `tbl_AllPO_Details`, `SHM_PO.txt`) and
  `Data For Stock\DC_Store` / `Tier_Update` — identified but out of scope. A future phase, with the
  same R8 discipline.
- **`dim_item.shm_item`** is captured but unused. Revisit if a non-CJ sales channel ever appears.

---

## 16. Definition of done (per table)

A table is finished when all of these are true:

- [ ] Explicit DDL in a migration, with PK, FKs, NOT NULL, and a declared grain
- [ ] Loaded by a module in `etl/pipelines/`, incremental and idempotent (R4)
- [ ] One transaction per load (R1)
- [ ] DQ checks declared and passing (R3)
- [ ] Source-to-target reconciliation declared, tested, and passing
- [ ] `ops.etl_run` + `dq_result` rows written
- [ ] Present in `rpt.v_freshness`
- [ ] Grain, columns, refresh cadence, and owner documented in `docs/data_catalog.md`
- [ ] An integration test covers it
