# ANS C++ — Seguimiento

> Core **0.5.3** · ABI **1.4.0** · Paridad CAM relativa  
> Compact-lite: **ON** (band-close + backfill) · Lite = **MC propio** (no FORCE) · Venom OFF  

> Sesión: [`_logs/SESSION_2026-08-03_AI_DETERMINISTIC.md`](./_logs/SESSION_2026-08-03_AI_DETERMINISTIC.md)

## Mapa rápido (checklist CAM)

| Bloque | Estado |
|--------|--------|
| 1. Geometría (NFP / bbox / Minkowski) | Sí / Sí / Parcial OK planta |
| 2. Heurísticas (BLF / FFD / strip) | Sí / Sí / Sí (CU) |
| 3. Metaheurísticas (GA / SA / Tabu) | Sí / Sí / Sí |
| 4. IA moderna | **L1+L3-lite OK** · L4 aparcado |
| 5. Kerf / veta / common-line / remnants | Sí* / Sí / Sí / Sí |

\* Kerf default `identity`.

## Env clave

- `ARGA_NEST_AI=0|1` (**default 1** · L1 ranker; off con `=0`)
- `ARGA_NEST_VENOM=0|1` (**default 0**)
- `ARGA_NEST_COMPACT=0|1` (**default 1**) · band-close + backfill (Lite sigue en MC)
- STEPS por carpeta: `_config/step_export_folders.json` (Configuración Global)
- Lite: `ARGA_LITE_PASSES=1` · `ARGA_LITE_PLATE_RENEST=1` · tries/mc=1

## Verificar

```powershell
cd "C:\Proyectos\ANS C++"
py -3.14 tests\native\run_regresiones.py   # obligatorio: candados de bugs ya corregidos
py -3.14 tests\native\test_compact_lite.py
py -3.14 tests\native\test_band_close.py
```

`run_regresiones.py` no necesita BD ni core compilado. **Cada bug corregido agrega
su candado ahí**, con el caso real que lo motivó y verificando que falle contra el
código viejo. Un bug sin candado vuelve.

## Siguiente foco

1. Fase 4 Spark: obtener SSH key vía NVIDIA Sync (`arga_dev`) y levantar `nest_remote_server` en `192.168.2.35:8765`
2. Relleno de huecos VFM **barato** en Lite (sin llamar FORCE/base)
3. Telemetría WO → AI UI default (ya hay `result.runtime` en packs)

## Changelog

### 2026-08-06e — Fixtura Amada 2 (28.95\") + elección automática de la más justa

