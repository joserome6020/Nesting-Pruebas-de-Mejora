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
py -3.14 tests\native\test_compact_lite.py
py -3.14 tests\native\test_band_close.py
```

## Siguiente foco

1. Relleno de huecos VFM **barato** en Lite (sin llamar FORCE/base)
2. Telemetría WO → AI UI default  
3. L4 sigue aparcado

## Changelog

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
