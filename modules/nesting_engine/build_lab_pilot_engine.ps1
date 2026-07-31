# Compila el piloto rápido aislado (algorithm_cpp_lab_pilot.pyd).
param(
    [string]$PythonExe = ""
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$CppDir = Join-Path $Root "cpp_lab_pilot"
$BuildDir = Join-Path $CppDir "build"
$RepoRoot = Split-Path -Parent (Split-Path -Parent $Root)

function Resolve-Python {
    if ($PythonExe -and (Test-Path -LiteralPath $PythonExe)) {
        return (Resolve-Path -LiteralPath $PythonExe).Path
    }
    $candidate = & py -3.14 -c "import sys; print(sys.executable)" 2>$null
    if ($LASTEXITCODE -ne 0 -or -not $candidate) {
        throw "Python 3.14 no encontrado. Pasa -PythonExe."
    }
    return ($candidate | Select-Object -Last 1).ToString().Trim()
}

function Find-CudaToolkit {
    $roots = Get-ChildItem `
        -Path "C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA" `
        -Directory `
        -ErrorAction SilentlyContinue |
        Sort-Object Name -Descending
    foreach ($root in $roots) {
        $nvcc = Join-Path $root.FullName "bin\nvcc.exe"
        if (Test-Path -LiteralPath $nvcc) {
            return $root.FullName
        }
    }
    return $null
}

$PyExe = Resolve-Python
$vswhere = "${env:ProgramFiles(x86)}\Microsoft Visual Studio\Installer\vswhere.exe"
$vs = if (Test-Path -LiteralPath $vswhere) {
    & $vswhere -latest -products * -property installationVersion
} else {
    "17.0"
}
$generator = if ([string]$vs -match "^16\.") { "Visual Studio 16 2019" } else { "Visual Studio 17 2022" }

Write-Host "== ARGA LAB Pilot ==" -ForegroundColor Cyan
Write-Host "Python: $PyExe"
Write-Host "CPP:    $CppDir"
Write-Host "Generator: $generator"

& $PyExe -m pip install pybind11 cmake -q
if ($LASTEXITCODE -ne 0) { throw "No se pudieron preparar dependencias de compilación." }

if (Test-Path -LiteralPath $BuildDir) {
    try {
        Remove-Item -LiteralPath $BuildDir -Recurse -Force -ErrorAction Stop
    } catch {
        $stamp = Get-Date -Format "yyyyMMdd_HHmmss"
        $BuildDir = Join-Path $CppDir "build_$stamp"
        Write-Host "[WARN] cpp_lab_pilot/build en uso; usando $BuildDir" -ForegroundColor Yellow
    }
}
New-Item -ItemType Directory -Path $BuildDir | Out-Null

$cudaToolkit = Find-CudaToolkit
$cmakeArgs = @(
    "-S", $CppDir,
    "-B", $BuildDir,
    "-G", $generator,
    "-A", "x64",
    "-DPython_EXECUTABLE=$PyExe"
)
if ($cudaToolkit) {
    $env:CUDA_PATH = $cudaToolkit
    $env:CudaToolkitDir = "$cudaToolkit\"
    Write-Host "CUDA: $cudaToolkit" -ForegroundColor Cyan
    $cmakeArgs += @("-T", "cuda=$cudaToolkit")
}

& $PyExe -m cmake @cmakeArgs
if ($LASTEXITCODE -ne 0) { throw "cmake configure falló" }
& $PyExe -m cmake --build $BuildDir --config Release -j 4
if ($LASTEXITCODE -ne 0) { throw "cmake build falló" }

$pyd = Get-ChildItem -LiteralPath $BuildDir -Recurse -Filter "algorithm_cpp_lab_pilot*.pyd" |
    Select-Object -First 1
if (-not $pyd) { throw "No se generó algorithm_cpp_lab_pilot.pyd" }
Copy-Item $pyd.FullName -Destination (Join-Path $Root "algorithm_cpp_lab_pilot.pyd") -Force
Copy-Item $pyd.FullName -Destination (Join-Path $Root $pyd.Name) -Force

Push-Location $RepoRoot
try {
    & $PyExe -c "from modules.nesting_engine.lab_pilot_adapter import is_ready; assert is_ready(); print('Motor piloto: listo')"
} finally {
    Pop-Location
}
