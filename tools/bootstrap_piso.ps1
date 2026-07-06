# Ultima configuracion manual en PC de piso (una sola vez).
# Requisitos: Git autenticado con GitHub + Python 3 + MSVC (Visual Studio Build Tools).
# Despues, el .exe se auto-actualiza solo al aceptar en el arranque.

$ErrorActionPreference = "Stop"
$RepoUrl = "https://github.com/joserome6020/Nesting-Pruebas-de-Mejora.git"
$InstallDir = Join-Path $env:LOCALAPPDATA "ArgaNestingSuite"
$RepoDir = Join-Path $InstallDir "repository"

function Require-Command($name) {
    if (-not (Get-Command $name -ErrorAction SilentlyContinue)) {
        throw "Falta '$name' en PATH. Instale Git for Windows y Python 3."
    }
}

Require-Command git
Require-Command python

New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null

if (-not (Test-Path (Join-Path $RepoDir ".git"))) {
    Write-Host "[BOOTSTRAP] Clonando repositorio en $RepoDir ..."
    git clone --branch main --single-branch $RepoUrl $RepoDir
} else {
    Write-Host "[BOOTSTRAP] Repositorio ya existe. git pull..."
    Set-Location $RepoDir
    git pull --ff-only origin main
}

Set-Location $RepoDir
Write-Host "[BOOTSTRAP] Compilando ArgaNestingSuite.exe (puede tardar varios minutos)..."
python tools/build_arga_exe.py --skip-deps

$exe = Join-Path $RepoDir "dist\ArgaNestingSuite.exe"
if (-not (Test-Path $exe)) {
    throw "No se genero $exe"
}

$state = @{
    repo_root  = $RepoDir
    active_exe = $exe
}
$state | ConvertTo-Json | Set-Content (Join-Path $InstallDir "install.json") -Encoding UTF8

$desktop = [Environment]::GetFolderPath("Desktop")
$lnkPath = Join-Path $desktop "ARGA NESTING SUITE.lnk"
$WshShell = New-Object -ComObject WScript.Shell
$lnk = $WshShell.CreateShortcut($lnkPath)
$lnk.TargetPath = $exe
$lnk.WorkingDirectory = Split-Path $exe
$lnk.Description = "ARGA NESTING SUITE"
$lnk.Save()

Write-Host ""
Write-Host "[OK] Listo. Use el acceso directo del escritorio."
Write-Host "     Ejecutable: $exe"
Write-Host "     De ahora en adelante las actualizaciones son automaticas desde la app."
