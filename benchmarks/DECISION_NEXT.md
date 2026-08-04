# Decisión post-PoC · avance a caché NFP

Fecha: 2026-07-27  
Corpus: `s0_micro`, `s1_single_plate`  
Comparación: `cpp_v2_poc` vs baseline `arga_force`

## Resultados

| Escenario | Motor | placed | efi_directa | solape_ok | kerf_viol | elapsed_ms | vs FORCE |
|-----------|-------|--------|------------|-----------|-----------|------------|----------|
| s0_micro | arga_force | 9/9 | 25.00% | true | 0 | ~225 | baseline |
| s0_micro | svgnest_ultra | 9/9 | 25.00% | true | 0 | ~796 | — |
| s0_micro | **cpp_v2_poc** | 9/9 | 25.00% | true | 0 | **~8** | PASS |
| s1_single_plate | arga_force | 33/33 | 42.81% | true | 0 | ~2780 | baseline |
| s1_single_plate | svgnest_ultra | 33/33 | 42.81% | true | 0 | ~11464 | — |
| s1_single_plate | **cpp_v2_poc** | 33/33 | 42.81% | true | 0 | **~571** | PASS |

Fuentes: `benchmarks/baselines/*.json`, `benchmarks/baselines/_last_poc_vs_force.json`.

## Criterios fail-closed

- Sin solapes metal (`sheet_integrity` + gap audit)
- Sin violaciones de kerf (gap ≥ 0.92 × kerf)
- Colocación completa (`placed == expected`)
- Contrato JSON de métricas completo

El PoC **cumple** todos en S0 y S1.

## Interpretación

1. El puente C++ v2 aislado es viable (round-trip, Clipper2 overlap/NFP, pack BLF simple).
2. En este corpus sintético rectilíneo el PoC iguala eficiencia de FORCE y es claramente más rápido.
3. **No** implica superioridad en piezas reales cóncavas / multi-calibre / cavidades abiertas: `in_holes` del PoC sigue en 0 en S1 mientras Ultra reporta 4.
4. La siguiente hipótesis de valor del plan (**caché NFP L1**) tiene luz verde **solo** como experimento medible en el harness, no como cambio de producción.

## Decisión

**AVANZAR** a Fase 2 (caché NFP L1) dentro de `cpp_v2`, con estas reglas:

1. Llave canónica: hash(geometría A) + hash(geometría B) + ángulos + kerf + versión algoritmo.
2. Medir hit-rate y delta de `elapsed_ms` en S0/S1 y, si hay acceso, un lote real repetido.
3. No registrar el motor en `engine_registry` ni cambiar el default `svgnest_ultra`.
4. Cualquier regresión de `solape_ok` / kerf / placed vs baseline FORCE ⇒ FAIL y no merge a producción.

## Cómo reproducir

```powershell
python -m benchmarks.poc_smoke
python -m benchmarks.runner --all --engines arga_force,svgnest_ultra --write-baselines
python -m benchmarks.runner --all --engine cpp_v2_poc --compare-vs arga_force
```

## Fase 2 completada — caché NFP L1

Implementación en `modules/nesting_engine/cpp_v2/`:

- Caché thread-safe por proceso con capacidad configurable.
- Llave: geometrías A/B canónicas, ángulos, kerf y versión del algoritmo.
- Geometrías preparadas una vez por lote; firmas completas validan una posible
  colisión del hash.
- API de diagnóstico: `reset_nfp_cache`, `nfp_cache_stats` y
  `run_nfp_cache_workload`.

Medición (`python -m benchmarks.nfp_cache_benchmark --all --iterations 3`):

| Escenario | Llamadas NFP | Hit-rate | Speedup lookup | Speedup end-to-end |
|-----------|--------------|----------|----------------|--------------------|
| `s0_micro` | 216 | 89.8% | 18.6× | 12.2× |
| `s1_single_plate` | 3,168 | 98.9% | 22.2× | 18.3× |