- Catálogo de dos fixturas: `Fixtura 2.DXF` (canal ~28.95\") y la original
  (~35.33\"). Mismo ancho exacto 5\".
- Siempre se elige la del canal más corto que aún acepte el largo de la pieza
  (presión de topes hacia el Punto 0). `GENE-BCU-5-170` (28.87\") → Fixtura 2;
  piezas ~30\"–35.33\" → original.
- Detector de asiento: acepta canales ≥25\" y, dentro de un mismo DXF, prefiere
  el canal más corto (Fixtura 2 tenía exterior 35\" + interior 28.95\").
- Export AMADA/FIXTURA dibuja el DXF de la fixtura elegida.
- Candado: `tests/native/test_amada_fixtura_catalogo.py`.

### 2026-08-06d — Renest Galv incompleto ya no se queda corto vs PARTS

- Síntoma: tras edición manual de placas, el grupo `0.11811_GALVANIZADO` quedaba
  con «faltan N colocaciones» y Guardar poka-yoke bloqueaba el `.arganest`.
  Renestear el calibre no recuperaba las piezas perdidas.
- Causa: el renesteo de calibre usaba solo el conteo del nest abierto; si la
  edición dejó menos piezas en hojas que en `piezas_pool`/PARTS, renest «cerraba»
  un inventário reducido. Además `_merge_resultado_en_mapa` unía hojas pero no
  `piezas_pool` (desync entre lotes plasma/láser).
- Fix: si el grupo ya está incompleto, el renest toma `max(PARTS, nest)` por
  nombre; fusión de grupos concatena `piezas_pool`; al reconstruir geometría/
  pack se prefiere material exacto del grupo (sinónimos solo de respaldo) y se
  estampa la etiqueta del grupo para no “pasar” Galv a A 36.
- Candado: `tests/native/test_renest_galv_incompleto.py`.

### 2026-08-06c — Nombre VSM vs carpeta corporate (GIGABOARD5 ↔ GIGA BOARD 5)


- Síntoma: SWO-005 (misma WO recién exportada) fallaba al descargar con
  «No se encontró archivos .dxf para esta SWO.» La BD sí tenía 948 piezas
  `Pendiente SWO` con `job=GIGA BOARD 5`.
- Causa: en red coexisten `...\GIGA\GIGA BOARD 5` (sin AutoDXF) y
  `...\GIGA\GIGABOARD5` (nombre VSM, con AutoDXF). La descarga SWO y el
  importador de largos resolvían solo el match exacto / la carpeta vacía.
- Fix: equivalencia compacta (ignora espacios/guiones) y preferencia por la
  carpeta que tenga `MODEL CORE FILES/AutoDXF` en `obtener_ruta_real_job` y
  `_buscar_carpeta_job_corporate`. No cambia el flujo VSM ni el job_id en BD.
- Candado: `tests/native/test_job_nombre_vsm.py`.

### 2026-08-06b — Export sin carpeta AutoDXF ya no tumba PQART (GIGA BOARD 5)

- Síntoma: al final del export de `W.O. 5 X3` / job `GIGA BOARD 5` fallaba con
  `PostgreSQL no confirmó el nesting/PQART … estado=autodxf_no_existe` aunque
  DXF/PDF/.arganest ya estaban escritos en la red.
- Causa: ese job no tiene `MODEL CORE FILES\AutoDXF` ni CSV de largos. El soft-fail
  de 2026-08-05a solo cubría `csv_no_encontrado` / `csv_vacio` (carpeta presente
  sin CSV); `autodxf_no_existe` seguía abortando el commit de `reporte_cortes` /
  `pqart_wo`.
- Fix: `ESTADOS_LARGOS_SIN_LISTA` incluye `autodxf_no_existe`; aviso y se continúa
  sin pedido MRL (igual que jobs sin perfiles).
- Candado: `tests/native/test_export_sin_lista_largos.py` (estructura tipo GIGA).

### 2026-08-06a — Mapa comercial de largos ya no omite piezas (480→240)

- Síntoma: en `SWO-003` el pedido MRL estaba bien (10 ANG + 5 SLC, canal negado a
  propósito), pero el mapa/PDF sumaba 62 de 66 piezas de ángulo y 42 de 44 de solera.
- Causa: el nesteo arma tiras de 480\" y el mapa las partía en mitades de 240\"
  tira-por-tira, omitiendo en silencio lo que no cabía en cada mitad. Una tira de
  480\" pierde 1\" de puntas; dos de 240\" pierden 2\", y 13×35\" no caben en 2×239\".
- Fix: `_repartir_piezas_en_barras_comerciales` (FFD) + reparto **global** por
  material en `listar_unidades_mrl_plan`. Las piezas se redistribuyen entre todas
  las barras comerciales del pedido; nunca se omiten. Si el pedido quedara corto
  se abre barra extra en el reparto y se avisa en consola.
- El pedido/PO no cambia: sigue saliendo de `ceil(stock/largo_cat)`.
- Candado: `tests/native/test_largos_mapa_comercial.py` (demuestra que el algoritmo
  viejo pierde piezas y el nuevo conserva las 66/44).
- Regla 10 de `AGENTS.md`: todo bug fix lleva commit local + push remoto a GitHub.

### 2026-08-05b — Largos de SWO se multiplican por WO, no por lote

- Síntoma: `SWO-003` (fusiona `W.O. 3 X11` de `251008-COMP-HI`) mostraba el nesteo de
  largos en X1 — `NESTEO · SWO · LOTE X1` — mientras la WO original sí salía en X11.
- Causa: el multiplicador salía de `lote_k` del nesteo. Una SWO se nestea como **un solo
  lote**, así que `lote_k = 1`. En una WO normal `lote_k` sí es el número de tanques, por
  eso el mismo código acertaba ahí. Las placas nunca fallaron porque vienen contadas
  desde `reporte_cortes`, no recalculadas.
- El multiplicador real de una SWO es la **suma de los factores de sus WO** (X2+X3+…),
  no un escalar del lote: las WO fusionadas pueden traer factores distintos.
- `largos_nesting_service`: `resolver_wos_fuente_swo` (pares job/WO desde `reporte_cortes`,
  fallback `meta_pdf_por_ruta` cuando la SWO aún no se exporta), `factor_demanda_swo`,
  `_filas_desde_bd_para_wo`, `_filas_desde_csv_para_pares` y `_filas_demanda_swo`.
  `_obtener_filas_demanda_lote` desvía a esa ruta cuando el job es SWO; la ruta WO queda igual.
- Prioriza `lista_largos_job` sobre el CSV para empatar con el plan canónico que valida el
  export (`plan_swo_canonico`), que antes descartaba el caché ×1 y ocultaba el bug: la
  compra salía bien pero el modal, el PDF de piso y los switches ‘Pedir’ iban en X1.
- Verificado contra BD: `SWO-003` pasa de 14 a 154 piezas (14 base × 11).
- Equivalencia comprobada: una WO X11 y una SWO de X3+X3+X3+X2 del mismo job dan
  el mismo plan (mismas piezas, mismas barras, mismos cortes por barra).
- **Por qué se escapó**: la expansión correcta por WO (`_obtener_jobs_de_swo` +
  `_expandir_lista_para_wo`) existe en `api/legacy_core.py` desde el commit inicial.
  El precálculo de escritorio (`calcular_planes_largos_nesting`, commit `7df25e1`)
  reimplementó la resolución de demanda por su cuenta con `factor_lote` y nunca
  cubrió el caso SWO. No fue una regresión: fueron dos caminos calculando lo mismo
  distinto, y el modal usaba el nuevo. Regla 9 de `AGENTS.md`.
- Candados: `tests/native/test_largos_swo_factor.py` (8 casos, sin BD) dentro de
  `tests/native/run_regresiones.py`. Verificado que fallan contra el código viejo.

### 2026-08-05a — Job sin lista de largos ya no tumba el export

- Caso real: `251008-COMPARTMENT` no lleva perfiles → AutoDXF sin CSV → `csv_no_encontrado`
  hacía fallar `guardar_nesting_en_postgresql` y abortaba el multi-lote completo.
- `postgres_connector`: `ESTADOS_LARGOS_SIN_LISTA` (`csv_no_encontrado`, `csv_vacio`) es aviso,
  no error (rama WO y rama SWO). Acumulador `obtener_wos_sin_lista_largos()`.
- `_mixin_export`: reinicia avisos por corrida y lista las WO sin largos en el mensaje final.
- Limpieza BD/servidor de W.O. 3/4/5 X2 zombis (commit de `reporte_cortes` previo al fallo).
- `guardar_nesting_en_postgresql`: el `commit` pasó a **después** del import de largos →
  una WO que falla ya no deja filas huérfanas en `reporte_cortes`/`pqart_wo`.
- `REPORTE_SWO` (reporte web / dashboard) ya no lanza `ExportStageError`: queda como
  checkpoint `WARNING` + aviso en consola, reintentable con ‘Reanudar sync’.
- MRL: `_demanda_largos_confirmada_vacia` verifica CSV/`lista_largos_job` antes de
  bloquear. El caché `plan_largos_sin_demanda_por_lote` se pierde al reabrir workspace
  o si el precálculo se corta a medio lote, y eso tumbaba la etapa MRL.
- **Fixes de bug van a los dos proyectos** (`ANS C++` y `New Arga Nesting Suite`);
  regla nueva en `AGENTS.md`.
- `_revertir_wos_persistidas`: si la corrida aborta **antes** de VSM/ContPAQ se borran
  todas las WO ya escritas (`reporte_cortes`, `pqart_wo|swo`, `costos_prorrateo`,
  `material_requerido_ldg`, checkpoints). Después de centralizar no se toca nada:
  ahí la exportación es válida y solo aplica ‘Reanudar sync’.

### 2026-08-04k — VALIDACIÓN ABSOLUTA Spark PASS (6/6)

- Hotfix poly + server reiniciado → `validate_spark_all_engines.py` **PASS**.
- Remoto OK: Lite, Force, Ultra, APEX, Burke, Libnest2d (`runtime=spark`, 3 piezas c/u).
- CUDA APEX disponible en Spark (GB10). Switch UI ANS C++ listo para nestear en producción de prueba.

### 2026-08-04j — Fix poly wire + Spark FULL smoke OK

- Spark build FULL OK: `algorithm_cpp` + CUDA GB10, motores ready, server `:8765`.
- Validación remota falló `'poly'` → `_piece_to_native` ahora acepta `poligonos/rings` sin Shapely.
- Hotfix PC→Spark: copiar `algorithm_bridge.py` + `nest_engine_job.py` y reiniciar server (sin rebuild).

### 2026-08-04i — Spark FULL: todos los motores + APEX CUDA

- `tools/build_spark_full.sh`: ArgaNestCore + `algorithm_cpp` con CUDA (archs 75…120 / override).
- `tools/validate_spark_all_engines.py`: valida Lite/Force/Ultra/APEX/Burke/Libnest2d remoto estricto.
- Package `package_spark_worker.ps1` + docs `NEST_RUNTIME_SPARK.md` actualizados.
- CMake: `ARGA_CUDA_ARCHITECTURES` override + PIC en algorithm_cpp.
- Pendiente en Spark Sync: curl paquete → `./tools/build_spark_full.sh` → nohup server → validate desde PC.

### 2026-08-04h — Cualquier motor vía Spark

- `engine_registry.empaquetar_una_hoja_detalle` enruta Local/Spark/Auto para **Lite/Force/APEX/…** (no solo ArgaNestCore).
- Nuevo cmd TCP `pack_engine` + `nest_engine_job` (serialize). Fallback local si Spark falla / sin motor.
- Nota: en Spark ARM hace falta el mismo `.so` del motor (p.ej. `algorithm_cpp` para Lite); si no está, Auto cae a este PC.

### 2026-08-04g — Switch UI NESTEAR EN SPARK (footer)

- Footer ANS C++: `HerinoxSwitch` **NESTEAR EN SPARK** / **NESTEAR EN ESTE PC** junto al de exportar servidor.
- ON → `prefer=auto` (+ ping); OFF → `prefer=local`. Persiste en `_config/nest_runtime.json`.

### 2026-08-04f — Nest real en Spark ARM OK

- Server `nest_remote_server` en `spark-7e79:8765` con ArgaNestCore **0.5.3 aarch64**.
- Desde PC: ping OK + `pack_sheet` remoto → `placed_count=2`, `runtime=spark`, `certify=True`, ~35 ms executor.
- Uso ANS: Configuración Global → Ejecutar en **Spark** o **Auto**, host `192.168.2.35`, puerto `8765`. Dejar el server corriendo en el Spark.

### 2026-08-04e — Core ARM compiló en Spark; fix import package

- Build aarch64 OK: `arga_nest_core.cpython-312-aarch64-linux-gnu.so` (~1.2 MB).
- `modules/nesting_engine/__init__.py` ya no exige `MotorNesting`/plate_stock (worker Spark).
- Pendiente: smoke `core_status` + `run_nest_remote_server` en `:8765`.

### 2026-08-04d — Paquete Spark listo para build ARM

- `tools/package_spark_worker.ps1` → `_spark_deploy/ans_cpp_spark_worker.tgz` (~0.9 MB fuentes).
- `tools/build_arga_nest_core_linux.sh` (cmake/ninja, CUDA off por default).
- HTTP LAN: `http://192.168.2.52:8766/ans_cpp_spark_worker.tgz` (PC Wi‑Fi).
- Siguiente en Spark: curl → build → `run_nest_remote_server.py :8765`.

### 2026-08-04c — Spark red OK (stub aarch64)

- Host `spark-7e79`: Linux **aarch64**, 20 cores, ~121 GiB RAM, Python 3.12.3.
- Stub TCP `:8765` en Spark → ping desde PC **OK** (`version=spark-ping-stub aarch64`).
- Nest real en Spark sigue pendiente de **build ArgaNestCore para Linux ARM** (el `.pyd` Windows no aplica).

### 2026-08-04b — Nest runtime Local / Spark / Auto (Fases 1–3 OK; 4 bloqueada SSH)

- **F1 contrato** `nest_runtime_contract` v1.0.0 + meta `runtime` en resultados.
- **F2 executor** `nest_executor` + prefs `_config/nest_runtime.json` (gitignored) + UI en Configuración Global (Local/Spark/Auto, host/puerto, Probar).
- **F3 servidor LAN** `nest_remote_server` (TCP JSON-line, mismo protocolo worker). Validado: `tests/native/test_nest_runtime_phases.py` **PASS** (local, auto→fallback, spark remoto localhost, auto→remoto).
- **F4 Spark**: docs `docs/NEST_RUNTIME_SPARK.md` + `tools/spark_ssh_smoke.py`. SSH `arga_dev@192.168.2.35` → Permission denied (falta llave en esta shell; usar NVIDIA Sync). No se guardan credenciales en repo.
- Bridge: `pack_sheet_json` enruta por prefer; `pack_sheet_json_local` evita recursión del server.
- Arranque server: `py -3.14 tools\run_nest_remote_server.py --port 8765`

### 2026-08-04 — APEX CUDA A/B (local GPU)

- Bench `ans_engines_cuda_ab` fuerza `ARGA_APEX_CUDA_OFF` en brazo CPU (APEX ya no “engancha” CUDA por default en el A/B).
- `s2_host_fill` (1×): CPU 11.9 s → CUDA 4.9 s · **speedup ≈ 2.4×** · gate pass · `apex_cuda` on/off OK.
- `r_1000kva_critical` (2×): CPU 14.5 s ↔ CUDA 14.8 s · **~paridad** · calidad igual (23 pcs) · gate pass (sin regresión >10%).
- Conclusión: CUDA en APEX **sí engancha** en esta máquina; ganancia no es automática en WO críticos. Spark sigue siendo paso aparte (worker remoto / port).
- Artefactos: `benchmarks/results_real/apex_cuda_ab_s2.json`, `apex_cuda_ab_r1000.json`.

### 2026-08-03y — Revert Lite→FORCE void-first

- Lite vuelve a **MC propio** (`empaquetar_una_hoja_mc`)
- Se mantiene: pack combinado + band-close + densify best-fit
- No sustituir Lite por `empaquetar_una_hoja_base` (era lento ≈ FORCE)

### 2026-08-03x — Lite void-first (no clonar Venom)

- Causa: `usar_pack_combinado` solo en FORCE → Lite nestaba 3 estructurales y mandaba 60 acc a P2
- Compact backfill corría (`solo_band_close`) pero MC por zona no metía piezas
- Fix: con `ARGA_NEST_COMPACT=1`, Lite también usa pack combinado (est+acc en un MC)
- Log: `[PACK-COMBINADO]`

### 2026-08-03v — Compact-lite (sin Venom full)

- `ARGA_NEST_COMPACT=1` default: band-close + backfill remanente L antes de hoja nueva
- Módulo `compact_lite.py`; wire en commit de placa + batch final
- Venom sigue OFF; opt-out compact: `ARGA_NEST_COMPACT=0`
- Smoke: `tests/native/test_compact_lite.py`

### 2026-08-03u — Venom OFF por default

- Master `ARGA_NEST_VENOM=0` (gate en `apply_smart_polisher`)
- Corridor fill / band close también 0
- Reconectar: `ARGA_NEST_VENOM=1` (+ opcional corridor/band=1)

### 2026-08-03t — L1 ranker ON por default

- `ARGA_NEST_AI=1` en `main.py` (antes 0)
- Opt-out: `ARGA_NEST_AI=0`
- Objetivo: empezar a usar ranking en WO reales y acumular señal vs Eddie/baseline

### 2026-08-03s — Lite: recortar tiempo (1+1 MC)

- Antes: 2 pases + plate_renest ×3 ×(explore→refine) ≈ muchos MC/placa
- Ahora: **1 pase MC** + **1 renest placa** (1 MC, keep-if-better, stop al mejorar)
- Defaults: `ARGA_LITE_PASSES=1` · `RENEST_TRIES=1` · `RENEST_MC=1`
- Más calidad (más lento): `ARGA_LITE_PASSES=2` / `ARGA_LITE_PLATE_RENEST_TRIES=2`

### 2026-08-03r — Plasma: barrenos CIRCLE + unidades DXF

- Compensa también `CIRCLE` en CUT_INNER/OUTER (antes solo polilíneas → Ø igual)
- Offset convertido a unidades del DXF (pulgadas/mm) — evita crecer ~0.32" por error mm→in
- Remarcar ESP. regenera el DXF en `Plasma Compensated/`

### 2026-08-03q — Contorno plasma rojo más visible

- Nesting: piezas `plasma_compensada_manual` con borde rojo cosmético grueso (también al seleccionar)
- PARTS: OUTER del DXF compensado resaltado en rojo `#FF1A1A`

### 2026-08-03p — Plasma PARTS: DXF compensado real (como nest)

- Al marcar ESP.: genera `Processed Files/Plasma Compensated/*.dxf` (OUTER+/INNER−)
- Visor muestra ese DXF (misma geometría que usará el nest)
- Nest parsea el DXF compensado; `ruta` original se conserva para láser 1:1
- Sin doble offset en export (`plasma_fuente_ya_compensada`)

### 2026-08-03o — PARTS: preview visual de compensación plasma

- Al marcar ESP. plasma: contorno rojo compensado + métricas en rosa + label offset
- Antes solo se guardaba el flag (DXF 1:1 en visor → parecía no compensada)
- Offset fino (0.0125\") apenas se ve a zoom fit; las medidas/label lo confirman

### 2026-08-03n — Plasma desde PARTS (ESP.)

- Columna ESP. en acero: marca pieza → valida DXF + buffer → ESTADO=PLASMA
- Nesting: inyecta geom compensada y nestea lote plasma separado (placas solo-plasma)
- Quitado menú COMPENSAR PLASMA (placa) y «Compensar calibre completo»
- Persistencia en `.arganest`: `plasma_compensada_por_ruta`
- Offset unificado vía `compute_plasma_offset_mm` (≤0.75→0.0125″, >0.75→0.250″)
- Smoke: `tests/native/test_plasma_parts_mark.py`

### 2026-08-03m — Tooltips legibles (texto negro)

- `QToolTip`: fondo blanco + texto `#111827` (global + cinta nesting)
- Evita tip invisible: la cinta heredaba color claro sobre tip blanco

### 2026-08-03l — Selección nesting en azul

- Piezas seleccionadas: azul fijo (`NEST_SEL_FILL` `#60A5FA`) — visible frente al gris del material
- Edición libre sigue en morado (`NEST_LIBRE_*`)
- Paletas de material: `sel_*` unificadas al azul de nesting

### 2026-08-03k — Lite: 2 pases + plate renest

- `lite_refine_passes=2` (env `ARGA_LITE_PASSES`)
- Tras MC: **plate_renest = renest placa sola** (solo piezas de la hoja, explore→refine × tries, keep-if-better)
- `lite_plate_renest_tries=3` · `refinar_intentos=1`
- Opt-out: `ARGA_LITE_PLATE_RENEST=0`

### 2026-08-03j — Config: STEPS por carpeta

- Configuración Global → sección STEPS (5 checkboxes)
- Persistencia: `_config/step_export_folders.json`
- Export FreeCAD/OCCT + conteo/auditoría respetan flags
- Default: todas ON (comportamiento previo)

### 2026-08-03i — Cinta «Placa» (sin botón flotante)

- Quitado `HERRAMIENTAS DE PLACA` del canvas
- Acciones en cinta Nesting → apartado **Placa** (junto a Herramientas):
  Renestear, Cambiar, Calibre, Vista, Mudar, Limpiar, Ed. libre, rotaciones

### 2026-08-03h — STEP: OCCT ya no redirige a FreeCAD

- Elegir «OCCT (Arga)» usa `occt_step_export` de verdad
- Antes: si FreeCAD estaba instalado, «Arga Nesting Suite» llamaba generador_verde
- Legacy opcional: `ARGA_EXPORT_3D_OCCT_ALLOW_FREECAD=1`

### 2026-08-03g — Amada fixtura: asset DXF

- Faltaba `FIXTURA AMADA/FICSTURA MEJORADA CORTE BUENO .25IN.DXF` → export caía a contorno simulado
- Asset copiado; resolve OK; draw real → 74 ents FIXTURE
- Warning en consola si vuelve a faltar el DXF

### 2026-08-03f — Venom band close + hosts medianos

- `venom_band_close.py`: desliza hileras/columnas como grupo hasta kerf
- Tras band close: 2ª pasada `fill_host_cavities` + sheet pockets
- Hosts medianos cóncavos (L/muescos ≥12 in²) para meter BKT
- Flag `ARGA_NEST_BAND_CLOSE` (default ON)
- Smoke: `tests/native/test_band_close.py`

### 2026-08-03e — Venom corridor fill (pasillos)

- `fill_sheet_free_pockets` + `_fill_corridor_gaps`
- Hosts WFM/VTN/WFN; flag `ARGA_NEST_CORRIDOR_FILL`

### 2026-08-03d — Remanentes: no PL-CARB en UI

- `ARGA_NEST_REMNANTS_IN_NEST` default **OFF**
- IDs UI `REM-{cal}-{W}x{H}`; material `CARB` → `CARBONO`

### 2026-08-03b — L3-lite bandit

- Bandit + calibrate_by_pack + s5

### 2026-08-03a — L1 host-aware

- Ranker schema 2
