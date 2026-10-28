#Requires -Version 5.1
<#
.SYNOPSIS
  Bootstrap the SCM ETL venv from the lockfile (§2.2).
.USAGE
  powershell -ExecutionPolicy Bypass -File ops\setup.ps1
#>
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)

uv python install 3.12
if ($?) { uv sync --project $Root }
if ($?) { Write-Output "OK: venv ready at $Root\.venv (Python 3.12, locked deps)" }
