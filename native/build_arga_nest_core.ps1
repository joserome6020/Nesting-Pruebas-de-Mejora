# Build ArgaNestCore (ANS C++ Fase A)
# Uso:
#   powershell -ExecutionPolicy Bypass -File native\build_arga_nest_core.ps1
#   powershell -ExecutionPolicy Bypass -File native\build_arga_nest_core.ps1 -PythonExe ".\.venv\Scripts\python.exe"

param(
    [string]$PythonExe = "",
    [switch]$Clean
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
if (-not $Root) { $Root = (Resolve-Path "$PSScriptRoot\..").Path }

$CoreDir = Join-Path $Root "native\ArgaNestCore"
$BuildDir = Join-Path $CoreDir "build"
$LegacyCpp = Join-Path $Root "modules\nesting_engine\cpp"
$Clipper = Join-Path $LegacyCpp "third_party\Clipper2\CPP\CMakeLists.txt"

Write-Host "ANS C++ root: $Root"
Write-Host "Core dir:     $CoreDir"

if (-not (Test-Path $Clipper)) {
    Write-Host "Clipper2 ausente. Intentando bootstrap via build_cpp_engine.ps1 ..."
    $LegacyBuild = Join-Path $Root "modules\nesting_engine\build_cpp_engine.ps1"
    if (Test-Path $LegacyBuild) {
        if ($PythonExe) {
            & powershell -ExecutionPolicy Bypass -File $LegacyBuild -PythonExe $PythonExe
        } else {
            & powershell -ExecutionPolicy Bypass -File $LegacyBuild
        }
    } else {
        throw "No Clipper2 y no existe build_cpp_engine.ps1"
    }
}

if (-not $PythonExe) {
    $PythonExe = (Get-Command python -ErrorAction SilentlyContinue | Select-Object -First 1).Source
    if (-not $PythonExe) { $PythonExe = "python" }
}
Write-Host "Python: $PythonExe"

if ($Clean -and (Test-Path $BuildDir)) {
    Remove-Item -Recurse -Force $BuildDir
}
New-Item -ItemType Directory -Force -Path $BuildDir | Out-Null

$vswhere = "${env:ProgramFiles(x86)}\Microsoft Visual Studio\Installer\vswhere.exe"
$Generator = "Ninja"
$ArchArgs = @()
if (Test-Path $vswhere) {
    $vsPath = & $vswhere -latest -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath
    if ($vsPath) {
        $Generator = "Visual Studio 17 2022"
        $ArchArgs = @("-A", "x64")
        # Prefer VS 2026 if present
        $ver = & $vswhere -latest -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property catalog_productLineVersion
        if ($ver -eq "18" -or $ver -eq "2026") {
            $Generator = "Visual Studio 18 2026"
        }
    }
}

function Resolve-CmakeExe {
    $candidates = @(
        "${env:ProgramFiles}\CMake\bin\cmake.exe",
        "${env:ProgramFiles(x86)}\Microsoft Visual Studio\2022\BuildTools\Common7\IDE\CommonExtensions\Microsoft\CMake\CMake\bin\cmake.exe",
        "${env:ProgramFiles(x86)}\Microsoft Visual Studio\2022\Community\Common7\IDE\CommonExtensions\Microsoft\CMake\CMake\bin\cmake.exe",
        "${env:ProgramFiles}\Microsoft Visual Studio\2022\Community\Common7\IDE\CommonExtensions\Microsoft\CMake\CMake\bin\cmake.exe"
    )
    $vswhere = "${env:ProgramFiles(x86)}\Microsoft Visual Studio\Installer\vswhere.exe"
    if (Test-Path $vswhere) {
        $vsPath = & $vswhere -latest -products * -property installationPath 2>$null
        if ($vsPath) {
            $candidates = @(
                (Join-Path $vsPath "Common7\IDE\CommonExtensions\Microsoft\CMake\CMake\bin\cmake.exe")
            ) + $candidates
        }
    }
    $pyDir = Split-Path $PythonExe -Parent
    $candidates += @(
        (Join-Path $pyDir "cmake.exe"),
        (Join-Path $pyDir "Scripts\cmake.exe")
    )
    foreach ($c in $candidates | Select-Object -Unique) {
        if ($c -and (Test-Path -LiteralPath $c)) { return $c }
    }
    $cmd = Get-Command cmake -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    throw "cmake no encontrado. Instala CMake o Visual Studio Build Tools."
}

$CmakeExe = Resolve-CmakeExe
Write-Host "CMake: $CmakeExe"
Write-Host "CMake generator: $Generator"
Push-Location $BuildDir
try {
    $cmakeArgs = @(
        "-G", $Generator
    ) + $ArchArgs + @(
        "-DPython_EXECUTABLE=$PythonExe",
        "-DARGA_NEST_ENABLE_CUDA=OFF",
        $CoreDir
    )
    & $CmakeExe @cmakeArgs
    if ($LASTEXITCODE -ne 0) { throw "cmake configure failed" }

    if ($Generator -like "Visual Studio*") {
        & $CmakeExe --build . --config Release --parallel
    } else {
        & $CmakeExe --build . --parallel
    }
    if ($LASTEXITCODE -ne 0) { throw "cmake build failed" }
}
finally {
    Pop-Location
}

Write-Host ""
Write-Host "OK - verifica con:"
Write-Host "  python tests/native/smoke_arga_nest_core.py"
Write-Host "  python -c import modules.nesting_engine.arga_nest_core_bridge"
