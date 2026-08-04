# Benchmarks · nuevo motor de nesting

Harness reproducible para comparar motores de producción y el PoC C++ v2.

## Contrato de métricas

Ver `metrics_contract.py`. Campos clave:

- `scenario`, `engine_id`, `elapsed_ms`
- `placed`, `expected`
- `efi_directa`, `solape_ok`, `kerf_violations`, `min_gap_in`, `in_holes`
- `pass_ok` (fail-closed: solape o kerf inválido ⇒ FAIL)

## Selección de placas (evidencia dual: acomodo + costo)

Harness dedicado en `plate_selection_runner.py` + contrato
`plate_selection_metrics.py`. Objetivo: **no activar** un cambio de selección
sin prueba fehaciente.

Usar el mismo Python que corre ANS (p. ej. 3.14) para que cargue `algorithm_cpp`.

| Caso | Qué prueba |
|------|------------|
| `ps_synth_cost_trap` | Inventario sintético con trampa (placa barata enorme vs formato óptimo). Oracle: `GOOD_FIT_60x30`. |
| `ps_giga_cal11_x1` | Corpus real local cal 11 + snapshot Herinox. Baseline A/B de producción. |

```powershell
# Listar gold cases
py -3.14 -m benchmarks.plate_selection_runner --list

# Congelar baseline del ANS actual (solo si pass_ok)
py -3.14 -m benchmarks.plate_selection_runner --case ps_synth_cost_trap --write-baseline

# Tras un cambio: debe mejorar costo y/o tiempo SIM sin regresar acomodo
py -3.14 -m benchmarks.plate_selection_runner --case ps_synth_cost_trap --compare

# Corpus real (requiere dxf local + cache/herinox_plates_snapshot.json)
py -3.14 -m benchmarks.plate_selection_runner --case ps_giga_cal11_x1 --write-baseline

# Gate unitario (sin nestear)
py -3.14 -c "from benchmarks.test_plate_selection_gate import *; test_rejects_cost_regression(); test_accepts_cost_improvement(); print('ok')"
```

Gate fail-closed (`compare_gate`):

- **Hard FAIL**: integridad, menos piezas, más hojas, efi ↓ >0.5 pp, costo ↑, oracle miss.
- **Activate OK**: hard OK **y** mejora fehaciente (≥1% costo ↓, o ≥20% SIM más rápido sin empeorar costo/hojas, o menos hojas sin subir costo).

Baselines: `benchmarks/baselines/plate_selection/`.
Resultados: `benchmarks/results_real/plate_selection/` (gitignored).

## Corpus

| ID | Nivel | Descripción |
|----|-------|-------------|
| `s0_micro` | S0 | TINY/NARROW/WIDE + host con hueco |
| `s1_single_plate` | S1 | Placa 60×30" con hosts/brackets sintéticos |

Geometría embebida en JSON (sin DXF de red).

### Corpus real local

`corpus_real/` admite dos formatos sin rutas UNC/OneDrive:

- `scenario.json`: snapshot de anillos extraído de una hoja `.arganest`.
- `scenario.nestsim.json` + `dxf/`: copia local de DXF con hashes en
  `manifest.json`, creada por `capture_real_corpus.py`.

`workspace_geometry_snapshot.py` conserva solo geometría de corte y metadatos
de placa. Los DXF/workspaces pesados y los resultados de ejecución permanecen
locales e ignorados por git. Un `manifest.json` con `source_unavailable`
documenta una fuente pendiente sin inventar datos.

## Uso

