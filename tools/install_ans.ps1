<#
Instalador de primera vez de ARGA NESTING SUITE (por-usuario, sin admin).

Uso:
    powershell -NoProfile -ExecutionPolicy Bypass -File install_ans.ps1
        [-ChannelUrl "https://…/latest.json"]      # canal (default = GitHub)
        [-InstallRoot "C:\Users\<u>\AppData\Local\ArgaNestingSuite"]
        [-CreateShortcuts]                          # accesos directos
        [-Force]                                    # reinstala aunque ya haya versión

Deja este layout:
    <InstallRoot>\app\<version>-<commit>\    (release extraído)
    <InstallRoot>\app\current                (junction -> release actual)
    <InstallRoot>\data\                       (mutables, se crea al primer arranque)
    <InstallRoot>\logs\updater.log
    <InstallRoot>\install.json                (estado)
    <InstallRoot>\updates\                    (descargas)

El .exe corre desde `app\current\`. Los accesos directos apuntan a esa
junction para que los updates (que solo cambian la junction) no rompan
los shortcuts del usuario.
#>

param(
    [string]$ChannelUrl = 'https://github.com/joserome6020/Nesting-Pruebas-de-Mejora/releases/latest/download/latest.json',
    [string]$InstallRoot = (Join-Path $env:LOCALAPPDATA 'ArgaNestingSuite'),
    [switch]$CreateShortcuts,
    [switch]$Force
)

$ErrorActionPreference = 'Stop'
$exeName = 'ARGA NESTING SUITE.exe'
$logDir  = Join-Path $InstallRoot 'logs'
$logPath = Join-Path $logDir 'installer.log'

function Write-Log([string]$msg) {
    try {
        if (-not (Test-Path $logDir)) { New-Item -ItemType Directory -Path $logDir -Force | Out-Null }
        $ts = (Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ')
        Add-Content -Path $logPath -Value "[$ts] $msg" -Encoding UTF8
    } catch {}
    Write-Host $msg
}

function Get-LatestJson([string]$url) {
    Write-Log "Consultando canal: $url"
    if ($url.ToLower().StartsWith('http')) {
        return Invoke-RestMethod -Uri $url -TimeoutSec 30 -Headers @{ 'User-Agent' = 'ArgaNestingSuite-Installer' }
    }
    if (Test-Path $url) {
        return Get-Content -Path $url -Raw -Encoding UTF8 | ConvertFrom-Json
    }
    throw "Canal no accesible: $url"
}

function Get-FileHashSha256([string]$path) {
    return (Get-FileHash -Path $path -Algorithm SHA256).Hash.ToLower()
}

function Download-File([string]$url, [string]$dst) {
    Write-Log "Descargando $url"
    if (Test-Path $dst) { Remove-Item -LiteralPath $dst -Force }
    $tmp = "$dst.part"
    if (Test-Path $tmp) { Remove-Item -LiteralPath $tmp -Force }
    Invoke-WebRequest -Uri $url -OutFile $tmp -UseBasicParsing -TimeoutSec 900 -Headers @{ 'User-Agent' = 'ArgaNestingSuite-Installer' }
    Rename-Item -LiteralPath $tmp -NewName (Split-Path -Leaf $dst)
}

function Extract-Zip([string]$zip, [string]$dst) {
    if (Test-Path $dst) {
        Write-Log "Limpiando carpeta destino: $dst"
        Remove-Item -LiteralPath $dst -Recurse -Force
    }
    New-Item -ItemType Directory -Path $dst -Force | Out-Null
    Write-Log "Extrayendo $zip -> $dst"
    Expand-Archive -Path $zip -DestinationPath $dst -Force
}

function Remove-CurrentLink([string]$link) {
    if (-not (Test-Path $link)) { return }
    $item = Get-Item -LiteralPath $link -Force
    if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) {
        & cmd /c rmdir "`"$link`"" 2>$null | Out-Null
    } else {
        Remove-Item -LiteralPath $link -Recurse -Force
    }
}

function New-Junction([string]$link, [string]$target) {
    Remove-CurrentLink $link
    $out = & cmd /c mklink /J "`"$link`"" "`"$target`"" 2>&1
    if ($LASTEXITCODE -ne 0) { throw "mklink /J falló: $out" }
    Write-Log "Junction creada: $link -> $target"
}

