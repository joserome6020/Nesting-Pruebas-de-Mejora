# Compila el motor de nesting C++ (pybind11 + Clipper2) para Python activo.
param(
    [string]$PythonExe = "",
    [switch]$InstallMsvc
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

function Resolve-PythonExePath {
    if ($PythonExe) {
        return (Resolve-Path -LiteralPath $PythonExe).Path
    }
    $out = & py -3.14 -c "import sys; print(sys.executable)"
    if ($LASTEXITCODE -ne 0) { throw "No se pudo resolver Python con py -3.14" }
    return ($out | Select-Object -Last 1).ToString().Trim()
}

function Resolve-CmakeExe {
    param([string]$PyExePath)
    # En Windows el venv coloca python.exe y cmake.exe en el mismo directorio (Scripts).
    $pyBinDir = Split-Path -Parent $PyExePath
    $candidates = @(
        (Join-Path $pyBinDir "cmake.exe"),
        (Join-Path (Join-Path (Split-Path -Parent $pyBinDir) "Scripts") "cmake.exe")
    )
    foreach ($cmake in $candidates) {
        if ($cmake -and (Test-Path -LiteralPath $cmake)) { return $cmake }
    }
    $cmd = Get-Command cmake -ErrorAction SilentlyContinue
    if ($cmd -and (Test-Path -LiteralPath $cmd.Source)) { return $cmd.Source }
    throw "cmake no encontrado. Ejecuta: python -m pip install cmake"
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

function Get-VisualStudioInstall {
    $vswhere = Find-VsWherePath
    if (-not $vswhere) { return $null }

    $lines = & $vswhere `
        -latest `
        -products * `
        -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 `
        -property installationPath,installationVersion `
        -format value 2>$null
    if (-not $lines -or $lines.Count -lt 2) { return $null }

    return [PSCustomObject]@{
        Path    = [string]$lines[0]
        Version = [string]$lines[1]
    }
}

function Get-CmakeGeneratorForVs {
    param([string]$InstallationVersion)
    $major = 0
    [void][int]::TryParse(($InstallationVersion -split '\.')[0], [ref]$major)
    switch ($major) {
        18 { return "Visual Studio 18 2026" }
        17 { return "Visual Studio 17 2022" }
        16 { return "Visual Studio 16 2019" }
        15 { return "Visual Studio 15 2017" }
        default { return "Visual Studio 17 2022" }
    }
}

function Test-CommandInPath {
    param([string]$Name)
    return [bool](Get-Command $Name -ErrorAction SilentlyContinue)
}

function Import-VcVarsEnvironment {
    param([string]$VsInstallPath)
    $vcvars = Join-Path $VsInstallPath "VC\Auxiliary\Build\vcvars64.bat"
    if (-not (Test-Path $vcvars)) { return $false }

    $envDump = cmd /c "`"$vcvars`" >nul 2>&1 && set"
    foreach ($line in $envDump) {
        if ($line -match '^(?<key>[^=]+)=(?<value>.*)$') {
            Set-Item -Path "Env:$($Matches.key)" -Value $Matches.value
        }
    }
    return Test-CommandInPath "cl"
}

function Install-MsvcBuildTools {
    if (-not (Test-CommandInPath "winget")) {
        throw @"
winget no está disponible. Instala manualmente:
  Visual Studio 2022 Build Tools
  Workload: 'Desarrollo para el escritorio con C++' / 'Desktop development with C++'
Descarga: https://visualstudio.microsoft.com/visual-cpp-build-tools/
"@
    }

    $installed = & winget list --id Microsoft.VisualStudio.2022.BuildTools -e 2>$null
    if ($LASTEXITCODE -eq 0 -and $installed -match "BuildTools") {
        Write-Host "[INFO] Visual Studio 2022 Build Tools ya instalado; verificando componente C++..." -ForegroundColor Cyan
        if (Get-VisualStudioInstall) { return }
    }

    Write-Host "[INFO] Instalando Visual Studio 2022 Build Tools (10-20 min, requiere internet)..." -ForegroundColor Yellow
    & winget install --id Microsoft.VisualStudio.2022.BuildTools -e `
        --accept-package-agreements --accept-source-agreements `
        --override "--wait --passive --add Microsoft.VisualStudio.Workload.VCTools --includeRecommended"
    if ($LASTEXITCODE -ne 0) {
        throw "winget falló instalando Build Tools (código $LASTEXITCODE)."
    }

    Start-Sleep -Seconds 3
    if (-not (Get-VisualStudioInstall)) {
        throw "Build Tools instalado pero no se detectó el componente C++. Reinicia la PC y vuelve a ejecutar el build."
    }
}

function Invoke-CmakeConfigure {
    param(
        [string]$CmakeExe,
        [string]$PyExe,
        [string]$SourceDir
    )

    $vs = Get-VisualStudioInstall
    if ($vs) {
        $generator = Get-CmakeGeneratorForVs $vs.Version
        Write-Host "[INFO] Visual Studio detectado: $($vs.Path)" -ForegroundColor Cyan
        Write-Host "[INFO] Generador CMake: $generator (x64)" -ForegroundColor Cyan
        & $CmakeExe $SourceDir -G $generator -A x64 -DPython_EXECUTABLE="$PyExe"
        if ($LASTEXITCODE -ne 0) { throw "CMake configure falló con generador $generator." }
        return "vs"
    }

    if (Test-CommandInPath "cl" -and Test-CommandInPath "nmake") {
        Write-Host "[INFO] MSVC en PATH; generador NMake Makefiles" -ForegroundColor Cyan
        & $CmakeExe $SourceDir -G "NMake Makefiles" -DCMAKE_BUILD_TYPE=Release -DPython_EXECUTABLE="$PyExe"
        if ($LASTEXITCODE -ne 0) { throw "CMake configure falló con NMake Makefiles." }
        return "nmake"
    }

    return $null
}

function Throw-MsvcMissingHelp {
    throw @"
No se encontró compilador C++ (MSVC) en esta PC.

Opciones:
  1) Instalar Visual Studio 2022 Build Tools con workload 'Desktop development with C++'
  2) Re-ejecutar con -InstallMsvc (instala vía winget si está disponible)
  3) En build_arga_exe.py: python tools\build_arga_exe.py --install-msvc