```powershell
# Listar
python -m benchmarks.runner --list

# Baselines de producción
python -m benchmarks.runner --all --engines arga_force,svgnest_ultra --write-baselines

# PoC v2 vs baseline arga_force
python -m benchmarks.runner --all --engine cpp_v2_poc --compare-vs arga_force

# Smoke geométrico v2
python -m benchmarks.poc_smoke

# Hipótesis de caché NFP L1 (preparación por lote + lookups)
python -m benchmarks.nfp_cache_benchmark --all --iterations 3

# Packer PoC con filtro NFP (mediana de cinco corridas)
python -m benchmarks.pack_nfp_filter_benchmark --all --iterations 5

# Baseline CPU fail-closed de snapshots reales
python -m benchmarks.real_baseline --scenario r_1000kva_critical --runs 5

# A/B de filtro raster conservador (CPU ↔ CUDA cuando el Toolkit está instalado)
python -m benchmarks.cuda_raster_benchmark --repeats 7

# A/B end-to-end del packer; activa el filtro BLF CUDA solo para esta prueba
python -m benchmarks.cuda_ab_benchmark --scenario r_1000kva_critical --repeats 5

# Compilar y validar el piloto rápido aislado de LAB
powershell -ExecutionPolicy Bypass -File modules\nesting_engine\build_lab_pilot_engine.ps1
python -m benchmarks.lab_cal11_compare --scenario r_giga_cal11 --engines lab_pilot --runs 3 --gate-cal11
python -m benchmarks.lab_pilot_shadow

# A/B CUDA dentro del motor piloto (no motor nuevo)
python -m benchmarks.lab_pilot_cuda_ab --scenario r_1000kva_critical --repeats 3
# Activa el acelerador: $env:ARGA_LAB_PILOT_CUDA=1

# A/B de motores del flujo ANS (registry) + PILOT±CUDA
$env:ARGA_NEST_CUDA_TRIAL=1
python -m benchmarks.ans_engines_cuda_ab --scenario r_1000kva_critical --repeats 2

# Etiquetas seed_order devueltas por el motor nativo y gate ML
python -m benchmarks.ml_seed_dataset --scenario r_1000kva_critical --seeds 6
python -m benchmarks.ml_go_no_go
```

## Motores

- Producción: `arga_force`, `svgnest_ultra`, … (vía `sim_lab.run_plate_sim`)
- PoC aislado: `cpp_v2_poc` (vía `algorithm_bridge_v2`, **no** registrado en UI)
- Piloto oculto: `arga_lab_pilot`, empaquetado como `algorithm_cpp_lab_pilot.pyd`.
  Solo se muestra manualmente con `ARGA_SHOW_EXPERIMENTAL_ENGINES=1`; nunca se
  vuelve default de forma automática.

## Caché NFP L1

`cpp_v2` mantiene una caché por proceso con llave canónica:
geometrías A/B normalizadas y cuantizadas a 0.001 mm, ángulos, kerf y versión
del algoritmo. La geometría se prepara una vez por lote para evitar que la
canonización anule la ganancia del caché. Las colisiones de hash se validan
contra la firma geométrica completa antes de reutilizar un NFP.

El packer usa el NFP solo para confirmar choques después del filtro AABB; un
candidato fuera del NFP conserva la intersección Clipper2 exacta. Así el
acelerador no puede autorizar una colisión por un falso negativo.

## Filtro raster CUDA opcional

`cpp_v2/cuda/` implementa un filtro batch de candidatas BLF. La máscara marca
solo celdas totalmente interiores a polígonos ya inflados por kerf: una celda
común prueba una intersección y permite rechazar la candidata. Todo resultado
no rechazado conserva la comprobación exacta de Clipper2; si CUDA no está
compilado o falla, el mismo contrato usa fallback CPU fail-open.

El módulo registra candidatas, rechazos seguros, bytes y tiempos H2D/D2H y de
kernel. Solo una ganancia end-to-end en corpus real puede habilitar integración
en el packer o shadow mode.

La ruta end-to-end se solicita con `ARGA_CPP_V2_CUDA_RASTER=1` (el script A/B
la define automáticamente), se activa únicamente si el runtime encuentra una
GPU CUDA y conserva la ruta CPU anterior si no. El binario incluye `sm_75`,
`sm_86` y `sm_89`; el filtro acelera solo GPUs NVIDIA. En equipos sin NVIDIA,
el build detecta que CUDA no está disponible y conserva el fallback CPU. CUDA
13.3 requiere un driver NVIDIA compatible de la rama 610 o posterior.

`cuda_seed_population_benchmark.py` compara tres rutas: CPU chatty, sesión
CUDA con loop Python, y `screen_population` (una sola llamada nativa con
candidata residente). Mide la etapa de población completa, pero sus
supervivientes siguen pasando por Clipper2 y el nesting exacto. Por ello su
resultado no habilita por sí solo CUDA en el motor diario.

API experimental: `modules.nesting_engine.cuda_seed_screener.screen_seed_population`.
