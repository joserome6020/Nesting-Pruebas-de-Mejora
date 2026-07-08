param(
    [Parameter(Mandatory = $true)][int]$ParentPid,
    [Parameter(Mandatory = $true)][string]$ProjectRoot,
    [Parameter(Mandatory = $true)][string]$ExePath,
    [Parameter(Mandatory = $false)][string]$LaunchMode = "exe_standalone",
    [Parameter(Mandatory = $false)][string]$PythonExe = ""
)

$ErrorActionPreference = "Stop"
Set-Location $ProjectRoot

Write-Host "[ARGA-UPDATE] Modo: $LaunchMode"
Write-Host "[ARGA-UPDATE] Esperando cierre de la app (PID $ParentPid)..."
if ($ParentPid -gt 0) {
    try {
        Wait-Process -Id $ParentPid -ErrorAction SilentlyContinue
    } catch {}
}

Write-Host "[ARGA-UPDATE] Compilando ejecutable..."

function Invoke-Build {
    if ($PythonExe -and (Test-Path $PythonExe)) {
        & $PythonExe "tools/build_arga_exe.py" "--skip-deps"
        return $LASTEXITCODE
    }
    $py = (Get-Command python -ErrorAction SilentlyContinue).Source
    if ($py) {
        & $py "tools/build_arga_exe.py" "--skip-deps"
        return $LASTEXITCODE
    }
    if (Get-Command py -ErrorAction SilentlyContinue) {
        & py -3 "tools/build_arga_exe.py" "--skip-deps"
        return $LASTEXITCODE
    }
    throw "Python no encontrado en PATH."
}

$code = Invoke-Build
if ($code -ne 0) { exit $code }

$newExe = Join-Path $ProjectRoot "dist\ArgaNestingSuite.exe"
if (-not (Test-Path $newExe)) {
    throw "No se genero dist\ArgaNestingSuite.exe"
}

$stateDir = Join-Path $env:LOCALAPPDATA "ArgaNestingSuite"
New-Item -ItemType Directory -Force -Path $stateDir | Out-Null
$state = @{
    repo_root   = $ProjectRoot
    active_exe  = $newExe
    launch_mode = $LaunchMode
    updated_utc = (Get-Date).ToUniversalTime().ToString("o")
}
$state | ConvertTo-Json | Set-Content -Path (Join-Path $stateDir "install.json") -Encoding UTF8

$desktop = [Environment]::GetFolderPath("Desktop")
$shortcut = Join-Path $desktop "ARGA NESTING SUITE.lnk"
try {
    $WshShell = New-Object -ComObject WScript.Shell
    $lnk = $WshShell.CreateShortcut($shortcut)
    $lnk.TargetPath = $newExe
    $lnk.WorkingDirectory = Split-Path $newExe
    $lnk.Description = "ARGA NESTING SUITE"
    $lnk.Save()
    Write-Host "[ARGA-UPDATE] Acceso directo actualizado: $shortcut"
} catch {
    Write-Host "[ARGA-UPDATE] WARN: no se pudo crear acceso directo: $_"
}

Write-Host "[ARGA-UPDATE] Iniciando aplicacion actualizada..."
Start-Process -FilePath $newExe -WorkingDirectory (Split-Path $newExe)
exit 0
