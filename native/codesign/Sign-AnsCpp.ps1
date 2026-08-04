# Firma Authenticode de artefactos ANS C++ y verifica firmas.
# Uso:
#   powershell -ExecutionPolicy Bypass -File native\codesign\Sign-AnsCpp.ps1
#   powershell -ExecutionPolicy Bypass -File native\codesign\Sign-AnsCpp.ps1 -Thumbprint ABCDEF...
#   $env:ARGA_SIGN_THUMBPRINT = "..."  # cert corporativo

param(
    [string]$Thumbprint = "",
    [string]$TimestampUrl = "http://timestamp.digicert.com"
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
if (-not $Root) { $Root = (Resolve-Path "$PSScriptRoot\..\..").Path }

function Resolve-SignTool {
    $kits = Get-ChildItem "C:\Program Files (x86)\Windows Kits\10\bin\*\x64\signtool.exe" -ErrorAction SilentlyContinue |
        Sort-Object FullName -Descending
    if ($kits) { return $kits[0].FullName }
    throw "signtool.exe no encontrado (instala Windows SDK)."
}

function Resolve-Thumbprint {
    param([string]$Tp)
    if ($Tp) { return ($Tp -replace '\s','').ToUpperInvariant() }
    if ($env:ARGA_SIGN_THUMBPRINT) {
        return ($env:ARGA_SIGN_THUMBPRINT -replace '\s','').ToUpperInvariant()
    }
    $last = Join-Path $PSScriptRoot "last_thumbprint.txt"
    if (Test-Path $last) {
        return ((Get-Content $last -Raw).Trim() -replace '\s','').ToUpperInvariant()
    }
    # Crear cert local si no hay
    & powershell -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot "New-ArgaCodeSignCert.ps1") | Out-Null
    if (Test-Path $last) {
        return ((Get-Content $last -Raw).Trim() -replace '\s','').ToUpperInvariant()
    }
    throw "No hay thumbprint de code-sign."
}

$signTool = Resolve-SignTool
$tp = Resolve-Thumbprint -Tp $Thumbprint
Write-Host "SignTool: $signTool"
Write-Host "Thumbprint: $tp"
Write-Host "Root: $Root"

$targets = @()
$targets += Get-ChildItem (Join-Path $Root "native\bin\ArgaNestWorker.exe") -ErrorAction SilentlyContinue
$targets += Get-ChildItem (Join-Path $Root "modules\nesting_engine\arga_nest_core*.pyd") -ErrorAction SilentlyContinue
$targets += Get-ChildItem (Join-Path $Root "native\ArgaNestCore\build\Release\arga_nest_core*.pyd") -ErrorAction SilentlyContinue
$targets += Get-ChildItem (Join-Path $Root "native\ArgaNestCore\build\Release\ArgaNestWorker.exe") -ErrorAction SilentlyContinue

$targets = $targets | Where-Object { $_ -ne $null } | Sort-Object FullName -Unique
if (-not $targets) { throw "No hay artefactos para firmar. Compila el core primero." }

$signed = @()
foreach ($f in $targets) {
    Write-Host "Signing $($f.FullName) ..."
    & $signTool sign /fd SHA256 /td SHA256 /tr $TimestampUrl /sha1 $tp /v $f.FullName
    if ($LASTEXITCODE -ne 0) {
        # Reintento sin timestamp (red bloqueada)
        Write-Host "Timestamp fallo; reintento sin timestamp..."
        & $signTool sign /fd SHA256 /sha1 $tp /v $f.FullName
        if ($LASTEXITCODE -ne 0) { throw "Fallo firmando $($f.Name)" }
    }
    & $signTool verify /pa /v $f.FullName
    if ($LASTEXITCODE -ne 0) {
        Write-Host "WARN: verify /pa fallo (tipico con auto-firmado). Firma presente:"
        Get-AuthenticodeSignature $f.FullName | Format-List Status, StatusMessage, SignerCertificate
    } else {
        Write-Host "VERIFY OK: $($f.Name)"
    }
    $hash = (Get-FileHash -Algorithm SHA256 $f.FullName).Hash
    $sig = Get-AuthenticodeSignature $f.FullName
    $signed += [pscustomobject]@{
        path = $f.FullName.Replace($Root + '\', '').Replace('\', '/')
        sha256 = $hash
        status = [string]$sig.Status
        signer = if ($sig.SignerCertificate) { $sig.SignerCertificate.Subject } else { "" }
        thumbprint = $tp
    }
}

$manifest = @{
    signed_at = (Get-Date).ToString("o")
    thumbprint = $tp
    timestamp_url = $TimestampUrl
    artifacts = $signed
    note = "Auto-firmado local OK para cadena interna. Sustituir por cert CA/empresa para SmartScreen."
}
$outJson = Join-Path $Root "native\codesign\signed_manifest.json"
$manifest | ConvertTo-Json -Depth 6 | Set-Content -Path $outJson -Encoding utf8
Write-Host "Manifest: $outJson"
Write-Host ("DONE - {0} artefactos firmados." -f $signed.Count)
