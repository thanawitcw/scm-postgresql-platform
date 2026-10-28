#Requires -Version 5.1
#Requires -RunAsAdministrator
<#
.SYNOPSIS
  Move the PostgreSQL 18 cluster C: -> D:\pgdata (one-time, 2026-10-06).

  Run ELEVATED, once:  powershell -File ops\move_pgdata_to_d.ps1
  Downtime ~5 min (2.8 GB copy). ETL + backup tasks are paused during the
  move and re-enabled after. Old cluster renamed, NOT deleted - remove
  C:\Program Files\PostgreSQL\18\data.bak-20261006 by hand once D: is proven.
#>
$ErrorActionPreference = "Stop"
$Svc = "postgresql-x64-18"
$Old = "C:\Program Files\PostgreSQL\18\data"
$New = "D:\pgdata"
$Acct = "NT AUTHORITY\NetworkService"

function Step($m) { Write-Output ">>> $m" }

Step "0. pre-checks"
if (-not (Get-Service $Svc -ErrorAction SilentlyContinue)) { throw "service $Svc missing" }
$free = (Get-PSDrive D).Free
if ($free -lt 20GB) { throw "D: free < 20 GB, refusing" }
$Tasks = @("SCM-ETL-Daily-1000", "SCM-Backup-Nightly")
foreach ($t in $Tasks) {
    if (Get-ScheduledTask -TaskName $t -ErrorAction SilentlyContinue) {
        Disable-ScheduledTask -TaskName $t | Out-Null
        Write-Output "paused task $t"
    }
}

Step "1. stop service"
Stop-Service $Svc -Force
(Get-Service $Svc).WaitForStatus("Stopped", "00:01:00")

Step "2. copy cluster C: -> D: (ACLs preserved)"
robocopy $Old $New /MIR /COPYALL /R:3 /W:5 /NP /NDL /NFL | Out-Null
if ($LASTEXITCODE -ge 8) { throw "robocopy failed, exit $LASTEXITCODE" }
& icacls $New /grant "$($Acct):(OI)(CI)F" /T /Q
if ($LASTEXITCODE -ne 0) { throw "icacls failed" }

Step "3. repoint service ImagePath -D to D:"
$reg = "HKLM:\SYSTEM\CurrentControlSet\Services\$Svc"
$img = (Get-ItemProperty $reg).ImagePath
if ($img -match [regex]::Escape($New)) {
    Write-Output "ImagePath already on D:, skipping repoint"
} else {
    if ($img -notmatch [regex]::Escape($Old)) { throw "ImagePath has no -D $Old : $img" }
    Set-ItemProperty $reg -Name ImagePath -Value ($img -replace [regex]::Escape($Old), $New)
    Write-Output "ImagePath now: $((Get-ItemProperty $reg).ImagePath)"
}

Step "4. start + verify"
Start-Service $Svc -ErrorAction SilentlyContinue
(Get-Service $Svc).WaitForStatus("Running", "00:02:00")
$env:PGHOST = "127.0.0.1"; $env:PGPORT = "5432"
$env:PGDATABASE = "scm"; $env:PGUSER = "etl_writer"
# PGPASSWORD comes from machine env (set for the ETL); never literals here.
# NOTE: SHOW data_directory is superuser-only, so D: is proven by the
# postmaster.pid the running server holds in D:\pgdata (fresh mtime).
$pidAgeMin = ((Get-Date) - (Get-Item "$New\postmaster.pid").LastWriteTime).TotalMinutes
Write-Output ("postmaster.pid in D:\pgdata, age {0:N1} min (fresh = running from D:)" -f $pidAgeMin)
if ($pidAgeMin -gt 10) { throw "postmaster.pid is stale - service may not run from D:" }
$c = & "C:\Program Files\PostgreSQL\18\bin\psql.exe" -t -A -c "SELECT count(*) FROM core.fact_daily_dc_stock;" 2>&1
Write-Output "fact_daily_dc_stock rows = $c"

Step "5. retire old cluster dir (rename, keep until proven)"
Rename-Item $Old ($Old + ".bak-20261006") -ErrorAction SilentlyContinue
foreach ($t in $Tasks) {
    if (Get-ScheduledTask -TaskName $t -ErrorAction SilentlyContinue) {
        Enable-ScheduledTask -TaskName $t | Out-Null
        Write-Output "resumed task $t"
    }
}
Write-Output "DONE on D:. Delete $($Old).bak-20261006 only after a green daily run."
