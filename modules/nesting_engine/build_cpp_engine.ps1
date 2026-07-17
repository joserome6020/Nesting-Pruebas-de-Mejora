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
    $pyBinDir = Split-Path -Parent $PyExePath
    $pyName = Split-Path -Leaf $pyBinDir
    $candidates = @(
        (Join-Path $pyBinDir "cmake.exe")
    )
    if ($pyName -ieq "Scripts") {
        # venv: .venv\Scripts\python.exe → cmake en el mismo Scripts
        $candidates += (Join-Path $pyBinDir "cmake.exe")
    } else {
        # Instalación global: Python314\python.exe → Python314\Scripts\cmake.exe
        $candidates += (Join-Path $pyBinDir "Scripts\cmake.exe")
    }
    foreach ($cmake in $candidates | Select-Object -Unique) {
        if ($cmake -and (Test-Path -LiteralPath $cmake)) { return $cmake }
    }
    try {
        $null = & $PyExePath -m cmake --version 2>$null
        if ($LASTEXITCODE -eq 0) {
            $scriptsDir = (& $PyExePath -c "import sysconfig; print(sysconfig.get_path('scripts'))").Trim()
            $fromScripts = Join-Path $scriptsDir "cmake.exe"
            if (Test-Path -LiteralPath $fromScripts) { return $fromScripts }
        }
    } catch { }
    $cmd = Get-Command cmake -ErrorAction SilentlyContinue
    if ($cmd -and (Test-Path -LiteralPath $cmd.Source)) { return $cmd.Source }
    $scriptsHint = if ($pyName -ieq "Scripts") { $pyBinDir } else { Join-Path $pyBinDir "Scripts" }
    throw @"
cmake no encontrado junto a Python ni en PATH.

Instalado vía pip pero Scripts no está en PATH. Prueba:
  py -3.14 -m pip install --upgrade cmake
  py -3.14 -m cmake --version

O agrega a PATH: $scriptsHint
"@
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
            return [PSCustomObject]@{
                Path    = $root
                Version = "17.0"
            }
        }
    }
    return $null
}

