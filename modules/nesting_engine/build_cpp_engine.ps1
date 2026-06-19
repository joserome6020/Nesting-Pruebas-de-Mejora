# Compila el motor de nesting C++ (pybind11 + Clipper2) para Python activo.
param(
    [string]$PythonExe = ""
)

function Invoke-Python {
    param([string[]]$PythonArgs)
    if ($PythonExe) {
        & $PythonExe @PythonArgs
    } else {
        & py -3.14 @PythonArgs
    }
    if ($LASTEXITCODE -ne 0) { throw "Python falló: $PythonArgs" }
}

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$CppDir = Join-Path $Root "cpp"
$BuildDir = Join-Path $CppDir "build"

Write-Host "== Arga Nesting C++ Engine ==" -ForegroundColor Cyan
Write-Host "Directorio: $Root"

Invoke-Python @("-m", "pip", "install", "--upgrade", "pip", "pybind11", "cmake")

$pyExePath = (Invoke-Python @("-c", "import sys; print(sys.executable)")).Trim()
$pyScripts = Join-Path (Split-Path -Parent $pyExePath) "Scripts"
$cmakeExe = Join-Path $pyScripts "cmake.exe"
if (-not (Test-Path $cmakeExe)) {
    $cmakeExe = (Get-Command cmake -ErrorAction SilentlyContinue).Source
}
if (-not $cmakeExe -or -not (Test-Path $cmakeExe)) {
    throw "cmake no encontrado. Ejecuta: py -m pip install cmake"
}

$clipperDir = Join-Path $CppDir "third_party\Clipper2"
if (-not (Test-Path $clipperDir)) {
    git clone --depth 1 --branch Clipper2_1.4.0 https://github.com/AngusJohnson/Clipper2.git $clipperDir
}

if (Test-Path $BuildDir) {
    Remove-Item -Recurse -Force $BuildDir
}
New-Item -ItemType Directory -Path $BuildDir | Out-Null

Push-Location $BuildDir
try {
    $pyExe = (Invoke-Python @("-c", "import sys; print(sys.executable)")).Trim()
    if (-not $pyExe) { throw "No se pudo resolver el ejecutable de Python." }

    & $cmakeExe .. -DCMAKE_BUILD_TYPE=Release -DPython_EXECUTABLE="$pyExe"
    & $cmakeExe --build . --config Release -j 4

    $pyd = Get-ChildItem -Recurse -Filter "algorithm_cpp*.pyd" | Select-Object -First 1
    if (-not $pyd) {
        throw "No se generó algorithm_cpp.pyd"
    }

    Copy-Item $pyd.FullName -Destination (Join-Path $Root "algorithm_cpp.pyd") -Force
    Write-Host "OK: $($pyd.Name) -> $Root" -ForegroundColor Green
    $ProjectRoot = Split-Path -Parent (Split-Path -Parent $Root)
    Push-Location $ProjectRoot
    try {
        Invoke-Python @("-c", "from modules.nesting_engine.algorithm_bridge import engine_name; print('Motor activo:', engine_name())")
    } finally {
        Pop-Location
    }
}
finally {
    Pop-Location
}
