#Requires -Version 5.1
<#
.SYNOPSIS
  SCM steady-state daily ETL --- datasource -> database, every step in run order.
  Mon---Fri 10:00 via Task Scheduler (ops\register_task.ps1). Run by hand too:

    powershell -File ops\daily_run.ps1

  Secrets: PG* connection env vars must exist at MACHINE or USER level
  (set once, outside this repo --- R5: no credential in source):
    [Environment]::SetEnvironmentVariable("PGHOST","127.0.0.1","Machine")
    ... PGPORT=5432 PGDATABASE=scm PGUSER=etl_writer PGPASSWORD=<etl_writer pw>
  Teams: set SCM_TEAMS_WEBHOOK (machine level) to an Incoming Webhook /
  Power Automate HTTP-trigger URL and the run posts a GREEN/RED card.
  Without it, notification is skipped (log only).
  Exits 0 when every step is OK/SKIP; exits N (failed steps) otherwise.
  Log: ops\logs\daily_YYYYMMDD.log
#>
$ErrorActionPreference = "Continue"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$Python = Join-Path $Root ".venv\Scripts\python.exe"
$LogDir = Join-Path $Root "ops\logs"
$Stamp = Get-Date -Format yyyyMMdd
$Log = Join-Path $LogDir "daily_${Stamp}.log"

function Log-Line($msg) {
    $line = "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') $msg"
    $line | Out-File -FilePath $Log -Append -Encoding utf8
    Write-Output $line
}

