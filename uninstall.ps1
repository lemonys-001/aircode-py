<#
.SYNOPSIS
    Uninstall aircode-py on Windows.
.PARAMETER Prefix
    Install root used by install.ps1. Default: %LOCALAPPDATA%\aircode-py
.PARAMETER Purge
    Also delete user data, config and cache.
.PARAMETER DryRun
    Print what would be removed.
.PARAMETER Yes
    Do not prompt.
#>
param(
    [string]$Prefix = $(Join-Path $env:LOCALAPPDATA "aircode-py"),
    [switch]$Purge,
    [switch]$DryRun,
    [switch]$Yes
)

$ErrorActionPreference = "Stop"
$App = "aircode-py"
$BinDir = Join-Path $Prefix "bin"
$uvTool = Join-Path $env:LOCALAPPDATA "uv\tools\aircode-py"
$targets = @(
    (Join-Path $BinDir "$App.exe"),
    (Join-Path $BinDir "$App.cmd"),
    (Join-Path $Prefix "venv"),
    $uvTool
)
if ($Purge) {
    $targets += (Join-Path $env:LOCALAPPDATA "aircode")
    $targets += (Join-Path $env:APPDATA "aircode")
}

$found = @($targets | Where-Object { Test-Path $_ })
if ($found.Count -eq 0) {
    Write-Host "Nothing to uninstall: no aircode-py installation found."
    exit 0
}

Write-Host "The following will be removed:"
$found | ForEach-Object { Write-Host "  - $_" }
if (-not $Purge) {
    Write-Host "User data is kept. Use -Purge for a full wipe." -ForegroundColor Yellow
}
if ($DryRun) {
    Write-Host "Dry run: nothing was deleted."
    exit 0
}
if (-not $Yes) {
    $reply = Read-Host "Proceed? [y/N]"
    if ($reply -notmatch '^(y|yes)$') { Write-Host "Aborted."; exit 1 }
}

if (Get-Command uv -ErrorAction SilentlyContinue) { & uv tool uninstall aircode-py 2>$null }
$found | ForEach-Object { Remove-Item -Recurse -Force $_ }
$userPath = [Environment]::GetEnvironmentVariable("Path", "User")
if ($userPath) {
    $newPath = (@($userPath -split ";" | Where-Object { $_ -and $_ -ne $BinDir }) -join ";")
    [Environment]::SetEnvironmentVariable("Path", $newPath, "User")
}
Write-Host "aircode-py uninstalled."
