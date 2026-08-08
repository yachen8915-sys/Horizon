[CmdletBinding()]
param(
    [switch]$WhatIf
)

$ErrorActionPreference = "Stop"
$TaskPath = "\Horizon\"
$TaskName = "Pangmen Daily Radar"
$ScriptPath = Join-Path $PSScriptRoot "trigger_daily_horizon.ps1"
$PowerShellPath = "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe"
$Arguments = "-NoProfile -ExecutionPolicy Bypass -File `"$ScriptPath`""

if (-not (Test-Path -LiteralPath $ScriptPath)) {
    throw "Daily trigger script not found: $ScriptPath"
}

$action = New-ScheduledTaskAction -Execute $PowerShellPath -Argument $Arguments
$triggers = @(
    (New-ScheduledTaskTrigger -Daily -At "09:15"),
    (New-ScheduledTaskTrigger -Daily -At "09:35")
)
$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -WakeToRun `
    -RestartCount 1 `
    -RestartInterval (New-TimeSpan -Minutes 5)

if ($WhatIf) {
    Write-Output "WOULD REGISTER $TaskPath$TaskName at 09:15 and 09:35 including battery power"
    exit 0
}

Register-ScheduledTask `
    -TaskName $TaskName `
    -TaskPath $TaskPath `
    -Action $action `
    -Trigger $triggers `
    -Settings $settings `
    -Description "Dispatches Horizon's full daily radar workflow and retries after 20 minutes when needed." `
    -Force | Out-Null

Write-Output "REGISTERED $TaskPath$TaskName at 09:15 and 09:35"
