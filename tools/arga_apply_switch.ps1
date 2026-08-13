<#
Aplica el swap atómico de versión del ANS después de que el .exe se cierre.

Uso (invocado por modules/app_auto_update.py):
    powershell -NoProfile -ExecutionPolicy Bypass -File arga_apply_switch.ps1
        -ParentPid       <PID del ANS actual>
        -InstallRoot     "C:\Users\<u>\AppData\Local\ArgaNestingSuite"
        -NewVersionDir   "<InstallRoot>\app\2026.08.13-abc12345"

Contrato:
  - Espera a que el PID muera (timeout 30s; luego fuerza kill).
  - Verifica sentinel `.ok` en <NewVersionDir>.
  - Cambia la junction <InstallRoot>\app\current -> <NewVersionDir>.
  - Actualiza install.json: installed_version / installed_dir / limpia pending.
  - Relanza <InstallRoot>\app\current\ArgaNestingSuite.exe.
  - Escribe log en <InstallRoot>\logs\updater.log.
  - Si algo falla, la versión anterior queda intacta (no borra `current`
    hasta tener nuevo destino listo).
#>

param(
    [Parameter(Mandatory = $true)][int]$ParentPid,
    [Parameter(Mandatory = $true)][string]$InstallRoot,
    [Parameter(Mandatory = $true)][string]$NewVersionDir
)

$ErrorActionPreference = 'Stop'
$exeName = 'ARGA NESTING SUITE.exe'
$logDir  = Join-Path $InstallRoot 'logs'
$logPath = Join-Path $logDir 'updater.log'
$installJson = Join-Path $InstallRoot 'install.json'
$appDir  = Join-Path $InstallRoot 'app'
$currentLink = Join-Path $appDir 'current'

function Write-Log([string]$msg) {
    try {
        if (-not (Test-Path $logDir)) { New-Item -ItemType Directory -Path $logDir -Force | Out-Null }
        $ts = (Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ')
        Add-Content -Path $logPath -Value "[$ts] $msg" -Encoding UTF8
    } catch {}
    Write-Host $msg
}

function Update-InstallJson {
    param(
        [string]$InstalledVersion,
        [string]$InstalledDir
    )
    try {
        $data = @{}
        if (Test-Path $installJson) {
            try {
                $data = Get-Content -Path $installJson -Raw -Encoding UTF8 | ConvertFrom-Json -AsHashtable
            } catch {
                $data = @{}
            }
        }
        if ($null -eq $data) { $data = @{} }
        $data['installed_version'] = $InstalledVersion
        $data['installed_dir']     = $InstalledDir
        $data['installed_at_utc']  = (Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ')
        $data.Remove('pending_switch')     | Out-Null
        $data.Remove('pending_switch_dir') | Out-Null
        $data.Remove('pending_since_utc')  | Out-Null
        ($data | ConvertTo-Json -Depth 6) | Set-Content -Path $installJson -Encoding UTF8
        Write-Log "install.json actualizado -> $InstalledVersion"
    } catch {
        Write-Log "WARN: no se pudo escribir install.json: $($_.Exception.Message)"
    }
}

function Remove-CurrentLink {
    if (-not (Test-Path $currentLink)) { return $true }
    try {
        $item = Get-Item -LiteralPath $currentLink -Force
        # Junction / symlink -> quitar con cmd rmdir para no borrar contenido.
        if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) {
            & cmd /c rmdir "`"$currentLink`"" 2>$null | Out-Null
        } else {
            Remove-Item -LiteralPath $currentLink -Recurse -Force
        }
        return -not (Test-Path $currentLink)
    } catch {
        Write-Log "ERROR quitando current: $($_.Exception.Message)"
        return $false
    }
}

function New-CurrentJunction([string]$target) {
    # mklink /J no requiere admin y funciona en el mismo volumen.
    $out = & cmd /c mklink /J "`"$currentLink`"" "`"$target`"" 2>&1
    if ($LASTEXITCODE -ne 0) {
        Write-Log "mklink /J falló: $out"
        return $false
    }
    Write-Log "Junction: current -> $target"
    return $true
}

try {
    Write-Log "----- apply_switch start -----"
    Write-Log "ParentPid=$ParentPid  InstallRoot=$InstallRoot  NewVersionDir=$NewVersionDir"

    if (-not (Test-Path $NewVersionDir)) {
        throw "NewVersionDir no existe: $NewVersionDir"
    }
    $okSentinel = Join-Path $NewVersionDir '.ok'
    if (-not (Test-Path $okSentinel)) {
        throw "Falta sentinel .ok en $NewVersionDir (extracción incompleta)."
    }
    $newExe = Join-Path $NewVersionDir $exeName
    if (-not (Test-Path $newExe)) {
        throw "No existe $newExe en la versión nueva."
    }
    if (-not (Test-Path $appDir)) { New-Item -ItemType Directory -Path $appDir -Force | Out-Null }

    # Esperar cierre del ANS actual.
    $deadline = (Get-Date).AddSeconds(30)
    try {
        $proc = Get-Process -Id $ParentPid -ErrorAction SilentlyContinue
    } catch { $proc = $null }
    while ($proc -and -not $proc.HasExited -and (Get-Date) -lt $deadline) {
        Start-Sleep -Milliseconds 300
        try { $proc.Refresh() } catch {}
    }
    if ($proc -and -not $proc.HasExited) {
        Write-Log "PID $ParentPid no cerró en 30s; forzando kill."
        try { $proc.Kill() } catch {}
        Start-Sleep -Milliseconds 500
    }

    # Swap de junction 'current'.
    if (-not (Remove-CurrentLink)) {
        throw "No se pudo quitar la junction/carpeta 'current' existente."
    }
    if (-not (New-CurrentJunction $NewVersionDir)) {
        throw "No se pudo crear la junction 'current' -> $NewVersionDir"
    }

    $version = Split-Path -Leaf $NewVersionDir
    Update-InstallJson -InstalledVersion $version -InstalledDir $NewVersionDir

    # Best-effort: borra el zip descargado (ya está aplicado).
    $updatesDir = Join-Path $InstallRoot 'updates'
    if (Test-Path $updatesDir) {
        Get-ChildItem -Path $updatesDir -Filter '*.zip' -ErrorAction SilentlyContinue |
            ForEach-Object {
                try { Remove-Item -LiteralPath $_.FullName -Force } catch {}
            }
        Get-ChildItem -Path $updatesDir -Filter '*.zip.part' -ErrorAction SilentlyContinue |
            ForEach-Object {
                try { Remove-Item -LiteralPath $_.FullName -Force } catch {}
            }
    }

    # Relanzar app usando la junction (paths estables en accesos directos).
    $currentExe = Join-Path $currentLink $exeName
    if (-not (Test-Path $currentExe)) {
        # Fallback: exe directo en la carpeta nueva.
        $currentExe = $newExe
    }
    Write-Log "Lanzando $currentExe"
    Start-Process -FilePath $currentExe -WorkingDirectory (Split-Path $currentExe) | Out-Null
    Write-Log "----- apply_switch done -----"
    exit 0
}
catch {
    Write-Log "ERROR: $($_.Exception.Message)"
    # Si el swap falló pero la versión anterior existe, intentar relanzarla.
    if (Test-Path $currentLink) {
        $fallback = Join-Path $currentLink $exeName
        if (Test-Path $fallback) {
            Write-Log "Fallback: relanzando versión anterior $fallback"
            try { Start-Process -FilePath $fallback -WorkingDirectory (Split-Path $fallback) | Out-Null } catch {}
        }
    }
    exit 1
}
