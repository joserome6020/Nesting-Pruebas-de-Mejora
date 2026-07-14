# Instala el .pyd recompilado (cerrar la app antes).
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$src = Join-Path $root "algorithm_cpp.NEEDS_INSTALL.pyd"
if (-not (Test-Path $src)) {
    $cand = Get-ChildItem (Join-Path $root "cpp") -Recurse -Filter "algorithm_cpp*.pyd" |
        Where-Object { $_.FullName -match "Release" } |
        Sort-Object LastWriteTime -Descending |
        Select-Object -First 1
    if (-not $cand) { throw "No hay .pyd nuevo para instalar." }
    $src = $cand.FullName
}
$dst = Join-Path $root "algorithm_cpp.pyd"
Copy-Item $src $dst -Force
Write-Host "Instalado: $dst"
Write-Host "Fuente: $src"