function Get-VisualStudioInstall {
    return Find-VcToolchain
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

function Get-VsSetupExe {
    $setup = "${env:ProgramFiles(x86)}\Microsoft Visual Studio\Installer\setup.exe"
    if (Test-Path -LiteralPath $setup) { return $setup }
    return $null
}

function Test-VsVcToolchainAtPath {
    param([string]$InstallPath)
    if (-not $InstallPath) { return $false }
    return Test-Path -LiteralPath (Join-Path $InstallPath "VC\Auxiliary\Build\vcvars64.bat")
}

function Get-VsBuildToolsInstallPath {
    $defaultPath = "${env:ProgramFiles(x86)}\Microsoft Visual Studio\2022\BuildTools"
    if (Test-VsVcToolchainAtPath $defaultPath) {
        return $defaultPath
    }

    $vswhere = Find-VsWherePath
    if (-not $vswhere) { return $null }
    try {
        $raw = & $vswhere -latest -products * -format json 2>$null
        if (-not $raw) { return $null }
        foreach ($inst in @((ConvertFrom-Json $raw))) {
            $path = [string]$inst.installationPath
            if (-not $path) { continue }
            if (Test-VsVcToolchainAtPath $path) {
                return $path
            }
        }
    } catch { }
    return $null
}

function Get-VsBuildToolsTargetPath {
    $defaultPath = "${env:ProgramFiles(x86)}\Microsoft Visual Studio\2022\BuildTools"
    if (Test-Path -LiteralPath (Join-Path $defaultPath "Common7")) {
        return $defaultPath
    }
    return $defaultPath
}

function Test-VsInstallerRunning {
    return [bool](
        Get-Process -Name "setup", "vs_BuildTools", "vs_installer", "vs_installershell" -ErrorAction SilentlyContinue
    )
}

function Test-VcInstallExitOk {
    param([int]$ExitCode)
    # 0=ok, 3010=reboot pendiente, 1641=reboot iniciado
    return ($ExitCode -eq 0 -or $ExitCode -eq 3010 -or $ExitCode -eq 1641)
}

function Get-VsBuildToolsBootstrapper {
    $cached = Join-Path $env:TEMP "vs_BuildTools_arga.exe"
    if (Test-Path -LiteralPath $cached) { return $cached }
    $url = "https://download.visualstudio.microsoft.com/download/pr/2ae938ff-cbb6-4e4d-990c-7794a7a03745/650517f804f6b1fc7d1e274202ee43e2d027cbdbf9376a8e94c7c5bb32abfd99/vs_BuildTools.exe"
    Write-Host "[INFO] Descargando instalador de Visual Studio Build Tools..." -ForegroundColor Cyan
    Invoke-WebRequest -Uri $url -OutFile $cached -UseBasicParsing
    return $cached
}

function Invoke-BootstrapperWorkload {
    $bootstrapper = Get-VsBuildToolsBootstrapper
    Write-Host "[INFO] Instalando workload C++ con vs_BuildTools.exe (puede tardar 10-30 min)..." -ForegroundColor Yellow
    $proc = Start-Process -FilePath $bootstrapper -ArgumentList @(
        "--wait", "--passive", "--norestart",
        "--add", "Microsoft.VisualStudio.Workload.VCTools",
        "--add", "Microsoft.VisualStudio.Component.VC.Tools.x86.x64",
        "--add", "Microsoft.VisualStudio.Component.Windows11SDK.22621",
        "--includeRecommended"
    ) -Wait -PassThru
    return [int]$proc.ExitCode
}

function Invoke-VcWorkloadInstall {
    param(
        [ValidateSet("auto", "install", "modify")]
        [string]$Mode = "auto"
    )

    $installPath = Get-VsBuildToolsTargetPath
    $toolchainReady = [bool](Get-VsBuildToolsInstallPath)

    $workloadArgs = @(
        "--add", "Microsoft.VisualStudio.Workload.VCTools",
        "--add", "Microsoft.VisualStudio.Component.VC.Tools.x86.x64",
        "--add", "Microsoft.VisualStudio.Component.Windows11SDK.22621",
        "--includeRecommended",
        "--passive", "--norestart", "--wait"
    )

    if ($Mode -eq "auto") {
        $Mode = if ($toolchainReady) { "modify" } else { "install" }
    }

    $setup = Get-VsSetupExe
    if ($setup) {
        function Invoke-SetupWorkload {
            param([string]$SetupMode)
            if ($SetupMode -eq "modify") {
                Write-Host "[INFO] Agregando workload C++ con setup.exe modify (puede tardar 10-30 min)..." -ForegroundColor Yellow
                $setupArgs = @("modify", "--installPath", $installPath) + $workloadArgs
            } else {
                Write-Host "[INFO] Instalando Build Tools + workload C++ con setup.exe install (puede tardar 10-30 min)..." -ForegroundColor Yellow
                $setupArgs = @(
                    "install",
                    "--installPath", $installPath,
                    "--productId", "Microsoft.VisualStudio.Product.BuildTools",
                    "--channelId", "VisualStudio.17.Release"
                ) + $workloadArgs
            }
            $proc = Start-Process -FilePath $setup -ArgumentList $setupArgs -Wait -PassThru
            return [int]$proc.ExitCode
        }

        $exitCode = Invoke-SetupWorkload -SetupMode $Mode
        if ($exitCode -eq 87 -and $Mode -eq "modify") {
            Write-Host "[WARN] setup.exe modify retorno 87; reintentando con install..." -ForegroundColor Yellow
            $exitCode = Invoke-SetupWorkload -SetupMode "install"
        }
        if ($exitCode -eq 87) {
            Write-Host "[WARN] setup.exe retorno 87; usando vs_BuildTools.exe..." -ForegroundColor Yellow
            $exitCode = Invoke-BootstrapperWorkload
        }
        return $exitCode
    }

    return Invoke-BootstrapperWorkload
}

function Wait-ForVcToolchain {
    param(
        [int]$TimeoutMinutes = 45,
        [int]$IdleFailMinutes = 20
    )
    $deadline = (Get-Date).AddMinutes($TimeoutMinutes)
    $idleSince = $null
    $dots = 0
    while ((Get-Date) -lt $deadline) {
        $toolchain = Find-VcToolchain
        if ($toolchain) { return $toolchain }

        $installerRunning = Test-VsInstallerRunning
        $dots = ($dots + 1) % 4
        $suffix = "." * $dots
        if ($installerRunning) {
            $idleSince = $null
            Write-Host "[INFO] Instalador de Visual Studio en ejecucion$($suffix.PadRight(3))" -ForegroundColor DarkYellow
            Start-Sleep -Seconds 20
            continue
        }

        if (-not $idleSince) { $idleSince = Get-Date }
        $idleMinutes = ((Get-Date) - $idleSince).TotalMinutes
        if ($idleMinutes -ge $IdleFailMinutes) {
            Write-Host "[ERROR] Sin instalador activo y MSVC no detectado tras $IdleFailMinutes min." -ForegroundColor Red
            return $null
        }

        Write-Host "[INFO] Esperando componentes MSVC$($suffix.PadRight(3))" -ForegroundColor DarkYellow
        Start-Sleep -Seconds 10
    }
    return $null
}

function Throw-VcInstallFailed {
    param([int]$ExitCode)
    if ($ExitCode -eq 87) {
        throw @"
setup.exe rechazo los parametros de instalacion (codigo 87).

Prueba:
  1) Abre 'Visual Studio Installer' -> Instalar/Modificar Build Tools 2022 -> 'Desarrollo para el escritorio con C++'
  2) Ejecuta PowerShell como administrador y corre: python tools\build_arga_exe.py
"@
    }
    if ($ExitCode -eq 740) {
        throw @"
La instalacion de MSVC requiere permisos de administrador (codigo 740).

Ejecuta PowerShell como administrador y corre:
  python tools\build_arga_exe.py

O abre 'Visual Studio Installer' y agrega 'Desarrollo para el escritorio con C++'.
"@
    }
    throw @"
