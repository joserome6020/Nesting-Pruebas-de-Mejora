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

### 2026-08-14n — Buzón de soporte con capturas y canal corporativo

- La barra superior de ANS incorpora **BUZÓN**: abre un formulario para reportar
  errores, seleccionar tipo, describir el caso, adjuntar archivos y capturar la
  ventana actual.
- El reporte envía categoría, descripción, contexto activo (job/pestaña),
  plataforma y adjuntos a un webhook HTTPS de Power Automate; el flujo entrega
  el correo a `jose_rosales@grupoarga.com`.
- No hay credenciales SMTP ni Microsoft 365 dentro del `.exe`. La URL firmada
  se guarda solamente en `_config/buzon_support.json` local o se administra con
  `ARGA_NEST_BUZON_WEBHOOK_URL`.
- Guía de creación del flujo: `docs/BUZON_POWER_AUTOMATE.md`.
- Build: `support_inbox` está en hidden imports, archivos críticos y smoke
  imports. Paridad confirmada con `ANS C++`; prueba headless del diálogo PASS.

### 2026-08-14m — visor pinta el énfasis plasma en piezas Inventor (LINE+ARC)

- Bug reportado por planta con `GENE-BKT-101` (bracket de 3 zonas
  ~9.42" × 3.16"): tras compensar plasma, el visor mostraba la pieza pero
  sin el OUTER rojo ni el label `+X"`; el usuario percibía "el offset se
  perdió". Además el panel decía `AREA NETA 23.00 in²` que era un valor
  cacheado — al abrir la pieza real, el loader devolvía `0.00 in²`.
- Causa: DXF exportados desde Autodesk Inventor (capa
  `IV_OUTER_PROFILE`) traen el perfil como decenas de LINE+ARC sueltos,
  nunca como LWPOLYLINE cerrado. `load_dxf_part` solo poblaba
  `outer_rings` con POLY cerrados o CIRCLE, así que para piezas Inventor
  el listado quedaba vacío y:
  - `emphasize_plasma_outers` no dibujaba el rojo (no tenía anillos).
  - `set_plasma_overlay` no podía hacer buffer para el preview.
  - `area_neta` quedaba en 0 y todo el pipeline de métricas del panel
    dependía de valores cacheados fuera del loader.