La regresión del PoC continúa en PASS contra `arga_force`: colocación completa,
misma eficiencia, cero solapes y cero violaciones de kerf.

## Fase 3 completada — filtro NFP en candidatos

El packer PoC ahora consulta el NFP cacheado después del descarte AABB:

1. Si el punto de colocación cae dentro o sobre el NFP, rechaza el candidato.
2. Si queda fuera, ejecuta la intersección Clipper2 exacta existente.

Así, un NFP que no detecte una colisión no puede habilitar una colocación
inválida. El NFP solo evita booleanas exactas cuando confirma un choque.

Medición de cinco corridas independientes por escenario:

| Escenario | Tiempo mediano | Rango | Hit-rate NFP | Resultado geométrico |
|-----------|----------------|-------|--------------|----------------------|
| `s0_micro` | 2.34 ms | 1.98–2.78 ms | 99.05% | 9/9, sin solape ni kerf inválido |
| `s1_single_plate` | 100.82 ms | 88.72–105.15 ms | 99.98% | 33/33, sin solape ni kerf inválido |

Fuente: `benchmarks/baselines/_last_pack_nfp_filter.json`.

**Límite actual:** el NFP del PoC modela el contorno exterior. Para piezas
cóncavas y barrenos se usa solo como filtro conservador y la intersección
exacta sigue siendo la autoridad. Antes de extender el motor a producción se
requiere un corpus local con DXF reales cóncavos, huecos, remanentes y varios
calibres.

## Calibre 11 GIGA — gate de tiempo y rediseño CUDA

Corpus reproducible: `r_giga_cal11` (242 piezas, placa 120 × 48 in, kerf
0.3 in). El baseline `arga_force` se detuvo de forma explícita: cada una de
las cuatro semillas llevaba aproximadamente 1,050–1,062 s para solo la primera
placa (103 piezas). No es un tiempo aceptable ni un resultado promocionable.

El `LAB SIMULATOR` sí completó el lote con su ruta timeline aislada:

- 242/242 piezas, 7 placas, 32.33 s.
- Eficiencia total 60.58%.
- Cero solapes, cero violaciones de kerf.

CUDA se evaluó por separado. El filtro raster CUDA conectado directamente al
BLF secuencial no mejoró el empaquetado completo, por el coste de transferencia
por cada candidata; por tanto permanece fuera de la ruta normal. Se añadió una
sesión persistente para población de semillas: mantiene la máscara fija en
VRAM y reutiliza sus buffers.

| Trabajo CUDA RTX 4050 | Tiempo mediano | Paridad |
|---|---:|---|
| Lote independiente, 16,188 candidatas | 6.06 ms | base |
| Sesión persistente, mismo lote | 1.64 ms (3.69×) | rechazos idénticos |

Decisión: integrar CUDA únicamente tras generar lotes paralelos de semillas o
candidatas que compartan la misma placa parcial. No habilitarla en `arga_force`
ni en el BLF secuencial actual. El adaptador `lab_pilot_adapter.py` permanece
aislado, sin registro en producción, hasta superar comparativas multi-corrida
de acomodo, geometría y tiempo.

## Promoción rápida — resultados de implementación

El timeline del LAB ahora se compila como `algorithm_cpp_lab_pilot.pyd` dentro
de `modules/nesting_engine`. Se registró como `arga_lab_pilot`, oculto y sin
cambio de default; únicamente `ARGA_SHOW_EXPERIMENTAL_ENGINES=1` lo expone a
selección manual.

Gate Calibre 11, tres corridas (`lab_cal11_pilot_gate_pass.json`):

- 242/242 piezas en 7 placas; eficiencia total 60.58%.
- Cero solapes y cero violaciones de kerf en las tres corridas.
- Mediana 27.56 s y p95 conservador 27.80 s, bajo el límite de 45 s.

La aceleración procede de compactar solo la shortlist de 48 anclas con mejor
score preliminar; su validación final conserva Clipper2 y el kerf exacto.

