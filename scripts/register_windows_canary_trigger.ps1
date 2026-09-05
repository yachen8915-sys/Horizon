[CmdletBinding()]
param(
    [switch]$WhatIf
)

$ErrorActionPreference = "Stop"
$TaskPath = "\Horizon\"
$TaskName = "Horizon Canary Dispatcher"
$ScriptPath = Join-Path $PSScriptRoot "trigger_horizon_canary.ps1"
$PowerShellPath = "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe"
$LocalAppData = [Environment]::GetFolderPath([Environment+SpecialFolder]::LocalApplicationData)
$LogPath = Join-Path $LocalAppData "Horizon\logs\canary-trigger.log"
$Arguments = "-NoProfile -ExecutionPolicy Bypass -File `"$ScriptPath`" -LogPath `"$LogPath`""

if (-not (Test-Path -LiteralPath $ScriptPath)) {
    throw "Canary trigger script not found: $ScriptPath"
}

$action = New-ScheduledTaskAction -Execute $PowerShellPath -Argument $Arguments
$trigger = New-ScheduledTaskTrigger -Daily -At "07:30"
$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -WakeToRun `
    -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 5)

if ($WhatIf) {
    Write-Output "WOULD REGISTER $TaskPath$TaskName at 07:30 with WakeToRun, StartWhenAvailable, and 3 task-level restarts"
    exit 0
}

Register-ScheduledTask `
    -TaskName $TaskName `
    -TaskPath $TaskPath `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -Description "Primary 07:30 dispatcher for the isolated Horizon canary GitHub Actions workflow." `
    -Force | Out-Null

Write-Output "REGISTERED $TaskPath$TaskName at 07:30 with WakeToRun"