Descarga manual: https://visualstudio.microsoft.com/visual-cpp-build-tools/
"@
}

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$CppDir = Join-Path $Root "cpp"
$BuildDir = Join-Path $CppDir "build"

Write-Host "== Arga Nesting C++ Engine ==" -ForegroundColor Cyan
Write-Host "Directorio: $Root"

Invoke-Python @("-m", "pip", "install", "--upgrade", "pip", "pybind11", "cmake")

$pyExePath = Resolve-PythonExePath
$cmakeExe = Resolve-CmakeExe -PyExePath $pyExePath

$clipperDir = Join-Path $CppDir "third_party\Clipper2"
if (-not (Test-Path $clipperDir)) {
    if (-not (Test-CommandInPath "git")) {
        throw "git no encontrado. Instálalo para clonar Clipper2 o copia third_party\Clipper2 manualmente."
    }
    git clone --depth 1 --branch Clipper2_1.4.0 https://github.com/AngusJohnson/Clipper2.git $clipperDir
}

if (Test-Path $BuildDir) {
    try {
        Remove-Item -Recurse -Force $BuildDir -ErrorAction Stop
    } catch {
        $stamp = Get-Date -Format "yyyyMMdd_HHmmss"
        $BuildDir = Join-Path $CppDir "build_$stamp"
        Write-Host "[WARN] cpp/build en uso por otro proceso; usando $BuildDir" -ForegroundColor Yellow
    }
}
New-Item -ItemType Directory -Path $BuildDir | Out-Null

Push-Location $BuildDir
try {
    $pyExe = $pyExePath
    if (-not $pyExe) { throw "No se pudo resolver el ejecutable de Python." }

    $configKind = Invoke-CmakeConfigure -CmakeExe $cmakeExe -PyExe $pyExe -SourceDir ".."
    if (-not $configKind) {
        if ($InstallMsvc) {
            Install-MsvcBuildTools
            $configKind = Invoke-CmakeConfigure -CmakeExe $cmakeExe -PyExe $pyExe -SourceDir ".."
        }
    }
    if (-not $configKind) {
        $vs = Get-VisualStudioInstall
        if ($vs -and (Import-VcVarsEnvironment $vs.Path)) {
            $configKind = Invoke-CmakeConfigure -CmakeExe $cmakeExe -PyExe $pyExe -SourceDir ".."
        }
    }
    if (-not $configKind) {
        Throw-MsvcMissingHelp
    }

    if ($configKind -eq "vs") {
        & $cmakeExe --build . --config Release -j 4
    } else {
        & $cmakeExe --build . --config Release
    }
    if ($LASTEXITCODE -ne 0) { throw "CMake build falló." }

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