function Send-Teams($title, $color, $lines) {
    $hook = (Get-Item "Env:SCM_TEAMS_WEBHOOK" -ErrorAction SilentlyContinue).Value
    if (-not $hook) {
        Log-Line "Teams notification skipped (SCM_TEAMS_WEBHOOK not set)"
        return
    }
    $text = @($lines | Select-Object -Last 12) -join "`n"
    if ($hook -match "powerplatform\.com") {
        # Power Automate "Post card" action: full Adaptive Card JSON.
        # The flow passes the whole trigger body as the card.
        # Table style: header + one row per step (STEP | RESULT).
        $accent = if ($color -eq "00FF00") { "Good" } else { "Attention" }
        $rows = @(
            @{ type = "ColumnSet"; columns = @(
                @{ type = "Column"; weight = "1";
                   items = @(@{ type = "TextBlock"; text = "STEP";
                                 weight = "Bolder" }) },
                @{ type = "Column"; weight = "2";
                   items = @(@{ type = "TextBlock"; text = "RESULT";
                                 weight = "Bolder" }) }
            ) }
        )
        foreach ($ln in ($lines | Select-Object -Last 12)) {
            $parts = $ln -split " ", 2
            $rows += @{ type = "ColumnSet"; separator = $true; columns = @(
                @{ type = "Column"; weight = "1";
                   items = @(@{ type = "TextBlock"; text = $parts[0];
                                 wrap = $true }) },
                @{ type = "Column"; weight = "2";
                   items = @(@{ type = "TextBlock"; text = $parts[1];
                                 wrap = $true }) }
            ) }
        }
        $body = @{
            type = "AdaptiveCard"
            '$schema' = "http://adaptivecards.io/schemas/adaptive-card.json"
            version = "1.4"
            body = @(
                @{ type = "TextBlock"; text = $title; weight = "Bolder"
                   size = "Medium"; color = $accent }
            ) + $rows
        } | ConvertTo-Json -Depth 8
    } else {
        # Legacy Incoming Webhook: MessageCard.
        $facts = @($lines | Select-Object -Last 12 | ForEach-Object {
            @{ name = "step"; value = $_ }
        })
        $body = @{
            "@type" = "MessageCard"; "@context" = "https://schema.org/extensions"
            summary = $title; themeColor = $color
            sections = @(@{ activityTitle = $title; facts = $facts })
        } | ConvertTo-Json -Depth 5
    }
    try {
        Invoke-RestMethod -Uri $hook -Method Post -Body $body `
            -ContentType "application/json" -TimeoutSec 30 | Out-Null
        Log-Line "Teams notification posted"
    } catch {
        Log-Line "Teams notification FAILED: $($_.Exception.Message)"
    }
}

# Weekend guard (trigger is weekdays-only; this is defense in depth).
$dow = (Get-Date).DayOfWeek
if ($dow -eq "Saturday" -or $dow -eq "Sunday") {
    New-Item -ItemType Directory -Path $LogDir -Force | Out-Null
    Log-Line "SKIP weekend ($dow) --- nothing to do"
    exit 0
}

New-Item -ItemType Directory -Path $LogDir -Force | Out-Null
Log-Line "=== SCM daily run start ==="

foreach ($v in @("PGHOST", "PGPORT", "PGDATABASE", "PGUSER", "PGPASSWORD")) {
    if (-not (Get-Item "Env:$v" -ErrorAction SilentlyContinue).Value) {
        Log-Line "FATAL missing env $v --- set machine-level PG* vars (see script header)"
        exit 99
    }
}

$Today = Get-Date -Format yyyy-MM-dd

# Step 0: validate everything BEFORE any load writes. A FAIL aborts the
# run — fix at source first, then rerun. Nothing is half-loaded by design.
Log-Line "--- step: validate <- etl validate ---"
$out = & $Python -m etl validate 2>&1 | Out-String
$ValidLines = @($out.Trim() -split "`r?`n" | Where-Object { $_.Trim() -ne "" })
foreach ($vl in ($ValidLines | Select-Object -Last 12)) { Log-Line "[validate] $vl" }
if ($LASTEXITCODE -ne 0) {
    Log-Line "=== SCM daily run RED: validate FAILED, loads aborted ==="
    Send-Teams "SCM daily ${Today}: VALIDATION FAILED, loads aborted" "FF0000" @(
        "validate failed - fix at source, then rerun",
        ($ValidLines | Where-Object { $_ -match "^FAIL" } | Select-Object -First 8)
    )
    exit 1
}

$Steps = @(
    @("dims", @("run", "--date", $Today)),
    @("sellout-CJ-latest", @("load-latest", "--channel", "CJ")),
    @("sellout-TD-latest", @("load-latest", "--channel", "TD")),
    @("progrid", @("load-progrid")),
    @("goods-receipt", @("load-gr")),
    @("sales-plan", @("load-plan")),
    @("weekly-stock", @("load-weekly-stock")),
    @("oos-status", @("load-oos-status"))
)

# DC daily: newest root snapshot, passed by exact filename (files land at root).
$DcRoot = if ($env:SCM_DC_ROOT) { $env:SCM_DC_ROOT } else { "\\fileserver\Supply Chain\Back_Office_SCM\Data For Stock\DC_DailyStock" }
$DcLatest = Get-ChildItem -Path $DcRoot -Filter "DC_End*.xlsx" -File -ErrorAction SilentlyContinue |
    Where-Object { $_.Name -match "^DC_End(\d{2})-(\d{2})-(\d{4})\.xlsx$" -and -not $_.Name.StartsWith("~") } |
    Sort-Object Name -Descending | Select-Object -First 1
if ($DcLatest) {
    $DcFile = $DcLatest.Name
    # Unary comma: without it += unwraps the pair and foreach iterates
    # the name string char-by-char ("d", "c", ...).
    $Steps += ,@("dc-stock-$DcFile", @("load-dc-stock", "--file", $DcFile))
} else {
    Log-Line "WARN no root DC snapshot found --- dc-stock step skipped"
}

$StepSummary = @()
$Failed = @()
foreach ($s in $Steps) {
    $name, $stepArgs = $s[0], $s[1]
    Log-Line "--- step: $name <- etl $($stepArgs -join ' ') ---"
    $out = & $Python -m etl @stepArgs 2>&1 | Out-String
    $tail = (($out.Trim() -split "`r?`n" | Where-Object { $_.Trim() -ne "" } | Select-Object -Last 3) -join " | ")
    Log-Line "[$name] $tail"
    if ($LASTEXITCODE -ne 0) {
        Log-Line "[$name] FAILED (exit $LASTEXITCODE)"
        $Failed += $name
        $StepSummary += "$name FAILED"
    } else {
        $StepSummary += "$name $tail"
    }
}

if ($Failed.Count -eq 0) {
    Log-Line "=== SCM daily run GREEN (all steps OK/SKIP) ==="
    Send-Teams "SCM daily $Today`: GREEN" "00FF00" $StepSummary
    exit 0
} else {
    Log-Line "=== SCM daily run RED: $($Failed -join ', ') ==="
    Send-Teams "SCM daily $Today`: RED ($($Failed -join ', '))" "FF0000" $StepSummary
    exit $Failed.Count
}