El piloto en sombra también pasó geometría y colocación completa en S0, S1,
1000 kVA, 2500 kVA x29/x30 y GIGA calibre 11
(`lab_pilot_shadow.json`). Permanece en modo manual/sombra.

CUDA: el cribado persistente de una población de 16 semillas × 2,000
candidatas obtuvo 3.43× (37.38 ms CPU vs 10.89 ms GPU) con paridad exacta de
rechazos. Es una mejora de la etapa de cribado, no una demostración de mejora
end-to-end del nesting completo, por lo que no se habilita en el motor diario.

## API de población (sesión + una llamada)

Para atacar el coste de ir-y-venir se añadió:

1. `RasterSession::set_candidate` — candidata residente en VRAM.
2. `RasterSession::screen_population` — criba N semillas en una sola llamada
   C++ (GIL liberado); solo transfiere offsets/resultados por lote.
3. `modules/nesting_engine/cuda_seed_screener.py` — API Python experimental
   (`screen_seed_population`) para el piloto de semillas.
4. Con `ARGA_CPP_V2_CUDA_RASTER=1`, el packer v2 reutiliza `RasterSession`
   para no re-subir la máscara fija en cada lote BLF (sigue siendo opt-in;
   no es ruta diaria).

Gate fail-closed antes de promoción:

- Paridad exacta de rechazos vs CPU.
- 0 solapes / 0 kerf inválido en nest exacto de supervivientes.
- Speedup de cribado ≥ 1.2× y, idealmente, transfer share < 0.5.
- Reducción medible del tiempo **total** del motor piloto; si no, queda
  solo como acelerador de cribado.

```powershell
python -m benchmarks.cuda_seed_population_benchmark --seeds 16 --candidates-per-seed 2000
```

## CUDA dentro del motor piloto existente

CUDA ya no es solo un cribado aislado de `cpp_v2`: con
`ARGA_LAB_PILOT_CUDA=1` acelera el escaneo raster de `arga_lab_pilot`
(`llenar_una_hoja_ultrafast`). El motor sigue siendo el mismo; Clipper2
valida la shortlist. Default: apagado.

```powershell
$env:ARGA_LAB_PILOT_CUDA=1
python -m benchmarks.lab_pilot_cuda_ab --scenario r_1000kva_critical --repeats 2
```

Medición inicial (RTX 4050, `r_1000kva_critical`): ~1.2× wall-clock con CUDA
activo y mismas piezas colocadas en la primera placa. Sigue experimental;
no es default de producción.

## Prueba ANS · motores + PILOT CUDA

`main.py` activa temporalmente `ARGA_NEST_CUDA_TRIAL=1` para mostrar en el
diálogo de renesteo:

- `ARGA PILOT RÁPIDO` (CPU)
- `ARGA PILOT + CUDA` (mismo flujo + GPU)

Medición vía registry (`ans_engines_cuda_ab`, `r_1000kva_critical`):

| Motor | ms | placas | CUDA |
|---|---:|---:|---|
| arga_lite | 59 | 1 | N/A |
| arga_force | 209 | 2 | N/A |
| burke_blf | 216 | 1 | N/A |
| **arga_lab_pilot_cuda** | **4377** | 3 | sí |
| arga_lab_pilot | 5544 | 3 | no |
| svgnest_ultra | 24215 | 2 | N/A |

PILOT+CUDA ≈ **1.27×** más rápido que PILOT CPU, mismas 23 piezas, 0 solapes.
Ultra/Force aún no tienen acelerador CUDA (solo baseline). Default de
producción sigue siendo `svgnest_ultra`.

ML: se generaron dos instancias etiquetadas iniciales y `ml_go_no_go.py`
dictaminó `defer_ml`: faltan 28 de las 30 instancias diversas requeridas. No
se entrenó ningún modelo ni se habilitó IA para decidir geometría.