No se pudo instalar el workload C++ de Visual Studio (codigo $ExitCode).

Prueba:
  1) Abre 'Visual Studio Installer' -> Modificar Build Tools 2022 -> 'Desarrollo para el escritorio con C++'
  2) Ejecuta PowerShell como administrador y corre: python tools\build_arga_exe.py
  3) Reinicia la PC si el instalador lo solicita
"@
}

function Install-MsvcBuildTools {
    $existing = Find-VcToolchain
    if ($existing) {
        Write-Host "[INFO] Toolchain MSVC ya disponible: $($existing.Path)" -ForegroundColor Cyan
        return
    }

    if (-not (Test-CommandInPath "winget") -and -not (Get-VsSetupExe)) {
        throw @"
No hay MSVC ni instalador de Visual Studio. Instala manualmente:
  Visual Studio 2022 Build Tools + workload 'Desktop development with C++'
  https://visualstudio.microsoft.com/visual-cpp-build-tools/
"@
    }

    # winget a veces solo registra el paquete sin instalar el workload C++.
    # Si no hay shell de Build Tools, lo registramos; el workload lo agrega setup.exe.
    if ((Test-CommandInPath "winget") -and -not (Test-VsVcToolchainAtPath (Get-VsBuildToolsTargetPath))) {
        Write-Host "[INFO] Registrando Visual Studio Build Tools con winget..." -ForegroundColor Cyan
        & winget install --id Microsoft.VisualStudio.2022.BuildTools -e `
            --accept-package-agreements --accept-source-agreements `
            --disable-interactivity 2>$null | Out-Null
    }

    $exitCode = Invoke-VcWorkloadInstall -Mode auto
    if (-not (Test-VcInstallExitOk $exitCode)) {
        if ((Test-VsVcToolchainAtPath (Get-VsBuildToolsTargetPath)) -and $exitCode -ne 0) {
            Write-Host "[WARN] setup.exe retorno $exitCode; reintentando con modify..." -ForegroundColor Yellow
            $exitCode = Invoke-VcWorkloadInstall -Mode modify
        }
    }

    if (Test-VcInstallExitOk $exitCode) {
        if ($exitCode -eq 3010 -or $exitCode -eq 1641) {
            Write-Host "[WARN] Instalador solicita reinicio (codigo $exitCode); se esperara MSVC..." -ForegroundColor Yellow
        }
    } elseif (-not (Test-VsInstallerRunning)) {
        Throw-VcInstallFailed -ExitCode $exitCode
    } else {
        Write-Host "[WARN] Instalador retorno codigo $exitCode; se esperara a que termine..." -ForegroundColor Yellow
    }

    $toolchain = Wait-ForVcToolchain -TimeoutMinutes 45 -IdleFailMinutes 20
    if (-not $toolchain) {
        throw @"
No se detecto el compilador C++ tras instalar Build Tools.

Prueba:
  1) Reinicia la PC
  2) Ejecuta de nuevo: python tools\build_arga_exe.py
  3) O abre 'Visual Studio Installer' y agrega 'Desarrollo para el escritorio con C++'
"@
    }
    Write-Host "[OK] Toolchain MSVC listo: $($toolchain.Path)" -ForegroundColor Green
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
            Write-Host "[INFO] Sin MSVC: iniciando instalacion de Build Tools..." -ForegroundColor Yellow
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
    # Python 3.1x carga el tag abi (cp314-win_amd64) antes que algorithm_cpp.pyd.
    $tagName = $pyd.Name
    if ($tagName -match '^algorithm_cpp\..+\.pyd$') {
        Copy-Item $pyd.FullName -Destination (Join-Path $Root $tagName) -Force
    } else {
        $pyTag = & $PythonExe -c "import sysconfig; print(sysconfig.get_config_var('EXT_SUFFIX') or '')"
        if ($pyTag) {
            Copy-Item $pyd.FullName -Destination (Join-Path $Root ("algorithm_cpp" + $pyTag.Trim())) -Force
        }
    }
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
