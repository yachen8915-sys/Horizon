[CmdletBinding()]
param(
    [switch]$WhatIf,
    [DateTimeOffset]$NowUtc = [DateTimeOffset]::UtcNow,
    [string]$WorkflowRunsJson,
    [string]$GhPath = "C:\Program Files\GitHub CLI\gh.exe",
    [ValidateRange(1, 10)]
    [int]$MaxAttempts = 4,
    [ValidateRange(0, 300)]
    [int]$RetryDelaySeconds = 15,
    [string]$LogPath = ""
)

$ErrorActionPreference = "Stop"

$Repository = "yachen8915-sys/Horizon"
$Workflow = "daily-summary.yml"
$ChinaTimeZone = [TimeZoneInfo]::FindSystemTimeZoneById("China Standard Time")
$TodayInChina = [TimeZoneInfo]::ConvertTime($NowUtc, $ChinaTimeZone).Date

if ([string]::IsNullOrWhiteSpace($LogPath)) {
    $localAppData = [Environment]::GetFolderPath([Environment+SpecialFolder]::LocalApplicationData)
    if ([string]::IsNullOrWhiteSpace($localAppData)) {
        $localAppData = Split-Path -Parent $PSScriptRoot
    }
    $LogPath = Join-Path $localAppData "Horizon\logs\daily-trigger.log"
}

function Write-TriggerLog {
    param([string]$Message)

    try {
        $logDirectory = Split-Path -Parent $LogPath
        if ($logDirectory -and -not (Test-Path -LiteralPath $logDirectory)) {
            New-Item -ItemType Directory -Path $logDirectory -Force | Out-Null
        }
        $timestamp = [DateTimeOffset]::Now.ToString("yyyy-MM-dd HH:mm:ss zzz")
        Add-Content -LiteralPath $LogPath -Value "$timestamp $Message" -Encoding UTF8
    }
    catch {
        # Logging must never prevent the dispatcher from running.
    }
}

function Invoke-GhWithRetry {
    param(
        [string[]]$Arguments,
        [string]$Operation
    )

    for ($attempt = 1; $attempt -le $MaxAttempts; $attempt++) {
        $previousErrorActionPreference = $ErrorActionPreference
        try {
            # Windows PowerShell 5.1 turns native stderr into a terminating
            # NativeCommandError when the global preference is Stop. Capture
            # it first so transient gh failures can be logged and retried.
            $ErrorActionPreference = "Continue"
            $output = @(& $GhPath @Arguments 2>&1)
            $exitCode = $LASTEXITCODE
        }
        finally {
            $ErrorActionPreference = $previousErrorActionPreference
        }
        if ($exitCode -eq 0) {
            if ($attempt -gt 1) {
                Write-TriggerLog "$Operation recovered on attempt $attempt/$MaxAttempts"
            }
            return ($output -join [Environment]::NewLine)
        }

        $detail = (($output | ForEach-Object { $_.ToString().Trim() }) -join " ").Trim()
        Write-TriggerLog "$Operation failed on attempt $attempt/$MaxAttempts (exit=$exitCode): $detail"
        if ($attempt -lt $MaxAttempts -and $RetryDelaySeconds -gt 0) {
            Start-Sleep -Seconds $RetryDelaySeconds
        }
    }

    throw "$Operation failed after $MaxAttempts attempts. See $LogPath"
}

Write-TriggerLog "START dispatcher"

try {
    if (-not (Test-Path -LiteralPath $GhPath)) {
        Write-TriggerLog "GitHub CLI was not found at $GhPath"
        throw "GitHub CLI was not found at $GhPath"
    }

    if ($WorkflowRunsJson) {
        $parsedRuns = $WorkflowRunsJson | ConvertFrom-Json
        $WorkflowRuns = if ($null -eq $parsedRuns) { @() } else { @($parsedRuns) }
    }
    else {
        $response = Invoke-GhWithRetry `
            -Arguments @(
                "api",
                "/repos/$Repository/actions/workflows/$Workflow/runs?event=workflow_dispatch&per_page=30",
                "--jq",
                '.workflow_runs | map({created_at, status, conclusion})'
            ) `
            -Operation "Read recent GitHub Actions runs"
        $parsedRuns = $response | ConvertFrom-Json
        $WorkflowRuns = if ($null -eq $parsedRuns) { @() } else { @($parsedRuns) }
    }

    $TodayRuns = @(
        foreach ($run in $WorkflowRuns) {
            if ($null -eq $run -or [string]::IsNullOrWhiteSpace([string]$run.created_at)) {
                continue
            }
            $createdAt = [DateTimeOffset]::Parse($run.created_at)
            if ([TimeZoneInfo]::ConvertTime($createdAt, $ChinaTimeZone).Date -eq $TodayInChina) {
                $run
            }
        }
    )

    if ($TodayRuns | Where-Object { $_.status -ne "completed" }) {
        Write-TriggerLog "SKIP active run already exists"
        Write-Output "SKIP active run already exists"
        exit 0
    }

    if ($TodayRuns | Where-Object { $_.conclusion -eq "success" }) {
        Write-TriggerLog "SKIP successful run already exists"
        Write-Output "SKIP successful run already exists"
        exit 0
    }

    if ($TodayRuns.Count -ge 2) {
        Write-TriggerLog "SKIP retry limit reached"
        Write-Output "SKIP retry limit reached"
        exit 0
    }

    Write-TriggerLog "DISPATCH full daily run"
    Write-Output "DISPATCH full daily run"
    if ($WhatIf) {
        exit 0
    }

    Invoke-GhWithRetry `
        -Arguments @("workflow", "run", $Workflow, "--repo", $Repository, "--ref", "main", "-f", "run_mode=full") `
        -Operation "Dispatch the daily Horizon workflow" | Out-Null
    Write-TriggerLog "Dispatch accepted by GitHub"
}
catch {
    $errorText = ($_ | Out-String).Trim()
    Write-TriggerLog "FAILED: $errorText"
    throw
}
