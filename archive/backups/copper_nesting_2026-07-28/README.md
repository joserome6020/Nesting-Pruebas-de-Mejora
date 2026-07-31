# Respaldo del nesting de cobre — 2026-07-28

Esta es una instantánea restaurable del canal de nesting de cobre en el estado
actual del árbol de trabajo. No interviene ni reemplaza la implementación activa.

## Alcance

`source/` conserva 31 archivos organizados con sus rutas originales:

- motor 1D de barras CU, inventario, RTZCU y el despacho desde `MotorNesting`;
- validación, integridad, eficiencia, numeración y exportación DXF/STEP;
- UI de cálculo, renesteo, edición, canvas y orientación manual;
- workspace, colores, plan de largos/MRL y reporte PDF.

Los archivos compartidos, como `modules/nesting_engine/manager.py`, se
conservaron completos para que también quede registrada la ruta de despacho que
desvía cobre del motor 2D de acero.

## Integridad

`MANIFEST.json` registra el SHA-256 de cada archivo fuente al momento de crear
la instantánea. Para comprobar que el respaldo no cambió:

```powershell
$root = (Get-Location).Path
$backup = Join-Path $root 'archive\backups\copper_nesting_2026-07-28'
(Get-Content (Join-Path $backup 'MANIFEST.json') -Raw | ConvertFrom-Json).files |
  ForEach-Object {
    $copy = Join-Path (Join-Path $backup 'source') $_.path
    if ((Get-FileHash $copy -Algorithm SHA256).Hash -ne $_.sha256) {
      throw "Checksum inválido: $($_.path)"
    }
  }
```

## Restauración

1. Compare primero el archivo activo con `source/<ruta original>`.
2. Restaure selectivamente los módulos de cobre necesarios.
3. Si necesita restaurar `manager.py` o archivos de UI compartidos, revise el
   diff antes de copiarlo porque esos archivos también contienen lógica de acero.

El núcleo del motor está en:
`source/modules/nesting_engine/cu_largos_nesting.py`.
