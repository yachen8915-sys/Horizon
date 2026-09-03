[CmdletBinding()]
param(
    [ValidateRange(1, 168)]
    [int]$Hours = 24,
    [string]$ConfigPath = "data/config.github.json",
    [string]$UvPath = "",
    [string]$LogPath = ""
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot

if ([string]::IsNullOrWhiteSpace($UvPath)) {
    $uvCommand = Get-Command "uv" -ErrorAction SilentlyContinue
    if ($null -eq $uvCommand) {
        throw "uv was not found on PATH"
    }
    $UvPath = $uvCommand.Source
}

if ([string]::IsNullOrWhiteSpace($LogPath)) {
    $localAppData = [Environment]::GetFolderPath([Environment+SpecialFolder]::LocalApplicationData)
    $LogPath = Join-Path $localAppData "Horizon\logs\source-shadow.log"
}

$logDirectory = Split-Path -Parent $LogPath
if (-not (Test-Path -LiteralPath $logDirectory)) {
    New-Item -ItemType Directory -Path $logDirectory -Force | Out-Null
}

$startedAt = [DateTimeOffset]::Now.ToString("yyyy-MM-dd HH:mm:ss zzz")
Add-Content -LiteralPath $LogPath -Value "$startedAt START source-only shadow" -Encoding UTF8

Push-Location $ProjectRoot
try {
    $output = @(
        & $UvPath run python scripts/run_source_shadow.py `
            --config $ConfigPath `
            --hours $Hours 2>&1
    )
    $exitCode = $LASTEXITCODE
    if ($output.Count -gt 0) {
        Add-Content -LiteralPath $LogPath -Value $output -Encoding UTF8
    }
    $finishedAt = [DateTimeOffset]::Now.ToString("yyyy-MM-dd HH:mm:ss zzz")
    Add-Content -LiteralPath $LogPath -Value "$finishedAt END exit=$exitCode" -Encoding UTF8
    if ($exitCode -ne 0) {
        throw "source-only shadow exited with code $exitCode; see $LogPath"
    }
}
finally {
    Pop-Location
}