function Save-InstallJson([hashtable]$data, [string]$path) {
    $data['installed_at_utc'] = (Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ')
    ($data | ConvertTo-Json -Depth 6) | Set-Content -Path $path -Encoding UTF8
}

function Create-Shortcut([string]$lnkPath, [string]$target, [string]$workingDir, [string]$iconPath) {
    $shell = New-Object -ComObject WScript.Shell
    $lnk = $shell.CreateShortcut($lnkPath)
    $lnk.TargetPath = $target
    $lnk.WorkingDirectory = $workingDir
    if ($iconPath -and (Test-Path $iconPath)) {
        $lnk.IconLocation = "$iconPath,0"
    }
    $lnk.Description = 'ARGA NESTING SUITE'
    $lnk.Save()
    Write-Log "Acceso directo: $lnkPath"
}

try {
    Write-Log "===== install_ans start ====="
    Write-Log "InstallRoot=$InstallRoot  Channel=$ChannelUrl  Force=$Force"

    if (-not (Test-Path $InstallRoot)) { New-Item -ItemType Directory -Path $InstallRoot -Force | Out-Null }
    $appDir = Join-Path $InstallRoot 'app'
    $updatesDir = Join-Path $InstallRoot 'updates'
    $currentLink = Join-Path $appDir 'current'
    $installJson = Join-Path $InstallRoot 'install.json'
    foreach ($d in @($appDir, $updatesDir)) {
        if (-not (Test-Path $d)) { New-Item -ItemType Directory -Path $d -Force | Out-Null }
    }

    $latest = Get-LatestJson $ChannelUrl
    $version = "$($latest.version)"
    $commitShort = "$($latest.commit_short)"
    if (-not $commitShort) { $commitShort = 'nogit000' }
    $expectedSha = "$($latest.sha256)".ToLower()
    $filename = "$($latest.filename)"
    $downloadUrl = "$($latest.url)"

    if (-not $version -or -not $filename -or -not $downloadUrl -or -not $expectedSha) {
        throw "latest.json incompleto (falta version/filename/url/sha256)."
    }

    $newDirName = "$version-$commitShort"
    $newDir = Join-Path $appDir $newDirName

    if ((Test-Path $newDir) -and -not $Force) {
        Write-Log "Ya existe $newDir (usa -Force para reinstalar)."
        exit 0
    }

    $zipPath = Join-Path $updatesDir $filename
    Download-File $downloadUrl $zipPath
    $actualSha = Get-FileHashSha256 $zipPath
    if ($actualSha -ne $expectedSha) {
        throw "sha256 no coincide. esperado=$expectedSha  actual=$actualSha"
    }
    Write-Log "sha256 verificado."

    Extract-Zip $zipPath $newDir
    Set-Content -Path (Join-Path $newDir '.ok') -Value ("{`"version`": `"$version`", `"installed_by`": `"install_ans.ps1`"}") -Encoding UTF8

    New-Junction $currentLink $newDir

    $data = @{
        installed_version = $newDirName
        installed_dir     = $newDir
        channel_url       = $ChannelUrl
    }
    Save-InstallJson $data $installJson

    if ($CreateShortcuts) {
        $exePath = Join-Path $currentLink $exeName
        if (Test-Path $exePath) {
            $iconPath = Join-Path $currentLink 'arga_app.ico'
            $desktop = [Environment]::GetFolderPath('Desktop')
            if ($desktop) {
                Create-Shortcut (Join-Path $desktop 'ARGA NESTING SUITE.lnk') $exePath $currentLink $iconPath
            }
            $startMenu = Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs'
            if (-not (Test-Path $startMenu)) { New-Item -ItemType Directory -Path $startMenu -Force | Out-Null }
            Create-Shortcut (Join-Path $startMenu 'ARGA NESTING SUITE.lnk') $exePath $currentLink $iconPath
        } else {
            Write-Log "WARN: no se encontró $exePath tras extraer; no se crean shortcuts."
        }
    }

    # Limpieza del zip descargado (release ya extraído).
    try { Remove-Item -LiteralPath $zipPath -Force -ErrorAction SilentlyContinue } catch {}
    Write-Log "===== install_ans done: $newDirName ====="
    Write-Host ""
    Write-Host "OK. Versión $newDirName instalada en $newDir"
    Write-Host "Junction 'current' apunta ahí. Abre desde: $currentLink\$exeName"
    exit 0
}
catch {
    Write-Log "ERROR: $($_.Exception.Message)"
    Write-Error $_
    exit 1
}
