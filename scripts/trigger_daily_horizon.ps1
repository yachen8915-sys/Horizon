[CmdletBinding()]
param(
    [switch]$WhatIf,
    [DateTimeOffset]$NowUtc = [DateTimeOffset]::UtcNow,
    [string]$WorkflowRunsJson
)

$ErrorActionPreference = "Stop"

$Repository = "yachen8915-sys/Horizon"
$Workflow = "daily-summary.yml"
$GhPath = "C:\Program Files\GitHub CLI\gh.exe"
$ChinaTimeZone = [TimeZoneInfo]::FindSystemTimeZoneById("China Standard Time")
$TodayInChina = [TimeZoneInfo]::ConvertTime($NowUtc, $ChinaTimeZone).Date

if (-not (Test-Path -LiteralPath $GhPath)) {
    throw "GitHub CLI was not found at $GhPath"
}

if ($WorkflowRunsJson) {
    $parsedRuns = $WorkflowRunsJson | ConvertFrom-Json
    $WorkflowRuns = if ($null -eq $parsedRuns) { @() } else { @($parsedRuns) }
}
else {
    $response = & $GhPath api "/repos/$Repository/actions/workflows/$Workflow/runs?event=workflow_dispatch&per_page=30"
    if ($LASTEXITCODE -ne 0) {
        throw "Unable to read recent GitHub Actions runs."
    }
    $WorkflowRuns = @((($response | ConvertFrom-Json).workflow_runs))
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
    Write-Output "SKIP active run already exists"
    exit 0
}

if ($TodayRuns | Where-Object { $_.conclusion -eq "success" }) {
    Write-Output "SKIP successful run already exists"
    exit 0
}

if ($TodayRuns.Count -ge 2) {
    Write-Output "SKIP retry limit reached"
    exit 0
}

Write-Output "DISPATCH full daily run"
if ($WhatIf) {
    exit 0
}

& $GhPath workflow run $Workflow --repo $Repository --ref main -f run_mode=full
if ($LASTEXITCODE -ne 0) {
    throw "Unable to dispatch the daily Horizon workflow."
}
