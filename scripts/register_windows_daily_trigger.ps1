[CmdletBinding()]
param(
    [switch]$WhatIf
)

$ErrorActionPreference = "Stop"
$TaskPath = "\Horizon\"
$TaskName = "Pangmen Daily Radar"
$ScriptPath = Join-Path $PSScriptRoot "trigger_daily_horizon.ps1"
$PowerShellPath = "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe"
$LocalAppData = [Environment]::GetFolderPath([Environment+SpecialFolder]::LocalApplicationData)
$LogPath = Join-Path $LocalAppData "Horizon\logs\daily-trigger.log"
$Arguments = "-NoProfile -ExecutionPolicy Bypass -File `"$ScriptPath`" -LogPath `"$LogPath`""

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
    -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 5)

if ($WhatIf) {
    Write-Output "WOULD REGISTER $TaskPath$TaskName at 09:15 and 09:35 including battery power, 3 task-level restarts, and a persistent log"
    exit 0
}

Register-ScheduledTask `
    -TaskName $TaskName `
    -TaskPath $TaskPath `
    -Action $action `
    -Trigger $triggers `
    -Settings $settings `
    -Description "Dispatches Horizon's full daily radar workflow at 09:15, with a 09:35 fallback and three five-minute task restarts." `
    -Force | Out-Null

Write-Output "REGISTERED $TaskPath$TaskName at 09:15 and 09:35 including 3 task-level restarts"
