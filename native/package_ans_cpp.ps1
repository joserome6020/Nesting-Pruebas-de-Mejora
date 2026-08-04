# Empaqueta un release portable de ANS C++ (sin firma de código).
# Uso: powershell -ExecutionPolicy Bypass -File native\package_ans_cpp.ps1

param(
    [string]$OutDir = ""
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
if (-not $OutDir) {
    $OutDir = Join-Path $Root "dist\ANS_CPP_portable"
}

Write-Host "Packaging to $OutDir"
if (Test-Path $OutDir) { Remove-Item -Recurse -Force $OutDir }
New-Item -ItemType Directory -Force -Path $OutDir | Out-Null

$copy = @(
    "main.py",
    "api_server.py",
    "config.py",
    "configuracion_nesting.json",
    "README_ANS_CPP.md",
    "AGENT_TRACKING.md",
    "ARCHITECTURE.md",
    "AGENTS.md",
    "docs",
    "modules",
    "interface",
    "api",
    "assets",
    "CAD (OCCT)",
    "native\bin",
    "native\python",
    "tests\native"
)

foreach ($item in $copy) {
    $src = Join-Path $Root $item
    if (-not (Test-Path $src)) { continue }
    $dst = Join-Path $OutDir $item
    $parent = Split-Path $dst -Parent
    New-Item -ItemType Directory -Force -Path $parent | Out-Null
    Copy-Item -Recurse -Force $src $dst
}

$run = @"
@echo off
cd /d "%~dp0"
REM ANS C++ adoption defaults (opt-out: set ARGA_NEST_CORE=0)
if not defined ARGA_NEST_CORE set ARGA_NEST_CORE=1
if not defined ARGA_NEST_CUDA set ARGA_NEST_CUDA=1
python main.py
"@
Set-Content -Path (Join-Path $OutDir "run_ans_cpp.bat") -Value $run -Encoding ASCII

Write-Host "OK portable package ready."
Write-Host ""
Write-Host "Code signing:"
Write-Host "  powershell -ExecutionPolicy Bypass -File native\codesign\Sign-AnsCpp.ps1"
Write-Host "  (crea cert local si no hay; o usa ARGA_SIGN_THUMBPRINT=cert_empresa)"
Write-Host "Ver native\codesign\signed_manifest.json tras firmar."
