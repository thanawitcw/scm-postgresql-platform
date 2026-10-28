#Requires -Version 5.1
<#
.SYNOPSIS
  Nightly logical dump (pg_dump -Fc), retained 14 days (§11.4).
#>
$ErrorActionPreference = "Stop"
$Stamp = Get-Date -Format yyyyMMdd
$Dest = "D:\pg_backup\scm_${Stamp}.dump"
if (-not $env:PGPASSWORD) {
  Write-Output "FATAL: PGPASSWORD not set - pg_dump would hang on a password prompt. Set machine-level PG* vars."
  exit 99
}
New-Item -ItemType Directory -Path "D:\pg_backup" -Force | Out-Null
& "C:\Program Files\PostgreSQL\18\bin\pg_dump.exe" -h 127.0.0.1 -U scm_admin -Fc -d scm -f $Dest
if ($?) {
  Get-ChildItem "D:\pg_backup\scm_*.dump" | Where-Object { $_.LastWriteTime -lt (Get-Date).AddDays(-14) } | Remove-Item -Force
  Write-Output "OK: $Dest"
}
