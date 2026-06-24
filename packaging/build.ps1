<#
OSL RAG Internal build automation.

Phases:
  1. Clean previous build artifacts.
  2. Build the PyInstaller single-folder distribution.
  3. Stage Ollama binary and the three required Ollama models.
  4. Stage the primary HuggingFace embedding model.
  5. Stage the LibreOffice MSI (download if missing).
  6. Compile the Inno Setup installer.
  7. Print the final installer path.

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
New-Item -ItemType Directory -Path (Join-Path $Deps "ollama_models") | Out-Null
New-Item -ItemType Directory -Path (Join-Path $Deps "hf_models")    -Force | Out-Null

# ── 2. PyInstaller ────────────────────────────────────────────
Step "Building PyInstaller single-folder distribution"
$spec = Join-Path $Packaging "osl_rag.spec"
if (-not (Test-Path $spec)) { Fail "missing $spec" }

$venvPy = "py"
& $venvPy -3.12 -m PyInstaller $spec
if ($LASTEXITCODE -ne 0) { Fail "PyInstaller failed" }
$builtExe = Join-Path $Dist "native_ui" "native_ui.exe"
if (-not (Test-Path $builtExe)) { Fail "PyInstaller output missing: $builtExe" }
Ok "built $builtExe"

# ── 3. stage Ollama + models ─────────────────────────────────
Step "Staging Ollama binary and required models"
$ollamaSrc = Join-Path $env:LOCALAPPDATA "Programs\Ollama\ollama.exe"
if (-not (Test-Path $ollamaSrc)) {
    $ollamaSrc = Join-Path $env:ProgramFiles "Ollama\ollama.exe"
}
if (Test-Path $ollamaSrc) {
    Copy-Item -LiteralPath $ollamaSrc -Destination (Join-Path $Deps "ollama" "ollama.exe") -Force
    Ok "copied ollama.exe"
} else {
    Fail "Ollama not found. Install from https://ollama.com and re-run."
}

# Pull required models if not present
$requiredModels = @("exaone3.5:2.4b", "qwen3.5:4b", "all-minilm")
foreach ($m in $requiredModels) {
    & ollama list | Out-Null
    $present = & ollama list 2>$null | Select-String ([regex]::Escape($m))
    if (-not $present) {
        Step "Pulling $m (first-time download, may take a while)"
        & ollama pull $m
        if ($LASTEXITCODE -ne 0) { Fail "ollama pull $m failed" }
    } else {
        Ok "$m already installed"
    }
}

$ollamaModelRoot = Join-Path $env:USERPROFILE ".ollama\models"
$dest = Join-Path $Deps "ollama_models"
if (Test-Path $ollamaModelRoot) {
    # Copy only the blobs and manifests (skip download cache)
    foreach ($item in @("blobs", "manifests")) {
        $src = Join-Path $ollamaModelRoot $item
        if (Test-Path $src) {
            Copy-Item -LiteralPath $src -Destination (Join-Path $dest $item) -Recurse -Force
        }
    }
    Ok "copied Ollama model blobs/manifests"
} else {
    Fail "Ollama model dir missing: $ollamaModelRoot"
}

# ── 4. stage HuggingFace model ──────────────────────────────
Step "Staging HuggingFace embedding model"
$hfRoot = Join-Path $env:USERPROFILE ".cache\huggingface\hub"
$hfModelDirs = @("models--dragonkue--multilingual-e5-small-ko")
$hfDest = Join-Path $Deps "hf_models"
foreach ($dir in $hfModelDirs) {
    $src = Join-Path $hfRoot $dir
    if (Test-Path $src) {
        Copy-Item -LiteralPath $src -Destination (Join-Path $hfDest $dir) -Recurse -Force
        Ok "copied $dir"
    } else {
        Fail "HuggingFace model missing: $src"
    }
}

# ── 5. stage LibreOffice MSI ─────────────────────────────────
Step "Staging LibreOffice installer"
$loMsi = Join-Path $Deps "LibreOffice.msi"
$existing = @(
    "C:\Program Files\LibreOffice\msi\LibreOffice*.msi",
    "C:\Program Files (x86)\LibreOffice\msi\LibreOffice*.msi"
) | Get-ChildItem -ErrorAction SilentlyContinue | Select-Object -First 1
if ($existing) {
    Copy-Item -LiteralPath $existing.FullName -Destination $loMsi -Force
    Ok "copied $($existing.FullName)"
} elseif (Test-Path $loMsi) {
    Ok "LibreOffice MSI already staged"
} else {
    $loUrl = "https://download.documentfoundation.org/libreoffice/stable/26.2.4/win/x86_64/LibreOffice_26.2.4_Win_x86-64.msi"
    Write-Host "    LibreOffice MSI not found locally. Download from $loUrl" -ForegroundColor Yellow
    Write-Host "    Re-run this script once LibreOffice.msi is placed at $loMsi" -ForegroundColor Yellow
    Fail "LibreOffice MSI missing"
}

# ── 6. Inno Setup ────────────────────────────────────────────
if ($SkipInnoSetup) {
    Step "Skipping Inno Setup (--SkipInnoSetup)"
} else {
    Step "Compiling Inno Setup installer"
    $iscc = (Get-Command iscc.exe -ErrorAction SilentlyContinue)
    if (-not $iscc) {
        $candidate = "C:\Program Files (x86)\Inno Setup 6\iscc.exe"
        if (Test-Path $candidate) { $iscc = Get-Item $candidate }
    }
    if (-not $iscc) {
        Fail "iscc.exe not found. Install Inno Setup 6 and re-run."
    }
    $iss = Join-Path $Packaging "installer.iss"
    & $iscc $iss
    if ($LASTEXITCODE -ne 0) { Fail "Inno Setup compilation failed" }
}

# ── 7. summary ────────────────────────────────────────────────
Step "Build complete"
$output = Join-Path $Packaging "output"
if (Test-Path $output) {
    Get-ChildItem $output | ForEach-Object { Ok $_.FullName }
} else {
    Write-Host "    Run packaging\installer.iss through Inno Setup GUI to produce the installer." -ForegroundColor Yellow
}

if (-not $KeepPyInstallerCache) {
    Step "Cleaning PyInstaller working tree"
    foreach ($p in @($Build)) {
        if (Test-Path $p) { Remove-Item -LiteralPath $p -Recurse -Force; Ok "removed $p" }
    }
}
