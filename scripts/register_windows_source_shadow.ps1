[CmdletBinding()]
param(
    [switch]$WhatIf,
    [switch]$Enable
)

$ErrorActionPreference = "Stop"
$TaskPath = "\Horizon\"
$TaskName = "Pangmen Source Shadow"
$RunnerPath = Join-Path $PSScriptRoot "run_local_source_shadow.ps1"
$PowerShellPath = "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe"
$LocalAppData = [Environment]::GetFolderPath([Environment+SpecialFolder]::LocalApplicationData)
$LogPath = Join-Path $LocalAppData "Horizon\logs\source-shadow.log"
$Times = @("08:30", "15:30")

if (-not (Test-Path -LiteralPath $RunnerPath)) {
    throw "Source shadow runner not found: $RunnerPath"
}

if (-not $Enable) {
    Write-Output "PREPARED $TaskPath$TaskName at $($Times -join '/') for source-only shadow; not registered because -Enable was not provided"
    exit 0
}

$arguments = "-NoProfile -ExecutionPolicy Bypass -File `"$RunnerPath`" -Hours 24 -LogPath `"$LogPath`""
$action = New-ScheduledTaskAction -Execute $PowerShellPath -Argument $arguments
$triggers = @($Times | ForEach-Object { New-ScheduledTaskTrigger -Daily -At $_ })
$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -WakeToRun `
    -MultipleInstances IgnoreNew `
    -RestartCount 2 `
    -RestartInterval (New-TimeSpan -Minutes 5)
$principal = New-ScheduledTaskPrincipal `
    -UserId "$env:USERDOMAIN\$env:USERNAME" `
    -LogonType Interactive `
    -RunLevel Limited

if ($WhatIf) {
    Write-Output "WOULD REGISTER $TaskPath$TaskName at $($Times -join '/') for source-only shadow"
    exit 0
}

Register-ScheduledTask `
    -TaskName $TaskName `
    -TaskPath $TaskPath `
    -Action $action `
    -Trigger $triggers `
    -Settings $settings `
    -Principal $principal `
    -Description "Horizon source-only shadow. No AI, delivery, or production state." `
    -Force | Out-Null

Write-Output "REGISTERED $TaskPath$TaskName at $($Times -join '/') for source-only shadow"
