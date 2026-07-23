# Compila ArgaIconHandler.dll (x64) con MSVC Build Tools.
param(
    [string]$OutDir = ""
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
if (-not $OutDir) { $OutDir = Join-Path $Root "bin" }
New-Item -ItemType Directory -Force -Path $OutDir | Out-Null

function Find-Vcvars64 {
    $vswhere = "${env:ProgramFiles(x86)}\Microsoft Visual Studio\Installer\vswhere.exe"
    if (-not (Test-Path $vswhere)) { throw "vswhere no encontrado" }
    $inst = & $vswhere -latest -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath
    if (-not $inst) { throw "MSVC x64 tools no instalados" }
    $vcvars = Join-Path $inst "VC\Auxiliary\Build\vcvars64.bat"
    if (-not (Test-Path $vcvars)) { throw "No existe $vcvars" }
    return $vcvars
}

$vcvars = Find-Vcvars64
$dll = Join-Path $OutDir "ArgaIconHandler.dll"
$objDir = Join-Path $OutDir "obj"
New-Item -ItemType Directory -Force -Path $objDir | Out-Null

$srcs = @(
    (Join-Path $Root "ArgaIconHandler.cpp"),
    (Join-Path $Root "classify.cpp"),
    (Join-Path $Root "miniz_tinfl.c")
)
$def = Join-Path $Root "ArgaIconHandler.def"

$clSources = ($srcs | ForEach-Object { "`"$_`"" }) -join " "
$defs = "/DUNICODE /D_UNICODE /DWIN32 /D_WINDOWS /DMINIZ_NO_ARCHIVE_APIS /DMINIZ_NO_ARCHIVE_WRITING_APIS /DMINIZ_NO_DEFLATE_APIS /DMINIZ_NO_STDIO /DMINIZ_NO_TIME"
$cmd = @"
call `"$vcvars`" >nul
cd /d `"$Root`"
cl /nologo /O2 /LD /EHsc /MD $defs /I`"$Root`" /Fo`"$objDir\\`" /Fe`"$dll`" $clSources /link /DEF:`"$def`" shlwapi.lib ole32.lib shell32.lib user32.lib advapi32.lib
"@

$bat = Join-Path $OutDir "_build_tmp.bat"
Set-Content -LiteralPath $bat -Value $cmd -Encoding ASCII
& cmd /c "`"$bat`""
if ($LASTEXITCODE -ne 0) { throw "Compilación ArgaIconHandler falló ($LASTEXITCODE)" }
if (-not (Test-Path -LiteralPath $dll)) { throw "No se generó $dll" }
Write-Output "[OK] $dll"
Get-Item -LiteralPath $dll | Select-Object FullName, Length, LastWriteTime
