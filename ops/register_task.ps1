#Requires -Version 5.1
<#
.SYNOPSIS
  Register the SCM steady-state daily job: Mon–Fri 10:00 (§Phase 5).

  The job runs ops\daily_run.ps1 (dims -> sellout latest -> progrid ->
  goods receipt -> weekly stock -> latest DC snapshot), which reads PG*
  connection env vars from MACHINE/USER level — set those first:

    [Environment]::SetEnvironmentVariable("PGHOST","127.0.0.1","Machine")
    [Environment]::SetEnvironmentVariable("PGPORT","5432","Machine")
    [Environment]::SetEnvironmentVariable("PGDATABASE","scm","Machine")
    [Environment]::SetEnvironmentVariable("PGUSER","etl_writer","Machine")
    [Environment]::SetEnvironmentVariable("PGPASSWORD","<etl_writer pw>","Machine")

  History/backfill entrypoints (load-month, etl.history.*) are NOT scheduled.
#>
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$Runner = Join-Path $Root "ops\daily_run.ps1"
$Action = New-ScheduledTaskAction -Execute "powershell.exe" `
    -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$Runner`"" `
    -WorkingDirectory $Root
$Trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday,Tuesday,Wednesday,Thursday,Friday -At 10:00am
Register-ScheduledTask -TaskName "SCM-ETL-Daily-1000" -Action $Action -Trigger $Trigger `
    -Description "SCM steady-state daily load, Mon-Fri 10:00 (ops\daily_run.ps1)" -Force
Write-Output "OK: SCM-ETL-Daily-1000 registered (Mon-Fri 10:00)"
Get-ScheduledTask -TaskName "SCM-ETL-Daily-1000" | Select-Object TaskName, State