- Fix en `interface/qt/dxf_part_loader.py`:
  - Recolectamos las entidades LINE/ARC de `es_outer_layer` y
    `es_inner_layer` durante el barrido inicial.
  - Nuevo helper `_agregar_shapes_desde_line_arc` que usa
    `_group_connected_cut_entities` + `_flatten_entity_group_inches` de
    `plasma_dxf_export` para estibarlas en anillos cerrados (< 0.05" gap).
  - Cada anillo se agrega a `shapes_cerrados` como POLY con el rol
    correcto; el clasificador existente lo mete en outers/inners y de ahí
    a `outer_rings` sin más cambios.
- Verificación con el DXF real (GENE-BKT-101):
  - Antes: `outer_rings=0`, `area_neta=0.00`.
  - Después: `outer_rings=1` (281 vértices), `area_neta=22.99 in²`
    coincidiendo con el 23.00 in² del panel.
- Candado `tests/native/test_visor_line_arc_outer.py`:
  1. Sintetiza un DXF Inventor con LINE+ARC en `IV_OUTER_PROFILE` +
     hueco circular en `IV_INTERIOR_PROFILES` y verifica outer_rings,
     área y bbox.
  2. Si `_tmp/GENE-BKT-101.dxf` existe (repro real), lo evalúa: bbox
     ~9.42×3.16, `area_neta > 20 in²`, 1 solo anillo OUTER.
- Regresiones 22/22 en ambos proyectos.

### 2026-08-14l — ROTAR 90° de pieza plasma no borra la marca del offset

- Bug reportado por planta: se compensaba una pieza (visor muestra OUTER
  rojo + `+0.0074"` en el panel PLASMA) y al rotar 90° "se le quitaba el
  offset". El DXF compensado seguía intacto en disco — lo que se perdía
  era la marca visual: rojo desaparecía y el label volvía a `—`.
- Causa: `renderizar_dxf` es llamado por `rotar_vista_90` para recargar
  el modelo con la nueva orientación; limpiaba la escena y no reaplicaba
  `emphasize_plasma_outers` ni la etiqueta.
- Fix en `interface/qt/visualizer.py`:
  - Nuevos flags `_plasma_emphasis_on` y `_plasma_emphasis_offset_in`.
  - `set_plasma_contour_emphasis` los persiste.
  - `renderizar_dxf` los reaplica al final del render si están activos.
  - Cambio de ruta (nueva pieza) resetea los flags para no arrastrar
    énfasis del anterior.
- Candado `test_visor_plasma_rotacion.py`:
  1. AST checks: firma los invariantes en el código fuente.
  2. Simulación end-to-end sin Qt: instancia dobles de `_cad` y
     `lbl_plasma`, ejecuta emphasis+render+rotar+render y exige que la
     última operación sobre `_cad` sea `emphasize_plasma_outers` y el
     label siga con `+X"`.
- Regresiones 21/21 en ambos proyectos.

### 2026-08-14k — TABLA GAPS DE CORTE: todos los motores respetan la foto de planta

- Auditoría: el default `margin_override = 0.15"` estaba filtrado en varios
  puntos del pipeline (manager `DEFAULT_MARGIN_IN`, `PackSheetRequest`,
  `engine_registry.empaquetar_una_hoja`, `sim_lab.run_plate_sim`, y
  `algorithm_bridge.empaquetar_una_hoja_arga_lite`). Si el caller no pasaba
  `margin_override` explícito (renest de placa, transferencia, sim), el
  motor colocaba a 0.15" del borde en vez de 0.250" de la tabla.
- Se importa `PLATE_TO_PIECE_DEFAULT_IN` (0.250") desde `cut_gaps_table` en
  todos los puntos afectados y se usa como default único. Cada motor
  individual (`arga_apex`, `arga_lite`, `arga_base`, `arga_force`,
  `burke_blf`, `libnest2d`, `svgnest_ultra`, `lab_pilot`) toma el margen
  del `PackSheetRequest`, así que la tabla llega a todos por herencia.
- `manager._procesar_grupo` sigue siendo la fuente de verdad en runtime:
  hace `gaps_for_calibre(req_cal)` por grupo (thread-safe, sin caché), así
  que un cambio en `_config/cut_gaps_table.json` se refleja en la siguiente
  corrida sin reiniciar.
- Nuevo candado `test_tabla_gaps_todos_los_motores.py`:
  - Firma cada default de motor contra `PLATE_TO_PIECE_DEFAULT_IN`.
  - Escribe un JSON alternativo (0.400" placa, kerf Cal 14 = 0.180") y
    verifica que `gaps_for_calibre` lo consuma.
  - End-to-end nativo: coloca 30 piezas con kerf 0.500" y margen 0.500",
    mide bboxes reales del pack C++ y exige que cada pieza respete margen
    de placa y kerf entre vecinos con tolerancia 0.5 mm.
- Regresiones 20/20 en ambos proyectos.

### 2026-08-14j — Motor de offset dual: OCCT alta fidelidad + Clipper2 bulletproof

- Se instala **pyclipr** (bindings de Clipper2, el mismo motor que usa
  FreeCAD en su workbench Path/CAM). Clipper2 opera con aritmética entera y
  join redondo, así que no rechaza perfiles reales por micro-gaps o esquinas
  cerradas —el mismo comportamiento que AutoCAD OFFSET sobre POLYLINE.
- `modules/plasma_offset_clipper.py` nuevo: rasteriza LINE/ARC/(LW)POLYLINE
  (paso adaptativo por sagita), corre `ClipperOffset` con `arcTolerance`
  proporcional al kerf y devuelve el contorno cerrado listo para escribirse
  como LWPOLYLINE. Rechaza sólo cuando el hueco entre entidades es realmente
  grande (> 0.05 in ≈ 1.27 mm), no cintas.
- `modules/plasma_compensator.py::_compensate_dxf_occt_exact` deja de fallar
  cuando OCCT no converge. Cascada: **OCCT primero** (LINE/ARC/CIRCLE
  nativos), **Clipper2 si falla** (polilínea densa cerrada). El backend
  reportado ahora es `"occt"`, `"occt+radius"`, `"clipper2+radius"`, etc.
- `_verificar_dxf_compensado` deja de correr `is_simple` sobre polilíneas de
  Clipper2: la densidad de puntos genera falsas auto-intersecciones por
  ruido flotante. La compuerta bbox + cierre + AreaNeta ya detecta los lazos
  reales. Esto desbloquea el `GENE-DF-10-162` en producción.
- `modules/plasma_dxf_export.py::_offset_closed_profile_inches` reintenta
  con Clipper2 cuando el motor primario devuelve vacío. Cierra el error
  `"plasma: sin contorno exportable desde el nest"` que reventaba el export
  de piezas como `GENE-BKT-101`.
- Cache: `PLASMA_OFFSET_ALGO_VERSION = "offset2d-v5-clipper2-fallback"`
  invalida los DXFs viejos en `Plasma Compensated/`.
- Candados nuevos en `test_plasma_occt_offset.py`:
  `test_clipper2_bulletproof_no_rechaza_esquinas_curvas` (perfil real
  33.37×18.77 in con esquinas R0.60) y
  `test_export_no_falla_sin_contorno_exportable_con_clipper`
  (repro del error del export con contorno rectangular con esquinas).
- Build (`tools/build_arga_exe.py`): `pyclipr` y `plasma_offset_clipper` /
  `plasma_offset2d` / `plasma_dxf_export` añadidos a `HIDDEN_IMPORTS`,
  `SMOKE_IMPORT_MODULES` y `CRITICAL_SUITE_FILES` para que el `.exe` los
  empaquete sin lazy imports rotos.
- Paridad ANS C++ verificada; regresiones 19/19 en ambos proyectos.

### 2026-08-14i — Offset verificado como offset + persistencia del bloqueo + tooltip

- **Validación real del offset:** ahora se comprueba que (a) el DXF escrito
  coincide con el wire que calculó OCCT (Hausdorff) y (b) **todo punto del
  resultado está a `|delta|` del contorno origen**. Eso es lo único que detecta
  bultos, picos y arcos convertidos al complementario; bbox/área no los ven.
  Se exige unión redondeada (offset verdadero); una esquina en punta (mitra,
  `delta·√2`) se rechaza. El muestreo del wire pasó a ser proporcional al
  barrido del arco (un arco de 340° ya no se muestreaba grueso).
- Candado nuevo: barreno con muesca (SWITCH PATCH) → el agujero encoge `|delta|`,
  la muesca engorda `|delta|` y **no se mueve de ángulo**.
- **Bloqueo de orientación con plasma:** el visor pinta el DXF compensado, así
  que el hook guardaba con la ruta de `Plasma Compensated` y la lectura usaba la
  original → se perdía al cambiar de pieza. Nueva
  `clave_orientacion_pieza(ruta, plasma_dxf_por_ruta)`: compensado y original
  comparten clave. Candado con el ciclo guardar-desde-compensado /
  leer-desde-original.
- **Tooltip en panel oscuro:** los widgets con stylesheet propio no heredan el
  `QToolTip` global (fondo claro / letra negra) y quedaban texto oscuro sobre
  fondo oscuro. `TOOLTIP_OSCURO_QSS` en `theme.py` (fondo `#1E293B`, letra
  `#F8FAFC`) aplicado al panel del visor y a la casilla. Candado incluido.
- Nota: la regla de 1" (`>0.75" → 0.250"/lado`) es del exportador legacy; sin cambios.

### 2026-08-14h — Plasma: polilínea cerrada de vuelta y barrenos de 2 vértices

- `SWITCH PATCH 1` fallaba con *"Polilínea sin aristas suficientes"*: un círculo
  dibujado como polilínea cerrada de **2 vértices con bulges** (dos semicírculos)
  es válido; el mínimo de 3 aristas era incorrecto.
- **AREA NETA 0.00** tras compensar: el resultado se escribía como `LINE`/`ARC`
  sueltos y el visor solo suma contornos cerrados (`CIRCLE` o polilínea). Ahora
  si el origen era polilínea cerrada se devuelve polilínea cerrada con bulges
  (curvas exactas, misma topología); la compuerta valida `closed` + simplicidad.
- Cache invalidado (`offset2d-v4-polilinea-cerrada`): los compensados previos
  con entidades sueltas se regeneran.
- Candado: `test_polilinea_cerrada_sobrevive_y_area_no_queda_en_cero` (área
  estilo visor antes/después). Nota: la regla de 1" (`>0.75" → 0.250"/lado`)
  viene del exportador legacy, no se cambió.

### 2026-08-14g — Plasma OFFSET: no más perfiles deforme (AREA 0 / lazos)

- Bug de producción en PARTS: tras ESP. el visor mostraba contorno con
  **casquetes redondos** en esquinas, **AREA NETA 0.00** y perímetro inflado
  (GENE-DF-10-124 y similares). Causa: se ofseteaba una cadena **abierta**
  (micro-huecos de CAD / aristas leídas sin `CumOri`) y se escribía el DXF
  sin validar.
- `plasma_occt_offset`: lee vértices con orientación acumulada; puentea
  micro-gaps ≤ 0.02"; rechaza huecos mayores; convierte ARC al arco real
  (no el complementario); valida cierre / simplicidad / crecimiento de
  `|delta|` por lado antes de devolver.
- `plasma_compensator`: compuerta fail-closed que **relee el DXF escrito**,
  comprueba bbox + cadena cerrada + no auto-intersección; borra el archivo
  si no cumple.
- Cache invalidado (`offset2d-v3-occt-validated`). Candados: filetes+slot,
  micro-hueco, contorno abierto. Sin módulos nuevos para el `.exe`.

### 2026-08-14f — Plasma OCCT: robustez ante BRep_API / perfiles CW

- Error en PARTS al marcar plasma (`OCCT OFFSET: BRep_API: command not done`)
  en perfiles tipo **H.V parking** (muesca circular, a menudo CW).
- `plasma_occt_offset`: ordena aristas, `ShapeFix_Wire`, fuerza orientación CCW,
  reintenta join Arc/Intersection y constructores Wire/Face; casteos
  `TopoDS.Edge/Wire` tras `Reversed()`.
- Invalida cache compensado (`offset2d-v2-occt-robust`). Candado ampliado en
  `test_plasma_occt_offset.py` (CW parking). Sin módulos nuevos para el `.exe`.

### 2026-08-14e — Bloqueo de orientación de corte (PARTS)

- Checkbox **BLOQUEAR ORIENTACIÓN DE CORTE** en el detalle de PARTS (junto a
  PLASMA): fija la orientación visible del visor como única permitida al nestear.
  `ROTAR 90°` actualiza los grados fijados si la casilla está activa; al
  desmarcar vuelven las rotaciones normales del motor.
- Persistencia por ruta DXF en `.arganest`: `orientacion_corte_por_ruta` +
  `orientacion_corte_bloqueada_por_ruta`. El hook de rotación ya no queda
  limitado a cobre.
- Manager: metal bloqueado → bake de grados en poly/marks + `grain_locked=True`
  + `allowed_rotations=[0]`. Bridge propaga el contrato y rechaza resultados
  que intercambien largo/ancho de una pieza bloqueada.
- Packers C++ (`algorithm_cpp`) leen `grain_locked` / `allowed_rotations` y
  restringen variaciones de rotación. Recompilar `algorithm_cpp` para que el
  nativo aplique el filtro; sin rebuild el rechazo Python sigue activo.
- Candado: `tests/native/test_orientacion_corte_bloqueada.py`. Regresiones
  `19/19` en ambos proyectos. Build `.exe`: sin módulos/assets nuevos; UI ya
  empaquetada (`interface.qt.visualizer` / `tab_parts`).

### 2026-08-14d — Plasma OFFSET OCCT exacto y fail-closed

- PARTS → ESP. ahora compensa con `BRepOffsetAPI_MakeOffset` de Open CASCADE
  (OCP) dentro del ANS: conserva `LINE`, `ARC` y `CIRCLE` nativos, sin iniciar
  FreeCAD ni convertir perfiles a una polilínea facetada.
- Si OCP falta, el wire es ambiguo o OCCT crea una curva no representable como
  DXF nativo, se rechaza la compensación: no se entrega un DXF degradado a
  producción. Los bulges de LWPOLYLINE se reconstruyen como ARC exactos.
- Se invalida el cache anterior de `Plasma Compensated` por versión de
  algoritmo. El empaquetador ahora recoge OCP y el módulo de offset.
- Candado: `tests/native/test_plasma_occt_offset.py`.

### 2026-08-14b — Tabla gaps: valores exactos de planta

- Corregidos los defaults de `CUT_GAP_RULES` para coincidir con la foto
  oficial: delgados (Cal 16..0.188) `0.150"`, medios (0.250..0.375) `0.200"`,
  0.500..0.750 `0.250"`, 1.000..1.250 `0.313"`, 1.500..2.000 `0.375"`.
  Placa→pieza sigue en `0.250"` fija. Cal 18 (no aparece en la foto) queda
  en `0.150"` como el grupo delgado de Herinox.
- Candado actualizado: `tests/native/test_cut_gaps_table.py`. Sin cambio de
  build: mismo módulo ya empaquetado.

### 2026-08-14 — Renesteo de calibre y worker sin ventana CUI

- Corregido el `NameError: name 'conteo_job' is not defined` al renestear un
  calibre. El registro de diagnóstico referenciaba los conteos de job/nido sin
  inicializarlos; el fallo ocurría antes de tratar geometría plasma, por lo que
  también afectaba piezas convencionales.
- `ArgaNestWorker.exe` ahora inicia con `CREATE_NO_WINDOW` en Windows: no
  vuelve a crear la consola breve durante carga, compensación o nesting.
- Candado nuevo: `tests/native/test_renest_calibre_and_worker_window.py`.
  Regresiones: `16/16` en ambos proyectos. Sin cambio para
  `tools/build_arga_exe.py`: el módulo ya estaba declarado como crítico e
  importado en el smoke del empaquetado; no se añadieron assets ni binarios.

### 2026-08-13g — Margen placa→pieza final de 0.250"

- Se confirmó el defecto con la medida CAD: `8.26 mm` (`0.325"`) era
  exactamente `0.250" + ½ kerf de 0.150"`. Los motores C++ usan ese medio
  kerf para colisiones pieza↔pieza, pero también lo estaban aplicando al
  borde de placa.
- `engine_registry.py` adapta solo las placas rectangulares de los motores
  productivos: el packer recibe `margen - kerf/2`, de modo que la separación
  física final placa→pieza queda en el `0.250"` de la tabla para cualquier
  calibre. RTZ y huecos irregulares conservan su margen exacto.
- Los filtros de cambio de placa usan ahora únicamente el margen físico de
  borde; ya no suman kerf al decidir si una placa cabe.
- Candado nuevo: `tests/native/test_plate_margin_constant.py`. Prueba real
  Cal `0.313"`/kerf `0.150"`: `6.343437 mm` (`0.249742"`). Regresiones:
  `15/15` en ambos proyectos. No requiere cambios en `build_arga_exe.py`:
  no hay imports dinámicos, binarios ni assets nuevos.

### 2026-08-13f — Compensación plasma persistente al reacomodar

- El cambio de placa reconstruía las piezas desde el DXF base y las
  transferencias descartaban ruta y metadatos plasma; por eso una pieza marcada
  en **PARTS → ESP.** perdía su compensación al mudar, cambiar placa o renestear.
- La reconstrucción de piezas ahora toma PARTS como fuente de verdad y vuelve a
  cargar el DXF compensado. Transferencias y cambio de placa conservan
  `plasma_compensada_manual`, offset, ruta compensada, ruta original e identidad.
- El refresco de display y la renovación en pose ahora usan `ruta_plasma` para
  piezas ya compensadas; si ese archivo falta, conservan el polígono nestado en
  vez de degradarlo al DXF base.
- Candado nuevo: `tests/native/test_plasma_compensation_persistence.py`, incluido
  en `run_regresiones.py`, cubre transferencia/reempaque y reconstrucción desde
  DXF compensado. No se requieren cambios en `build_arga_exe.py`: no hay módulos,
  binarios ni assets nuevos para empaquetar.

### 2026-08-13e — NvidiaSpark y STEPS restringidos en Configuración Global

- **NvidiaSpark** queda visible en Configuración Global, apagado por defecto.
  Activarlo pide `DYT361`; al estar activo prueba el worker remoto y hace
  fallback automático al motor local si no responde.
- La versión New incorpora el router remoto compatible con los motores
  existentes, por lo que el switch ejecuta el mismo motor en Spark o local,
  no solo cambia la interfaz.
- Los checks de **STEPS** permanecen visibles y bloqueados hasta pulsar
  `EDITAR STEPS` y validar `DYT361`.
- El build incluye módulos dinámicos y la plantilla
  `_config/nest_runtime.json`; `test_nvidia_spark_prefs.py` protege el
  arranque local y la activación explícita.

### 2026-08-13d — Tabla oficial de gaps de corte por calibre

- **`modules/nesting_engine/cut_gaps_table.py`** nuevo: concentra las reglas
  oficiales de separación placa→pieza y pieza→pieza, reconoce calibre y
  espesores decimales/fraccionarios, persiste cambios en
  `_config/cut_gaps_table.json` y protege la edición con contraseña.
- **`manager.py`**, **`sheet_integrity.py`** y
  **`_mixin_plate_mgmt.py`** usan los gaps guardados por hoja para que el
  calibre determine el kerf real durante preflight, nesting y renest.
- **Configuración Global** reemplaza los campos globales de kerf/margen por la
  tabla visible; su edición se abre con `EDITAR TABLA GAPS`.
- **`tests/native/test_cut_gaps_table.py`** agregado a
  `run_regresiones.py` como candado de las reglas, normalización y contraseña.

### 2026-08-13c — PR 3: canal de release (updater sin git ni rebuild) + swap atómico + instalador por-usuario

- **`modules/app_auto_update.py`** reescrito como cliente del canal:
  - `check_for_updates()` descarga `latest.json` desde
    `ARGA_NEST_CHANNEL_URL` (default: GitHub Releases del repo). Compara
    `version` contra `arga_build_manifest.json` de la carpeta del .exe.
  - `apply_update()` descarga el zip, verifica sha256, extrae a
    `%LOCALAPPDATA%\ArgaNestingSuite\app\<version>-<commit>\`, escribe
    sentinel `.ok`, marca `pending_switch` en `install.json` y lanza
    `tools/arga_apply_switch.ps1` para hacer el swap tras el cierre.
  - Preserva la API pública (dataclasses `UpdateInfo`, `UpdateResult`;
    `check_for_updates`, `apply_update`, `dismiss_available_update`,
    `launch_restart`, `entry_mode`). La UI (`main_window.py`) no cambia.
  - Elimina toda dependencia de git / MSVC / PyInstaller en el cliente.
  - Purga versiones viejas dejando la activa + N (default: 2).
  - Update forzado si `local_version < latest.min_supported_version`.
  - Dismiss ahora persiste por *versión*, no por commit (canal-first).
  - En dev (`python main.py`) devuelve `has_update=False` con razón
    "Modo desarrollo".
- **`tools/arga_apply_switch.ps1`** nuevo: espera cierre del PID,
  cambia la junction `app\current` a la versión nueva (mklink /J, sin
  admin), limpia `pending_switch`, borra el zip de `updates/`, relanza
  la app. Fallback: si el swap falla, relanza `app\current` anterior
  (nunca queda estado sucio).
- **`tools/install_ans.ps1`** nuevo: instalador de primera vez
  por-usuario. Descarga `latest.json`, verifica sha256, extrae a
  `app\<version>-<commit>`, crea junction `app\current`, opcionalmente
  crea shortcuts (Escritorio + Menú Inicio), escribe `install.json`.
  Sin admin, sin MSI. Idempotente vía `-Force`.
- **`tools/arga_apply_update.ps1`** eliminado (compilaba en cliente;
  reemplazado por swap atómico).
- **`tools/build_arga_exe.py`**: siembra `tools/arga_apply_switch.ps1`
  junto al .exe y en `<dist>/tools/`. Deploy checklist lo valida.
- Smoke tests (dev, frozen simulado con canal `file://`, dismiss, skip
  via env, download_url + sha256 correctamente propagados): PASS.
- Variables de entorno soportadas:
  - `ARGA_NEST_CHANNEL_URL` — override del canal (http/https/UNC/local).
  - `ARGA_NEST_GITHUB_REPO` — repo GitHub para el default.
  - `ARGA_SKIP_AUTO_UPDATE` — apagar todo el flujo (ya existía).
  - `ARGA_NEST_DATA_DIR` — override del data_dir (de PR 1).
  - `GITHUB_TOKEN` / `GH_TOKEN` — auth para GitHub Releases privado.

### 2026-08-13b — PR 2: build onedir por default + `defaults/` en bundle + release artifact (zip + `latest.json`)

- `tools/build_arga_exe.py`:
  - Default cambia de `--onefile` a **`--onedir`** (release-friendly:
    swap atómico por carpeta versionada; `--onefile` queda como legacy).
  - Nuevas banderas: `--release`, `--release-notes`,
    `--release-min-version`.
  - `_pyinstaller_data_args` mete las plantillas de mutables en el bundle
    como `_MEIPASS/defaults/<rel>` (inventario_remanentes.csv,
    configuracion_nesting.json, `_config/step_export_folders.json`).
  - `seed_persistent_sidecars(onefile=False)`: en onedir siembra las
    plantillas en `<dist>/defaults/…` (nunca al lado del .exe). En
    onefile mantiene el comportamiento legacy para compat.
  - `verify_build_artifacts` y `print_deploy_checklist` verifican
    presencia de `defaults/*` en onedir.
  - `write_build_manifest` incluye `version` (calendario `YYYY.MM.DD`),
    `layout`, `git_commit_short` y retorna el `Path` del manifest.
  - Nueva `write_release_artifacts()`: zippa la carpeta onedir a
    `dist/releases/ArgaNestingSuite-<version>-<commit>.zip`, calcula
    sha256 y escribe `dist/releases/latest.json` con `url` vacía
    (la llena `publish_release.py`).
- `tools/publish_release.py` **nuevo**: sube zip + `latest.json` a
  GitHub Releases (via `gh`) o a UNC (`\\fileserver\ANS\channel`).
  Verifica sha256 del zip antes de publicar y escribe `url` +
  `published_at_utc` en `latest.json`.
- `config.py`: `bootstrap_data_dir()` ahora, además de migrar el layout
  legacy, siembra `defaults/inventario_remanentes.csv`,
  `defaults/configuracion_nesting.json` y
  `defaults/_config/step_export_folders.json` desde el bundle
  (`_MEIPASS/defaults/`) al `data_dir` del usuario en primer arranque.
  Nunca sobreescribe archivos existentes.
- Smoke tests (dev + frozen simulado con `_MEIPASS/defaults/`, y
  edición de usuario en segunda corrida): PASS.
- `build_arga_exe.py --help` parsea todos los flags nuevos.
- Nota: PR 2 aún **no** conecta el updater al canal; el `.exe` sigue
  arrancando igual que hoy. PR 3 introduce `ArgaNestingLauncher.exe`,
  reescribe `modules/app_auto_update.py` para leer `latest.json` y
  retira `tools/arga_apply_update.ps1`.

### 2026-08-13 — PR 1: rutas persistentes en `%LOCALAPPDATA%\ArgaNestingSuite\data` + migración legacy

- Motivo: preparar el rediseño de despliegue (release por canal + swap
  atómico de versión) sin tener que rescribir cliente. Primer paso:
  separar **inmutable** (bundle del .exe) de **mutable** (datos del
  usuario) para que un update no pueda pisar historial ni configs.
- Nuevo en `config.py`:
  - `data_dir()`, `install_root()`, `bootstrap_data_dir()`,
    `_migrate_legacy_data()`.
  - Frozen: mutables viven en `%LOCALAPPDATA%\ArgaNestingSuite\data\`.
  - Dev (`python main.py`): sigue en la raíz del repo (compat).
  - Override: `ARGA_NEST_DATA_DIR` (pruebas / portable).
  - `ruta_persistente` ya no devuelve `dirname(sys.executable)`;
    devuelve `data_dir() / rel`.
  - `asegurar_archivo_persistente` siembra desde
    `_MEIPASS/defaults/<rel>` (release nuevo), luego `_MEIPASS/<rel>`,
    luego `dirname(exe)/defaults/<rel>` y `dirname(exe)/<rel>` (compat).
- `main.py`: llama `bootstrap_data_dir()` antes del crash log y hace
  `chdir(data_dir)` en frozen; crash log pasa por `ruta_persistente`.
- `modules/nesting_engine/ai_telemetry.py` y `dxf_export_log.py`:
  `_logs/` cae en `data_dir` cuando frozen (antes escribían a
  `_MEIPASS/_logs/` = temp que se borra al salir → logs perdidos).
- Migración one-shot en primer arranque de la versión nueva: mueve
  `historial_jobs.json`, `inventario_remanentes.csv`,
  `herinox_sync.local.json`, `configuracion_nesting.json`, `Plates.xlsx`,
  `cache/`, `TEMP_PROCESSED/`, `_logs/`, `_config/` de `dirname(exe)` a
  `data_dir`. No sobreescribe si ya existen en destino.
- Smoke test (dev + frozen simulado + migración legacy + 2ª corrida
  no destructiva): PASS.
- Compatibilidad build actual: `seed_persistent_sidecars` sigue igual;
  actúa como fuente de semilla para la migración one-shot. Los cambios
  del build (`--onedir` por default, `defaults/` en el bundle,
  publicación por canal) llegan en PR 2 / PR 3.

### 2026-08-11b — Regla: paridad permanente de `build_arga_exe.py`

- Cada mejora/modificación del ANS debe validar si
  `tools/build_arga_exe.py` empaqueta ese cambio y, si no, arreglarlo
  en el mismo cambio. Documentado en `AGENTS.md` #10 y
  `.cursor/rules/build-exe-parity.mdc` (`alwaysApply`).

### 2026-08-11 — build_arga_exe empaqueta ArgaNestCore + Worker + fixturas

- Diagnóstico: `tools/build_arga_exe.py` solo compilaba/empaquetaba
  `algorithm_cpp.pyd` (legacy) mientras `main.py` activa por defecto
  `ARGA_NEST_CORE=1`, `ARGA_NEST_WORKER=1`, `ARGA_NEST_CUDA=1`. El .exe
  salía sin núcleo nuevo, sin Worker y sin DXF de fixtura Amada.
- Build: compila `native/build_arga_nest_core.ps1` (CUDA si hay Toolkit),
  empaqueta `arga_nest_core.pyd`, `ArgaNestWorker.exe`, fixturas,
  `_config/`, `configuracion_nesting.json`; checklist/manifiesto ampliados.
- Frozen paths: `config.app_search_roots()`, worker/fixtura/prefs leen
  sidecar junto al .exe o `_MEIPASS`; `nest_engine_config` persiste junto
  al exe.

### 2026-08-07c — Muda cross-WO: pool 1:1 y conteo no ignora el calibre

- Síntoma: 5/16 tenía 70; al mudar 1 pieza el donante quedaba en 66 (faltaba una
  placa de 3) y `PIEZAS TOTALES: 130` / `PLACAS: 4` ignoraban todo ese calibre.
- Causa: `_quitar_piezas_de_pool` descontaba la pieza por nombre exacto **y** otra
  vez por nombre base → pool corto; al refrescar, `sanitizar_hojas_grupo`
  eliminaba un bloque madre y el poka-yoke marcaba `error` en el grupo; los
  labels saltaban grupos con `error`.
- Fix: una pieza mudada = una baja en el pool; labels cuentan hojas aunque el
  grupo tenga aviso/error de inventario.
- Candado: `tests/native/test_transfer_pool_doble_descuento.py`.

### 2026-08-07b — WOs independientes: se elimina la réplica a gemelas

- Síntoma: tras mudar `Top_Cover_1` WO1→WO2 (éxito), al volver a la WO donante
  faltaban placas / piezas del calibre (p. ej. 70→66 PZAS, P13/P14 desaparecidas).
- Causa: `_replicar_lote_activo_a_gemelos` copiaba el nest activo a otras WO del
  mismo `lote_k`, y además al cambiar de WO se podía **aliasar** el dict `data`
  (misma referencia en memoria) si no había `gemelo_desync`.
- Fix: réplica a gemelas desactivada (noop); persistir/cargar WO siempre con
  `deepcopy`; renesteo de lote ya no limpia `gemelo_desync` para reactivar sync;
  carga de workspace también desacopla.
- Se mantiene el muda intencional entre WO (desacoplar + transferir).
- Candado: `tests/native/test_wo_gemelas_sin_replica.py`.

### 2026-08-07a — Mudar pieza WO→WO: ya no «no se pudo identificar»

- Síntoma: al transferir `Top_Cover_1` a otra Work Order (destino PLC059 óptimo)
  fallaba con «No se pudo identificar la pieza en la placa actual». El naranja
  del % en el diálogo es solo color de eficiencia baja, no el fallo.
- Causa: antes de mover entre WO se hace deepcopy para desacoplar gemelas; se
  rompía `id()` de hoja/pieza y el lookup de origen usaba la 1.ª `placa_id`
  ambigua (`_hoja_origen_idx` nunca se pasaba).
- Fix: capturar índice de hoja origen antes del clone; resolver por
  idx/uid/seq (sin primera placa_id ambigua); rematch de candidatos por
  `debug_id`/offset tras el deepcopy.
- Candado: `tests/native/test_transfer_cross_wo_identidad.py`.

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
