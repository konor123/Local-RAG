<#
OSL AI Assistant build automation.

Phases:
  1. Clean previous build artifacts.
  2. Build the PyInstaller single-folder distribution.
  3. Stage Ollama binary.
  4. Stage the LibreOffice MSI (download if missing).
  5. Compile the Inno Setup installer.
  6. Print the final installer path.

Run from the project root:

    powershell -ExecutionPolicy Bypass -File packaging\build.ps1
#>

[CmdletBinding()]
param(
    [switch] $SkipInnoSetup,
    [switch] $KeepPyInstallerCache
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$ProjectRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$Packaging   = $PSScriptRoot
$Deps        = Join-Path $Packaging "deps"
$Dist        = Join-Path $ProjectRoot "dist"
$Build       = Join-Path $ProjectRoot "build"

# ── helpers ────────────────────────────────────────────────────
function Step([string] $msg) {
    Write-Host ""
    Write-Host "==> $msg" -ForegroundColor Cyan
}

function Ok([string] $msg) {
    Write-Host "    [OK] $msg" -ForegroundColor Green
}

function Fail([string] $msg) {
    Write-Host "    [ERROR] $msg" -ForegroundColor Red
    exit 1
}

# ── 1. clean ──────────────────────────────────────────────────
Step "Cleaning previous build artifacts"
foreach ($p in @($Dist, $Build, $Deps)) {
    if (Test-Path $p) {
        Remove-Item -LiteralPath $p -Recurse -Force
        Ok "removed $p"
    }
}
New-Item -ItemType Directory -Path $Deps -Force | Out-Null
New-Item -ItemType Directory -Path (Join-Path $Deps "ollama")     -Force | Out-Null

# ── 2. PyInstaller ────────────────────────────────────────────
Step "Building PyInstaller single-folder distribution"
$spec = Join-Path $Packaging "osl_rag.spec"
if (-not (Test-Path $spec)) { Fail "missing $spec" }

$venvPy = "py"
& $venvPy -3.12 -m PyInstaller $spec
if ($LASTEXITCODE -ne 0) { Fail "PyInstaller failed" }
$builtExe = Join-Path (Join-Path $Dist "OSL_AI_Assistant") "OSL_AI_Assistant.exe"
if (-not (Test-Path $builtExe)) { Fail ("PyInstaller output missing: {0}" -f $builtExe) }
Ok ("built {0}" -f $builtExe)

# ── 3. stage Ollama binary ───────────────────────────────────
Step "Staging Ollama binary"
$ollamaSrc = Join-Path $env:LOCALAPPDATA "Programs\Ollama\ollama.exe"
if (-not (Test-Path $ollamaSrc)) {
    $ollamaSrc = Join-Path $env:ProgramFiles "Ollama\ollama.exe"
}
if (Test-Path $ollamaSrc) {
    Copy-Item -LiteralPath $ollamaSrc -Destination (Join-Path (Join-Path $Deps "ollama") "ollama.exe") -Force
    Ok "copied ollama.exe"

    # Stage CPU Ollama runtime support files (llama-server, DLLs, etc.)
    $ollamaRoot    = Split-Path -Parent $ollamaSrc
    $ollamaLibSrc  = Join-Path $ollamaRoot "lib\ollama"
    $ollamaLibDst  = Join-Path (Join-Path $Deps "ollama") "lib\ollama"
    if (Test-Path $ollamaLibSrc) {
        New-Item -ItemType Directory -Path $ollamaLibDst -Force | Out-Null
        # Copy required top-level runtime files (no GPU/backend subdirs)
        Get-ChildItem -LiteralPath $ollamaLibSrc -File | Where-Object {
            $_.Name -in @("llama-server.exe", "llama-quantize.exe") -or
            $_.Extension -eq ".dll"
        } | Copy-Item -Destination $ollamaLibDst -Force
        if (-not (Test-Path (Join-Path $ollamaLibDst "llama-server.exe"))) {
            Fail "llama-server.exe not found in staged Ollama lib directory"
        }
        Ok "staged CPU Ollama runtime files (llama-server.exe, DLLs) to deps\ollama\lib\ollama"
    } else {
        Write-Host "    [WARN] Older monolithic Ollama layout assumed (no lib\ollama subdirectory)" -ForegroundColor Yellow
    }
} else {
    Fail "Ollama not found. Install from https://ollama.com and re-run."
}

# ── 4. stage LibreOffice MSI ─────────────────────────────────
Step "Staging LibreOffice installer"
$loMsi = Join-Path $Deps "LibreOffice.msi"
$existing = @(
    "C:\Program Files\LibreOffice\msi\LibreOffice*.msi",
    "C:\Program Files (x86)\LibreOffice\msi\LibreOffice*.msi"
) | Get-ChildItem -ErrorAction SilentlyContinue | Select-Object -First 1
if ($existing) {
    Copy-Item -LiteralPath $existing.FullName -Destination $loMsi -Force
    Ok ("copied {0}" -f $existing.FullName)
} elseif (Test-Path $loMsi) {
    Ok "LibreOffice MSI already staged"
} else {
    $loUrl = "https://download.documentfoundation.org/libreoffice/stable/26.2.4/win/x86_64/LibreOffice_26.2.4_Win_x86-64.msi"
    Write-Host "    LibreOffice MSI not found locally. Downloading from $loUrl" -ForegroundColor Yellow
    # Force TLS 1.2 for Windows PowerShell 5.1 compatibility
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    Invoke-WebRequest -Uri $loUrl -OutFile $loMsi
    if (-not (Test-Path $loMsi)) { Fail "LibreOffice MSI download failed" }
    Ok "downloaded LibreOffice MSI"
}

# ── 5. Inno Setup ────────────────────────────────────────────
if ($SkipInnoSetup) {
    Step "Skipping Inno Setup (--SkipInnoSetup)"
} else {
    Step "Cleaning previous Inno Setup outputs"
    $output = Join-Path $Packaging "output"
    if (Test-Path $output) {
        Remove-Item -LiteralPath $output -Recurse -Force
        Ok "removed $output"
    }
    New-Item -ItemType Directory -Path $output -Force | Out-Null

    Step "Compiling Inno Setup installer"
    $iscc = (Get-Command iscc.exe -ErrorAction SilentlyContinue)
    if (-not $iscc) {
        $candidate = "C:\Program Files (x86)\Inno Setup 6\iscc.exe"
        if (Test-Path $candidate) { $iscc = Get-Item $candidate }
    }
    if (-not $iscc) {
        $candidate = "C:\InnoSetupExtracted\ISCC.exe"
        if (Test-Path $candidate) { $iscc = Get-Item $candidate }
    }
    if (-not $iscc) {
        Fail "iscc.exe not found. Install Inno Setup 6 and re-run."
    }
    $iss = Join-Path $Packaging "installer.iss"
    & $iscc $iss
    if ($LASTEXITCODE -ne 0) { Fail "Inno Setup compilation failed" }
}

# ── 6. summary ────────────────────────────────────────────────
Step "Build complete"
if (Test-Path $output) {
    Get-ChildItem $output | ForEach-Object { Ok "$($_.FullName)" }
} else {
    Write-Host "    Run packaging\installer.iss through Inno Setup GUI to produce the installer." -ForegroundColor Yellow
}

if (-not $KeepPyInstallerCache) {
    Step "Cleaning PyInstaller working tree"
    foreach ($p in @($Build)) {
        if (Test-Path $p) { Remove-Item -LiteralPath $p -Recurse -Force; Ok "removed $p" }
    }
}
