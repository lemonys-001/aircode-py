param(
    [string]$Prefix = "$env:LOCALAPPDATA\Aircode",
    [string]$Version = "latest",
    [switch]$Source,
    [switch]$Gui
)

$ErrorActionPreference = "Stop"
$Repo = if ($env:AIRCODE_REPO) { $env:AIRCODE_REPO } else { "lemonys-001/aircode-py" }
if ($env:AIRCODE_PREFIX) { $Prefix = $env:AIRCODE_PREFIX }
if ($env:AIRCODE_VERSION) { $Version = $env:AIRCODE_VERSION }
$BinDir = Join-Path $Prefix "bin"
New-Item -ItemType Directory -Force -Path $BinDir | Out-Null

function Install-Source {
    if (Test-Path (Join-Path $PSScriptRoot "pyproject.toml")) {
        $spec = $PSScriptRoot
    } else {
        $spec = "aircode-py @ git+https://github.com/$Repo.git"
    }
    if ($Gui) {
        $spec = "$spec[gui]"
    }
    if (Get-Command uv -ErrorAction SilentlyContinue) {
        $env:UV_LINK_MODE = "copy"
        $env:UV_TOOL_BIN_DIR = $BinDir
        & uv tool install --force --python 3.13 $spec
        if ($LASTEXITCODE -eq 0) {
            Write-Host "Installed $(Join-Path $BinDir 'aircode.exe')"
            return
        }
        Write-Host "uv failed; falling back to a Python venv."
    }
    if (Get-Command py -ErrorAction SilentlyContinue) {
        $venv = Join-Path $Prefix "venv"
        & py -3.13 -m venv $venv
        & (Join-Path $venv "Scripts\python.exe") -m pip install --upgrade pip
        & (Join-Path $venv "Scripts\python.exe") -m pip install $spec
        if ($LASTEXITCODE -ne 0) { throw "pip could not install Aircode" }
        $shim = Join-Path $BinDir "aircode.cmd"
        Set-Content -Path $shim -Value "@echo off`r`n`"$venv\Scripts\aircode.exe`" %*"
        Write-Host "Installed $shim"
        return
    }
    throw "Install uv or Python 3.11+ to install from source"
}

if ($Source) {
    Install-Source
    Write-Host "Add $BinDir to PATH if it is not already there."
    exit 0
}

$arch = [System.Runtime.InteropServices.RuntimeInformation]::OSArchitecture
if ($arch -ne "X64") {
    Write-Host "No prebuilt binary for Windows $arch."
    Write-Host "Install from source with: .\install.ps1 -Source -Gui"
    exit 1
}

$asset = "aircode-windows-x86_64.exe"
$tmp = Join-Path ([System.IO.Path]::GetTempPath()) ("aircode-" + [System.Guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Force -Path $tmp | Out-Null
$downloaded = $false

if (Get-Command gh -ErrorAction SilentlyContinue) {
    if ($Version -eq "latest") {
        & gh release download --repo $Repo --pattern $asset --dir $tmp 2>$null
        & gh release download --repo $Repo --pattern SHA256SUMS --dir $tmp 2>$null
    } else {
        & gh release download $Version --repo $Repo --pattern $asset --dir $tmp 2>$null
        & gh release download $Version --repo $Repo --pattern SHA256SUMS --dir $tmp 2>$null
    }
    $downloaded = Test-Path (Join-Path $tmp $asset)
}

if (-not $downloaded) {
    if ($Version -eq "latest") {
        $base = "https://github.com/$Repo/releases/latest/download"
    } else {
        $base = "https://github.com/$Repo/releases/download/$Version"
    }
    try {
        Invoke-WebRequest -Uri "$base/$asset" -OutFile (Join-Path $tmp $asset) -UseBasicParsing
        try {
            Invoke-WebRequest -Uri "$base/SHA256SUMS" -OutFile (Join-Path $tmp "SHA256SUMS") -UseBasicParsing
        } catch {
            Write-Host "Checksum file unavailable."
        }
        $downloaded = $true
    } catch {
        Write-Host "No prebuilt binary found for Windows x86_64 ($Version)."
        Write-Host "For a private repository, run: gh auth login"
        Write-Host "Or install from source with: .\install.ps1 -Source -Gui"
        exit 1
    }
}

$sums = Join-Path $tmp "SHA256SUMS"
if (Test-Path $sums) {
    $line = Get-Content $sums | Where-Object { $_ -match "\s$([regex]::Escape($asset))$" } | Select-Object -First 1
    if ($line) {
        $expected = ($line -split "\s+")[0].ToLower()
        $actual = (Get-FileHash (Join-Path $tmp $asset) -Algorithm SHA256).Hash.ToLower()
        if ($expected -ne $actual) {
            throw "Checksum mismatch for $asset"
        }
    }
}

$dest = Join-Path $BinDir "aircode.exe"
Copy-Item -Force (Join-Path $tmp $asset) $dest
Remove-Item -Recurse -Force $tmp

$userPath = [Environment]::GetEnvironmentVariable("Path", "User")
if (($userPath -split ";") -notcontains $BinDir) {
    $newPath = if ($userPath) { "$userPath;$BinDir" } else { $BinDir }
    [Environment]::SetEnvironmentVariable("Path", $newPath, "User")
    Write-Host "Added $BinDir to your user PATH. Open a new terminal."
}

Write-Host "Installed $dest"
& $dest --version
