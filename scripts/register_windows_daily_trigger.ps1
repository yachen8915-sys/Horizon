[CmdletBinding()]
param(
    [switch]$WhatIf,
    [switch]$Enable
)

$ErrorActionPreference = "Stop"
$TaskPath = "\Horizon\"
$ScriptPath = Join-Path $PSScriptRoot "trigger_daily_horizon.ps1"
$PowerShellPath = "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe"
$LocalAppData = [Environment]::GetFolderPath([Environment+SpecialFolder]::LocalApplicationData)
$LogDirectory = Join-Path $LocalAppData "Horizon\logs"
$Definitions = @(
    @{
        Name = "Pangmen Intelligence Radar Morning"
        Mode = "morning"
        At = "09:20"
    },
    @{
        Name = "Pangmen Intelligence Radar Afternoon"
        Mode = "afternoon"
        At = "16:20"
    }
)

if (-not (Test-Path -LiteralPath $ScriptPath)) {
    throw "Daily trigger script not found: $ScriptPath"
}

if (-not $Enable) {
    foreach ($definition in $Definitions) {
        Write-Output "PREPARED $TaskPath$($definition.Name) at $($definition.At) for $($definition.Mode); not registered because -Enable was not provided"
    }
    exit 0
}

$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -WakeToRun `
    -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 5)

foreach ($definition in $Definitions) {
    $logPath = Join-Path $LogDirectory "$($definition.Mode)-trigger.log"
    $arguments = "-NoProfile -ExecutionPolicy Bypass -File `"$ScriptPath`" -RunMode $($definition.Mode) -LogPath `"$logPath`""
    $action = New-ScheduledTaskAction -Execute $PowerShellPath -Argument $arguments
    $trigger = New-ScheduledTaskTrigger -Daily -At $definition.At

    if ($WhatIf) {
        Write-Output "WOULD REGISTER $TaskPath$($definition.Name) at $($definition.At) for $($definition.Mode)"
        continue
    }

    Register-ScheduledTask `
        -TaskName $definition.Name `
        -TaskPath $TaskPath `
        -Action $action `
        -Trigger $trigger `
        -Settings $settings `
        -Description "Fallback dispatcher for Horizon $($definition.Mode) intelligence radar." `
        -Force | Out-Null

    Write-Output "REGISTERED $TaskPath$($definition.Name) at $($definition.At) for $($definition.Mode)"
}
