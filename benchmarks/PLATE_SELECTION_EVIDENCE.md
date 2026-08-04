# Evidencia de selección de placas (ANS)

Protocolo fail-closed para cambios de selección de formato/placa.
Objetivo dual: **mejor acomodo** + **menor impacto de costo**.

## Qué mide

| Métrica | Rol |
|---------|-----|
| `placed_pieces` / `expected_pieces` | Completitud |
| `sheet_count` | Acomodo (menos hojas = mejor) |
| `mean_efi_direct` | Calidad de empaque |
| `cost_total` | Impacto de costo (`Σ precio_placa` sin RTZ) |
| `sim_elapsed_ms_total` | Cuello de botella SIM multi-formato |
| `integrity_ok` | Cero solapes / fallas poka-yoke |
| `oracle_ok` | Casos sintéticos con ganador esperado |

## Flujo obligatorio antes de activar un cambio

1. Congelar baseline del ANS actual (mediana recomendada):
   ```powershell
   py -3.14 -m benchmarks.plate_selection_runner --case ps_synth_cost_trap --runs 3 --write-baseline
   py -3.14 -m benchmarks.plate_selection_runner --case ps_giga_cal11_x1 --runs 3 --write-baseline
   ```
2. Implementar candidata detrás de flag (apagada por defecto).
3. Comparar con la misma cantidad de runs:
   ```powershell
   py -3.14 -m benchmarks.plate_selection_runner --case ps_synth_cost_trap --runs 3 --compare
   py -3.14 -m benchmarks.plate_selection_runner --case ps_giga_cal11_x1 --runs 3 --compare
   ```
4. Solo si `activate_ok=true` en **todos** los gold cases → considerar activación.

Sin `activate_ok`, el cambio **no entra** a producción.

## Gate

Hard FAIL (cualquiera):

- integridad rota
- menos piezas colocadas
- más hojas
- efi directa baja > 0.5 pp
- costo total sube (>0.1% ruido)
- oracle miss (casos con oracle)

Mejora fehaciente (necesaria para activar):

- costo ↓ ≥ 1%, **o**
- tiempo SIM ↓ ≥ 20% sin subir costo ni hojas, **o**
- menos hojas sin subir costo

## Casos gold

- `ps_synth_cost_trap` — reproducible, sin red; trampa de costo; oracle `GOOD_FIT_60x30`.
- `ps_giga_cal11_x1` — corpus real local + `cache/herinox_plates_snapshot.json`.

Añadir WOs: crear JSON en `benchmarks/plate_selection_cases/` (ver los existentes).

## Instrumentación

`modules/nesting_engine/plate_selection_probe.py` + env
`ARGA_PLATE_SEL_PROBE_FILE` (JSONL multi-proceso). Logs
`[SIM-PLACA-SCORE] … elapsed_ms=…` en el motor.

## Hallazgos A/B (2026-07-30) — fehacientes

Caso `ps_synth_many_formats` (varios formatos, mediana de runs):

| Variante | Costo | Hojas | SIM ms | Gate |
|----------|------:|------:|-------:|------|
| Baseline ANS | 505 | 1 | ~37100 | referencia |
| `ARGA_PLATE_SEL_PARALLEL=1` | 505 | 1 | ~119600 | hard_ok, **más lento** |
| `ARGA_PLATE_SEL_FAST=1` | 505 | 1 | ~34100 | hard_ok, ~8% más rápido — **no activa** |

Calidad (costo/acomodo) se preserva. Paralelo ingenuo no se enciende (oversubscription).
Falta estrategia de tiempo más fuerte antes de activar en producción.
Flags quedan **OFF** por defecto.
