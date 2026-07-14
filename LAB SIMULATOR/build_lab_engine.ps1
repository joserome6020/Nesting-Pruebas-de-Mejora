# Compila motor LAB SIMULATOR (algorithm_cpp_lab.pyd)
param(
    [string]$PythonExe = ""
)

$ErrorActionPreference = "Stop"
$LabRoot = $PSScriptRoot
$CppDir = Join-Path $LabRoot "engine\cpp"
$BuildDir = Join-Path $CppDir "build"
$RepoRoot = Split-Path -Parent $LabRoot

function Resolve-PythonExe {
    if ($PythonExe -and (Test-Path -LiteralPath $PythonExe)) {
        return (Resolve-Path -LiteralPath $PythonExe).Path
    }
    $venvPy = Join-Path $RepoRoot ".venv\Scripts\python.exe"
    if (Test-Path -LiteralPath $venvPy) {
        return (Resolve-Path -LiteralPath $venvPy).Path
    }
    $out = & py -3.14 -c "import sys; print(sys.executable)" 2>$null
    if ($LASTEXITCODE -eq 0 -and $out) {
        return ($out | Select-Object -Last 1).ToString().Trim()
    }
    throw "Python no encontrado. Pasa -PythonExe o usa .venv / py -3.14."
}

$PyExe = Resolve-PythonExe

Write-Host "== LAB SIMULATOR engine ==" -ForegroundColor Cyan
Write-Host "Python: $PyExe"
Write-Host "CPP:    $CppDir"

& $PyExe -m pip install pybind11 cmake -q

$CmakeExe = Join-Path (Split-Path $PyExe) "Scripts\cmake.exe"
if (-not (Test-Path $CmakeExe)) {
    $CmakeExe = Join-Path (Split-Path $PyExe) "cmake.exe"
}
if (-not (Test-Path $CmakeExe)) {
    $CmakeExe = "cmake"
}

if (Test-Path $BuildDir) { Remove-Item -Recurse -Force $BuildDir }
New-Item -ItemType Directory -Path $BuildDir | Out-Null

Push-Location $BuildDir
try {
    & $CmakeExe -S $CppDir -B . -G "Visual Studio 17 2022" -A x64
    if ($LASTEXITCODE -ne 0) { throw "cmake configure fallo" }
    & $CmakeExe --build . --config Release
    if ($LASTEXITCODE -ne 0) { throw "cmake build fallo" }

    $pyd = Get-ChildItem -Path . -Recurse -Filter "algorithm_cpp_lab*.pyd" | Select-Object -First 1
    if (-not $pyd) { throw "No se genero algorithm_cpp_lab.pyd" }

    $dest = Join-Path $LabRoot "engine"
    Copy-Item $pyd.FullName -Destination (Join-Path $dest "algorithm_cpp_lab.pyd") -Force
    Write-Host "OK -> $(Join-Path $dest 'algorithm_cpp_lab.pyd')" -ForegroundColor Green
} finally {
    Pop-Location
}
