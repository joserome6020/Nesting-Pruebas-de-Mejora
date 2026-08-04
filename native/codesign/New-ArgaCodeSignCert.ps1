# Crea certificado Code Signing local "ARGA Nesting Suite" (CurrentUser\My).
# Para producción IT: importar .pfx corporativo y usar -Thumbprint.
# Uso:
#   powershell -ExecutionPolicy Bypass -File native\codesign\New-ArgaCodeSignCert.ps1

param(
    [string]$Subject = "CN=ARGA Nesting Suite Code Signing",
    [int]$Years = 3
)

$ErrorActionPreference = "Stop"

$existing = Get-ChildItem Cert:\CurrentUser\My -CodeSigningCert -ErrorAction SilentlyContinue |
    Where-Object { $_.Subject -eq $Subject -and $_.NotAfter -gt (Get-Date) } |
    Select-Object -First 1

if ($existing) {
    Write-Host "Cert existente:"
    Write-Host "  Subject: $($existing.Subject)"
    Write-Host "  Thumbprint: $($existing.Thumbprint)"
    Write-Host "  NotAfter: $($existing.NotAfter)"
    $existing.Thumbprint | Set-Content -Path (Join-Path $PSScriptRoot "last_thumbprint.txt") -Encoding ascii
    return $existing.Thumbprint
}

Write-Host "Creando certificado auto-firmado Code Signing..."
$cert = New-SelfSignedCertificate `
    -Type CodeSigningCert `
    -Subject $Subject `
    -CertStoreLocation "Cert:\CurrentUser\My" `
    -KeyExportPolicy Exportable `
    -KeySpec Signature `
    -KeyLength 2048 `
    -HashAlgorithm SHA256 `
    -NotAfter (Get-Date).AddYears($Years)

Write-Host "OK"
Write-Host "  Thumbprint: $($cert.Thumbprint)"
Write-Host "  NotAfter: $($cert.NotAfter)"
Write-Host ""
Write-Host "NOTA: Es auto-firmado (valido para firma real local)."
Write-Host "Windows SmartScreen pedira confianza hasta usar cert de CA publica/empresa."

$cert.Thumbprint | Set-Content -Path (Join-Path $PSScriptRoot "last_thumbprint.txt") -Encoding ascii

# Export PFX opcional para backup (password vacia no permitida en algunos hosts)
$pfxOut = Join-Path $PSScriptRoot "arga_codesign_dev.pfx"
$pwd = ConvertTo-SecureString -String "ArgaDevSign!" -Force -AsPlainText
Export-PfxCertificate -Cert $cert -FilePath $pfxOut -Password $pwd | Out-Null
Write-Host "PFX backup: $pfxOut (password: ArgaDevSign!)"

return $cert.Thumbprint
