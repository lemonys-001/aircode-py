<#
.SYNOPSIS
    Install aircode-py on Windows.
.DESCRIPTION
    Downloads the latest release asset, verifies SHA256SUMS, installs it to
    %LOCALAPPDATA%\aircode-py\bin, and adds that folder to the user PATH.

    One-liner:
      irm https://raw.githubusercontent.com/lemonys-001/aircode-py/main/install.ps1 | iex
.PARAMETER Prefix
    Install root. Default: %LOCALAPPDATA%\aircode-py
.PARAMETER Version
    Release tag, for example v0.1.0. Default: latest
.PARAMETER Source
    Install from source instead of a release binary.
.PARAMETER Gui
    With -Source, include PySide6 for the native window.
.PARAMETER NoModifyPath
    Do not edit the user PATH.
#>
param(
    [string]$Prefix = $(Join-Path $env:LOCALAPPDATA "aircode-py"),
    [string]$Version = "latest",
    [switch]$Source,
    [switch]$Gui,
    [switch]$NoModifyPath
)

$ErrorActionPreference = "Stop"
$App = "aircode-py"
$Repo = if ($env:AIRCODE_REPO) { $env:AIRCODE_REPO } else { "lemonys-001/aircode-py" }
if ($env:AIRCODE_PREFIX) { $Prefix = $env:AIRCODE_PREFIX }
if ($env:AIRCODE_VERSION) { $Version = $env:AIRCODE_VERSION }
$BinDir = Join-Path $Prefix "bin"
$Exe = Join-Path $BinDir "$App.exe"
New-Item -ItemType Directory -Force -Path $BinDir | Out-Null

function Write-Info($Message) { Write-Host $Message -ForegroundColor Blue }
function Write-Err($Message) { throw "error: $Message" }

function Add-ToPath {
    if ($NoModifyPath) { return }
    $userPath = [Environment]::GetEnvironmentVariable("Path", "User")
    $parts = @($userPath -split ";" | Where-Object { $_ })
    if ($parts -notcontains $BinDir) {
        $newPath = if ($userPath) { "$BinDir;$userPath" } else { $BinDir }
        [Environment]::SetEnvironmentVariable("Path", $newPath, "User")
        Write-Info "Added $BinDir to your user PATH. Open a new terminal."
    }
}

function Install-Source {
    if ($PSScriptRoot -and (Test-Path (Join-Path $PSScriptRoot "pyproject.toml"))) {
        $spec = $PSScriptRoot
    } else {
        $spec = "aircode-py @ git+https://github.com/$Repo.git"
    }
    if ($Gui) { $spec = "$spec[gui]" }

    if (Get-Command uv -ErrorAction SilentlyContinue) {
        $env:UV_LINK_MODE = "copy"
        $env:UV_TOOL_BIN_DIR = $BinDir
        & uv tool install --force --python 3.13 $spec
        if ($LASTEXITCODE -eq 0) {
            Add-ToPath
            Write-Info "aircode-py installed at $Exe"
            return
        }
        Write-Host "uv failed; falling back to a Python venv."
    }
    if (Get-Command py -ErrorAction SilentlyContinue) {
        $venv = Join-Path $Prefix "venv"
        & py -3.13 -m venv $venv
        & (Join-Path $venv "Scripts\python.exe") -m pip install --upgrade pip
        & (Join-Path $venv "Scripts\python.exe") -m pip install $spec
        if ($LASTEXITCODE -ne 0) { Write-Err "pip could not install aircode-py" }
        $shim = Join-Path $BinDir "$App.cmd"
        Set-Content -Path $shim -Value "@echo off`r`n`"$venv\Scripts\$App.exe`" %*"
        Add-ToPath
        Write-Info "aircode-py installed at $shim"
        return
    }
    Write-Err "install uv or Python 3.11+ to install from source"
}

if ($Source) {
    Install-Source
    exit 0
}

$arch = [System.Runtime.InteropServices.RuntimeInformation]::OSArchitecture
if ($arch -ne "X64") {
    Write-Info "No prebuilt binary for Windows $arch. Installing from source."
    $Gui = $true
    Install-Source
    exit 0
}

$asset = "$App-windows-x86_64.exe"
$tmp = Join-Path ([System.IO.Path]::GetTempPath()) ("aircode-py-" + [System.Guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Force -Path $tmp | Out-Null

if ($Version -eq "latest") {
    $base = "https://github.com/$Repo/releases/latest/download"
} else {
    $base = "https://github.com/$Repo/releases/download/$Version"
}

$downloaded = $false
if (Get-Command gh -ErrorAction SilentlyContinue) {
    if ($Version -eq "latest") {
        & gh release download --repo $Repo --pattern $asset --pattern SHA256SUMS --dir $tmp 2>$null
    } else {
        & gh release download $Version --repo $Repo --pattern $asset --pattern SHA256SUMS --dir $tmp 2>$null
    }
    $downloaded = Test-Path (Join-Path $tmp $asset)
}

if (-not $downloaded) {
    try {
        Invoke-WebRequest -Uri "$base/$asset" -OutFile (Join-Path $tmp $asset) -UseBasicParsing
        Invoke-WebRequest -Uri "$base/SHA256SUMS" -OutFile (Join-Path $tmp "SHA256SUMS") -UseBasicParsing
        $downloaded = $true
    } catch {
        Write-Info "No prebuilt binary for Windows x86_64 ($Version). Installing from source."
        $Gui = $true
        Install-Source
        exit 0
    }
}

$sums = Join-Path $tmp "SHA256SUMS"
if (-not (Test-Path $sums)) { Write-Err "release is missing SHA256SUMS; refusing to install" }
$line = Get-Content $sums | Where-Object { $_ -match "\s\*?$([regex]::Escape($asset))$" } | Select-Object -First 1
if (-not $line) { Write-Err "SHA256SUMS does not list $asset" }
$expected = (($line -split "\s+")[0]).ToLowerInvariant()
$actual = (Get-FileHash (Join-Path $tmp $asset) -Algorithm SHA256).Hash.ToLowerInvariant()
if ($expected -ne $actual) { Write-Err "SHA-256 verification failed for $asset" }

Copy-Item -Force (Join-Path $tmp $asset) $Exe
Remove-Item -Recurse -Force $tmp
Add-ToPath
Write-Info "aircode-py installed at $Exe"
& $Exe --version
