# Compila motor PoC C++ v2 (algorithm_cpp_v2.pyd) — aislado de producción.
param(
    [string]$PythonExe = ""
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$CppDir = Join-Path $Root "cpp_v2"
$BuildDir = Join-Path $CppDir "build"
$RepoRoot = Split-Path -Parent (Split-Path -Parent $Root)

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

function Resolve-CmakeExe {
    param([string]$PyExePath)
    $pyBinDir = Split-Path -Parent $PyExePath
    $pyName = Split-Path -Leaf $pyBinDir
    $candidates = @(
        (Join-Path $pyBinDir "cmake.exe"),
        (Join-Path $pyBinDir "Scripts\cmake.exe")
    )
    if ($pyName -ieq "Scripts") {
        $candidates += (Join-Path $pyBinDir "cmake.exe")
    }
    foreach ($cmake in $candidates | Select-Object -Unique) {
        if ($cmake -and (Test-Path -LiteralPath $cmake)) { return $cmake }
    }
    $cmd = Get-Command cmake -ErrorAction SilentlyContinue
    if ($cmd -and (Test-Path -LiteralPath $cmd.Source)) { return $cmd.Source }
    throw "cmake no encontrado. Instala: py -3.14 -m pip install cmake"
}

function Find-VsWherePath {
    $candidates = @(
        "${env:ProgramFiles(x86)}\Microsoft Visual Studio\Installer\vswhere.exe",
        "${env:ProgramFiles}\Microsoft Visual Studio\Installer\vswhere.exe"
    )
    foreach ($path in $candidates) {
        if (Test-Path $path) { return $path }
    }
    return $null
}

function Find-VcToolchain {
    $vswhere = Find-VsWherePath
    if ($vswhere) {
        try {
            $raw = & $vswhere -latest -products * -format json 2>$null
            if ($raw) {
                foreach ($inst in @((ConvertFrom-Json $raw))) {
                    $path = [string]$inst.installationPath
                    if (-not $path) { continue }
                    $vcvars = Join-Path $path "VC\Auxiliary\Build\vcvars64.bat"
                    if (Test-Path -LiteralPath $vcvars) {
                        return [PSCustomObject]@{
                            Path    = $path
                            Version = [string]$inst.installationVersion
                        }
                    }
                }
            }
        } catch { }
    }
    $roots = @(
        "${env:ProgramFiles(x86)}\Microsoft Visual Studio\2022\BuildTools",
        "${env:ProgramFiles}\Microsoft Visual Studio\2022\BuildTools",
        "${env:ProgramFiles(x86)}\Microsoft Visual Studio\2022\Community",
        "${env:ProgramFiles}\Microsoft Visual Studio\2022\Community"
    )
    foreach ($root in $roots) {
        $vcvars = Join-Path $root "VC\Auxiliary\Build\vcvars64.bat"
        if (Test-Path -LiteralPath $vcvars) {
            return [PSCustomObject]@{ Path = $root; Version = "17.0" }
        }
    }
    return $null
}

function Get-CmakeGeneratorForVs {
    param([string]$InstallationVersion)
    $major = 0
    [void][int]::TryParse(($InstallationVersion -split '\.')[0], [ref]$major)
    switch ($major) {
        18 { return "Visual Studio 18 2026" }
        17 { return "Visual Studio 17 2022" }
        16 { return "Visual Studio 16 2019" }
        default { return "Visual Studio 17 2022" }
    }
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

$PyExe = Resolve-PythonExe
$CmakeExe = Resolve-CmakeExe -PyExePath $PyExe

Write-Host "== Arga Nesting C++ v2 PoC ==" -ForegroundColor Cyan
Write-Host "Python: $PyExe"
Write-Host "CPP:    $CppDir"

& $PyExe -m pip install pybind11 cmake -q

# Asegura Clipper2 (compartido con producción / LAB).
$clipperDir = Join-Path $Root "cpp\third_party\Clipper2"
if (-not (Test-Path (Join-Path $clipperDir "CPP\CMakeLists.txt"))) {
    if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
        throw "Clipper2 ausente y git no disponible. Ejecuta build_cpp_engine.ps1 primero."
    }
    New-Item -ItemType Directory -Path (Split-Path $clipperDir) -Force | Out-Null
    git clone --depth 1 --branch Clipper2_1.4.0 https://github.com/AngusJohnson/Clipper2.git $clipperDir
}

if (Test-Path $BuildDir) {
    try {
        Remove-Item -Recurse -Force $BuildDir -ErrorAction Stop
    } catch {
        $stamp = Get-Date -Format "yyyyMMdd_HHmmss"
        $BuildDir = Join-Path $CppDir "build_$stamp"
        Write-Host "[WARN] cpp_v2/build en uso; usando $BuildDir" -ForegroundColor Yellow
    }
}
New-Item -ItemType Directory -Path $BuildDir | Out-Null

$vs = Find-VcToolchain
if (-not $vs) {
    throw @"
No se encontro MSVC. Instala Visual Studio 2022 Build Tools + workload C++.
O compila el motor de produccion primero con build_cpp_engine.ps1 -InstallMsvc.
"@
}
$generator = Get-CmakeGeneratorForVs $vs.Version
Write-Host "VS: $($vs.Path)" -ForegroundColor Cyan
Write-Host "Generator: $generator" -ForegroundColor Cyan
$cudaToolkit = Find-CudaToolkit
if ($cudaToolkit) {
    # CUDA 13 instalado por winget puede no registrar InstallDir para MSBuild.
    # CMake/Visual Studio requieren tanto entorno como toolset explícito.
    $env:CUDA_PATH = $cudaToolkit
    $env:CudaToolkitDir = "$cudaToolkit\"
    Write-Host "CUDA: $cudaToolkit" -ForegroundColor Cyan
}

Push-Location $BuildDir
try {
    $cmakeArgs = @(
        "-S", $CppDir,
        "-B", ".",
        "-G", $generator,
        "-A", "x64",
        "-DPython_EXECUTABLE=$PyExe"
    )
    if ($cudaToolkit) {
        $cmakeArgs += @("-T", "cuda=$cudaToolkit")
    }
    & $CmakeExe @cmakeArgs
    if ($LASTEXITCODE -ne 0) { throw "cmake configure fallo" }
    & $CmakeExe --build . --config Release -j 4
    if ($LASTEXITCODE -ne 0) { throw "cmake build fallo" }

    $pyd = Get-ChildItem -Path . -Recurse -Filter "algorithm_cpp_v2*.pyd" | Select-Object -First 1
    if (-not $pyd) { throw "No se genero algorithm_cpp_v2.pyd" }

    Copy-Item $pyd.FullName -Destination (Join-Path $Root "algorithm_cpp_v2.pyd") -Force
    $tagName = $pyd.Name
    if ($tagName -match '^algorithm_cpp_v2\..+\.pyd$') {
        Copy-Item $pyd.FullName -Destination (Join-Path $Root $tagName) -Force
    } else {
        $pyTag = & $PyExe -c "import sysconfig; print(sysconfig.get_config_var('EXT_SUFFIX') or '')"
        if ($pyTag) {
            Copy-Item $pyd.FullName -Destination (Join-Path $Root ("algorithm_cpp_v2" + $pyTag.Trim())) -Force
        }
    }
    Write-Host "OK -> $(Join-Path $Root 'algorithm_cpp_v2.pyd')" -ForegroundColor Green

    Push-Location $RepoRoot
    try {
        & $PyExe -c "from modules.nesting_engine.algorithm_bridge_v2 import engine_name; print('Motor v2:', engine_name())"
    } finally {
        Pop-Location
    }
} finally {
    Pop-Location
}
