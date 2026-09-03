# New Arga Nesting Suite — Seguimiento

> Core **0.5.3** · ABI **1.4.0** · Paridad CAM relativa  
> Compact-lite: **ON** (band-close + backfill) · Lite = **MC propio** (no FORCE) · hole-fill ON · Venom OFF  

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
- `ARGA_LITE_HOLE_FILL=0|1` (**default 1**) · post-pase nest-in-hole en Lite
- STEPS por carpeta: `_config/step_export_folders.json` (Configuración Global)
- Lite: `ARGA_LITE_PASSES=1` · `ARGA_LITE_PLATE_RENEST=1` · tries/mc=1

## Verificar

```powershell
cd "C:\Proyectos\New Arga Nesting Suite"
py -3.14 tests\native\run_regresiones.py   # obligatorio: candados de bugs ya corregidos
py -3.14 tests\native\test_compact_lite.py
py -3.14 tests\native\test_band_close.py
```

`run_regresiones.py` no necesita BD ni core compilado. **Cada bug corregido agrega
su candado ahí**, con el caso real que lo motivó y verificando que falle contra el
código viejo. Un bug sin candado vuelve.

## Siguiente foco

1. Fase 4 Spark: obtener SSH key vía NVIDIA Sync (`arga_dev`) y levantar `nest_remote_server` en `192.168.2.35:8765`
2. Lite hole-fill: probar en bridas reales (P13 / SP rings); si falta, pull desde restos
3. Telemetría WO → AI UI default (ya hay `result.runtime` en packs)

## Baseline estable (planta)

- **Commit:** `712f499` — `fix(nest): close_pair solape ya no tumba GIGA (faltan 34)`
- **Rama:** `feature/amada-copper-export`
- **Criterio:** nest Cal 11 Galv (GIGA) completa inventario sin EMPAQUE-STOP por
  solape 101×102; void-first + patio MC + pasillo/Fase B. Tratar como última
  versión estable de referencia hasta el próximo release explícito.

## Changelog

### 2026-09-03c — Ultra: cupo en placa = metal + margen (no kerf)
- Bug: al Cambiar de placa a PLC058 120×48 (pieza 81.037×47.374, margen
  0.200, ENTRE PIEZAS 0.250) Ultra rechazaba todo (`placed=0`). Dos causas:
  (1) variaciones medían AABB **con buffer de kerf** vs `placa−2·margen`;
  (2) `comprobar_colision` / IFP de placa exigían que el **globo de kerf**
  cupiera dentro del margen (Lite/base ya usaban solo metal).
- Fix: Ultra / libnest2d / burke / packer lite — rechazo e IFP de placa con
  **metal**; kerf solo entre piezas. Candado
  `test_ultra_plate_fit_metal_not_kerf.py` (1 colocada + 1 resto). Mensaje UI
  de “sin espacio” ya no habla de kerf en el borde.

### 2026-09-03b — GIGA zig-zag: no tumbar inventario (faltan 174)
- Causa: zig-zag corría sin `placa_w/h` (checker ciego, I fuera de placa)
  y en torres con patio (P6 BKT) la cascada Y aplastaba invitados; poka
  expulsaba → EMPAQUE-STOP / faltan N.
- Placa+kerf se sellan ANTES de close/zigzag. Zig-zag solo en torre
  VFM (sin patio). Candados patio + sin-placa.

### 2026-09-03 — GIGA Cal 11: zig-zag automático en torres VFM
- Post-proceso `zigzag_vfm_tower_stack` tras `close_pair`: torres de ≥3
  VFM-20 escalonan en X (~2.5–6\") con engrane T (gap_y negativo) y
  cascada Y; opcional pull de otra I desde restos.
- No toca hojas de 1 par (P1–P5 mixtas con HFM/patio).
- Candados: escalona X, no-un-par, franja/quinta; `run_regresiones`.

### 2026-09-01i — Largos: PDF descripción completa + recalcular demanda CSV
- PDF consumo en piso: columna **DESCRIPCION DEL MATERIAL** con ajuste de
  tipografía y saltos con guiones (sin truncar `...`; mismos anchos de columna).
- Modal Nesteo de largos: botón **Recalcular desde CSV de demanda** (encima del
  PDF) — relee AutoDXF, refresca catálogo react-Herinox, reimporta
  `lista_largos_job` y regenera plan (evita PTR048 en CSV vs PTR027 en nest).
- `obtener_filas_demanda_contexto` prioriza CSV forzado para PDF y export.

### 2026-09-01h — Config: switch cobre sin marcaje (solo Corte CyPTube)
- Preferencia `cu_sin_marcaje` en Configuración Global (junto a forzar DXF+STEP).
- **Default ON** (norma planta): solo corte; OFF restaura MARK + Marcaje (con contraseña).
- AutoDXF/reproceso respeta el switch: sin capa MARK ni stick en Processed Files.
- Export: barra DXF cuenta solo archivos reales (sin Marcaje si cu_sin_marcaje); barra STEP oculta si no hay 3D.
- ON: export cobre sin capa MARK; verticales solo `*_Corte.dxf` (sin `*_Marcaje`).
- JSON `cyptube_verticales.json` marca `sin_marcaje`; CypTube RPA sigue con corte.
- Candado: `test_split_solo_corte_sin_marcaje` en `test_cyptube_vertical_split.py`.

### 2026-09-01e — Repo/release: main al día + tag GitHub en commit real
- `publish_release.py`: `gh release create --target <commit>` del
  `latest.json` (antes caía a `main` viejo: tag con hash nuevo, commit julio).
- Regla `release-publish.mdc` + `AGENTS.md` 9b: release exige push **y**
  actualizar `main` remota, no solo la feature branch.
- Commit pendiente UI scale / cinta / PARTS-SHEETS + candado `test_ui_scale`.

### 2026-09-01g — Cinta 1 fila ancho completo + bloqueo orientación en barra CAD
- Nesting: cinta original (1 fila) a **ancho completo** arriba del splitter;
  botones no se comprimen (scroll con rueda si hace falta, sin barra fea).
- PARTS visor: **BLOQUEAR ORIENTACIÓN** en la barra superior junto a ROTAR 90°;
  más espacio al panel detalle (splitter equilibrado).

### 2026-09-01f — Cinta Nesting: restaurada layout original (1 fila)
- Se revirtió el rediseño a 2 filas; vuelve la cinta AutoCAD de una fila
  en el panel derecho (como antes de los experimentos de UI scale).

### 2026-09-01e — Cinta Nesting ancho completo + PARTS ESP usable
- Cinta: **2 filas a ancho completo** (arriba del splitter). Todos los
  comandos visibles; sin scroll feo ni apilado que recorte títulos.
- PARTS: toolbar en 2 filas (DEMANDA completo); columna **ESP** con
  checkbox visible (Amada cobre / Plasma acero); tabla con scroll
  horizontal para no aplastar columnas.

### 2026-09-01d — Cinta Nesting compacta + PARTS AMADA/PLASMA visible
- Cinta: altura compacta, botones Fixed (sin hueco vacío), sin barra de scroll
  visible; rueda del mouse desplaza si no cabe. Separación clara vs panel oscuro.
- PARTS: columna **AMADA / PLASMA** (antes ESP.) con checkbox etiquetado
  Amada/Plasma; anchos mínimos para que no se esconda. Toolbar sin barra fea.
- SHEETS filtros: mismo scroll invisible + finalize de ancho.

### 2026-09-01c — UI scale: cinta Nesting + barras PARTS/SHEETS
- Cinta Nesting más alta (140px diseño), botones con más padding y columnas
  apiladas sin recorte vertical; scroll horizontal si el panel es angosto.
- PARTS: toolbar superior con scroll + botones escalados; visor CAD con barra
  y métricas en scroll horizontal (LARGO/ANCHO/… ya no se truncan).
- SHEETS: fila de filtros con scroll horizontal; combos y etiquetas escalados.
- Helper reutilizable `make_hscroll_toolbar` en `layout_helpers.py`.

### 2026-09-01b — UI scale: ANS respeta resolución/escala de cada PC
- Baseline diseño 1920×1080: en pantallas/VM más chicas reduce botones,
  márgenes y diálogos (`interface/qt/ui_scale.py`); no agranda sobre diseño.
- High-DPI PassThrough en `main.py`; FILES con scroll + tamaños `s()`.
- Diálogos clave usan `fit_window` / `set_scaled_*` (largos, nest sim, modales).
- Candado: `tests/native/test_ui_scale.py`.

### 2026-09-01 — CypTube Modo B: ANS dispara auto-nest al exportar cobre
- Tras `cyptube_verticales.json`, ANS lanza CypTube
  `auto-nest --nesteos-dir … --skip-wait` (consola nueva, no bloquea UI).
- **Local y servidor 80:** pasa la ruta absoluta real del export (UNC si
  `exportar_a_servidor`); CTDS junto a ese `NESTEOS DE COBRE`. Log
  `destino=servidor|local`. Remapeo opcional `path_maps`.
- Config: `_config/cyptube_bridge.json` (`enabled`, `cyptube_main`, `python_exe`).
- Off: `enabled: false` o `ARGA_CYPTUBE_AUTO_NEST=0`.
- Módulo `modules/dxf_export/cyptube_bridge.py`; candado `test_cyptube_bridge.py`.
- Convenio ANS↔CypTube actualizado (Modo B + dual path).

### 2026-08-31c — PO correo: SMTP real + reenvío GAM 13048 (SWO-048)
- Export SWO-048 creó OC/PDF (`PO_GAM_13048.pdf`, total $43,697.37) y ANS
  vio `nombreReporte`, pero el mail no llegó (InsertaPO solo montaba
  `manager.py`; mailer de la imagen podía “éxito” sin SMTP).
- InsertaPO `manager.py`: PDF + SMTP forzado con aceptados>0 (3 reintentos).
- `docker-compose`: monta también `reportePOGAM` / `enviarPDFCorreo` / `main`
  (requiere recreate del contenedor en 2.80).
- ANS: `correo_po_confirmado` respeta `correo_enviado`.
- Reenviado correo GAM 13048 (9/9 SMTP OK). Totales PDF OK.

### 2026-08-31b — PO ContPAQ: no dar por bueno el export sin correo (GAM 13040)
- Causa SWO-047: InsertaPO creó OC+PDF, SMTP falló, HTTP 200 con
  `nombreReporte=""`; ANS no validaba el campo → “PO confirmada” sin mail.
- InsertaPO (`manager.py`): 3 reintentos de PDF/correo tras crear la OC.
- ANS: `correo_po_confirmado` + checkpoint `CORREO_PO` WARNING + aviso UI;
  CONTPAQ queda OK para no duplicar `/run` al reanudar.
- Candado: `tests/native/test_correo_po_nombre_reporte.py`.

### 2026-08-31 — SWO MRL: mismo plan al pedir y validar (SWO-047)
- Causa: export armaba MRL desde el plan del modal (15× ANG037 @ 240\") y
  validaba regenerando el plan en BD (14) → rollback.
- Conteo correcto: **15** (demanda ~3417\" no cabe en 14×239\" útiles).
- Fix: `aplicar_pedido…` devuelve el plan usado; `validar_mrl…` lo recibe
  como `plan_referencia` (no regenera packing distinto).
- Candado: `tests/native/test_mrl_swo_plan_referencia.py`.

### 2026-08-28g — Plasma: no duplicar DXF láser/cama en hojas compensadas
- Causa SWO-042: piezas con `plasma_compensada_manual` sin flag de hoja → canal
  plasma + DXF láser/cama del mismo H (cotas 1:1 vs compensadas).
- `hoja_export_solo_plasma` / `_debe_generar_plasma` alineados a pieza|hoja;
  `promover_compensacion_plasma_en_hoja` en export y transfer.
- Candado: `tests/native/test_plasma_export_solo_sin_duplicar_laser.py`.

### 2026-08-28f — STEP planta rápido (PIECE_ONESHOT multi-tool)
- Flujo ANS único para Crear STEPs / export DXF+3D / despachador:
  `ENGRAVE_PIECE_ONESHOT` = 1 CUT multi-tool por pieza (OCCT `SetTools`),
  no chunks de 50 ni compound de ranuras cruzadas.
- Defaults: `step_mark_mode` oneshot, `mark_chunk=0`, flatten texto ≥1.25 mm,
  `piece_workers=2`. Override: `ARGA_STEP_MARK_MODE` / `ARGA_MARK_TEXT_FLATTEN_MM`.
- Bugfix: `mark_chunk=80` ya no anula oneshot (volvía al camino lento).
- Validado en SWO-038/039 Plasma: ~5–22 s/placa vs minutos-horas.
- Candado `test_step_mark_fast_defaults.py`.

### 2026-08-28e — Reexport WO/SWO limpia NESTING previo
- Al exportar DXF de una WO/SWO, si ya existe `ARGA MODEL CORE/NESTING`
  (y placeholders `DXF NESTING` / `3D NESTING`), se elimina el árbol completo
  antes de recrear carpetas y escribir. Evita DXF/STEP/PDF/JSON huérfanos
  tras fallos o cambios de piezas/familias/cantidad de hojas.
- Helper `limpiar_nesting_previo` en `modules/nesting_engine/exporter.py`.
- Candado `test_limpiar_nesting_previo.py`.

### 2026-08-28d — SWO: TAPA≠TAPA 2 + reproceso conserva W.O.__
- Carga SWO: match de DXF por pieza exacta (ya no `startswith` con espacio).
  Caso SWO-043: `BUSHING PATCH TAPA` no toma el DXF de `… TAPA 2`.
- REPROCESAR AUTODXF en contexto SWO: fusiona rutas nuevas sin quitar el
  prefijo `W.O. N XN__`, sin pisar qty BD ni `job_activo` (SWO-xxx).
- Candado `test_swo_autodxf_item_match.py`.

### 2026-08-28c — CyPTube Marcaje: guillotina origen + fin
- `*_Marcaje.dxf` = MARK + líneas a ancho de barra en extremos (BAR_START /
  CUT_OUTER origen y fin). Sin barrenos ni guillotinas intermedias.
- Corte sin cambios. Contorno de referencia para empalme CypTube.

### 2026-08-28b — CyPTube: Marcaje también en PQART
- Fallo W.O. 66: `hay DXF exportados sin registro PQART` en `*_Marcaje.dxf`.
- Causa: split Corte+Marcaje añadía ambos a `exportados` pero solo registraba
  Corte. Ahora se registran los dos (mismo `tipo_corte`).

### 2026-08-28 — CyPTube vertical: Corte + Marcaje + JSON
- Barras verticales cobre (Amada ESP y escalón `sin_gap`): el DXF combinado
  se parte en `*_Corte.dxf` (CUT_*) y `*_Marcaje.dxf` (MARK); se elimina el
  combinado.
- Manifiesto `NESTEOS DE COBRE/cyptube_verticales.json` con `A_mm=ancho+0.2`,
  `B_mm=6.0` y lista plana `archivos[]` (rol corte/marcaje) para automatización.
- Módulo `modules/dxf_export/cyptube_vertical.py`; candado
  `test_cyptube_vertical_split.py`.

### 2026-08-27b — Eliminada regla de copiar a ANS C++
- `AGENTS.md` y `build-exe-parity.mdc`: único árbol =
  `New Arga Nesting Suite`. No sync / Compare-Object a `ANS C++`.

### 2026-08-27 — Cobre DXF: R2000 AutoCAD-safe obligatorio al exportar
- Causa: AutoCAD 2026 descarta DXF R2010 de ezdxf (fingerprint OBJECTS) →
  pantalla negra + "Press ENTER to continue".
- Fix en export (no repair externo): `_save_cobre_dxf_atomic` siempre
  reescribe a R2000/AC1015, sincroniza vista/LIMMAX, quita fingerprint, y
  **falla el export** si el archivo en disco no pasa el assert AutoCAD.
- Caminos: `export_cobre_hoja_to_dxf` + `export_nest_to_dxf` (`solo_cobre`).
- Candado: `test_cobre_export_limmax_cubre_geometria` +
  `_assert_dxf_autocad_safe_on_disk`.

### 2026-08-26b — Amada ESP: vertical con MARK; FIXTURA colchón +10\" join
- Barra vertical CyPTube (ESP.): cortes + **MARK**; sin barrenos.
- AMADA/FIXTURA: rectángulo **15\"** (5\" pieza + 10\" colchón) como LWPOLYLINE
  cerrado + barrenos en banda superior; sin marcaje.
- Módulo `modules/dxf_export/amada_esp.py`.
- Candados: `test_cyptube_esp_vertical_marks.py`, `test_amada_export_sin_fixtura.py`.

### 2026-08-26 — Cobre sin_gap: MARK de vuelta en DXF CyPTube
- Se quitó la supresión de `draw_marks` en export `sin_gap` (CyPTube).
- Capas: CUT_OUTER + CUT_INNER + **MARK** + BAR_START (sin Plate/CUT_CU).
- Excepción intacta: piezas `cu_especial_vertical` Amada siguen sin MARK.
- Candado: `tests/native/test_cobre_sin_gap_marks.py`.

### 2026-08-25d — Lista de largos NUNCA tumba el export (job_mismatch)
- Caso real: W.O. 37 X2 / carpeta `9919-12CABINET2` + `job_data_9913-12CABINET2.csv`
  → `estado=job_mismatch` abortaba PQART/reporte_cortes.
- Causa de regresión: fixes previos usaban **whitelist**
  (`csv_no_encontrado` / `csv_vacio` / `autodxf_no_existe`). Cada status nuevo
  volvía a tumbar el multi-lote.
- Fix estructural: import de largos es **siempre opcional**. Cualquier `ok=False`
  o excepción → aviso + commit de nesting. SWO igual (sin raise por largos).
- BD residual W.O. 37 (costos_prorrateo + ERP tipado 9913) purgada.
- Candados: `test_job_mismatch_caso_9919_vs_9913` +
  `test_postgres_nunca_raise_por_lista_largos` (prohíbe reintroducir los raise).

### 2026-08-25c — OCCT: agujeros ya no se comen piezas (H7)
- Causa real: `FClass2d` en círculos/`CUT_OUTER` reportaba OUT hasta el centro;
  el fallback por bbox aplicaba `CUT_INNER` ajenos y destruía sólidos (H7 pieza
  gemela 6.8M→1.8M). Inventor veía nests incompletos.
- Fix: point-in por polígono muestreado + asignación al outer más chico con
  bbox del agujero contenido; cut con guardia -50%/multi-sólido.
- Candado: `test_swo033_h7_keep_volumes` + `test_point_in_circle_outer`.
- Regen detenido; reabrir ANS y regenerar STEPs con el fix.

### 2026-08-25b — Crear STEPs: DXF también stagea a %TEMP%
- Flujo estable: UNC/unidad de red → copia DXF a `%TEMP%` → ezdxf+OCCT local →
  STEP en `%TEMP%` → copia al servidor → limpia temps.
- `engine/local_staging.py` + `collect_dxf_nest` (cubre Crear STEPs / despachador / OCCT).
- Override: `ARGA_STAGE_DXF=0` off · `=1` fuerza stage aunque el path sea local.
- Candado: `tests/native/test_dxf_local_staging.py`.

### 2026-08-25 — OCCT STEP: piezas completas + acero + marcaje estable
- Bug: `CUT_INNER` global borraba piezas chicas (H4 9→5 sólidos). Ahora cada agujero solo aplica a su `CUT_OUTER`.
- Bug: `CUT_OUTER` como CIRCLE (H9) no se leía → 0 sólidos.
- Bug: default `material=CU` pintaba acero de cobre. Default = `STEEL` (CU solo cobre).
- Marcaje: TEXT→stroke; modo producción = `ENGRAVE` por lotes (no ONESHOT que se cuelga horas).
- I/O: STEP se escribe en `%TEMP%` local y se copia al UNC (no dump XCAF en red).
- Poka-yoke: si `solids != outers` → RuntimeError (no publica STEP a medias).
- Candado: `tests/native/test_occt_inner_per_piece.py`.

### 2026-08-24 — STEP: OCCT único en UI (FreeCAD solo env legacy)
- **Crear STEPs** y **Exportar DXF+3D** ya no preguntan motor; siempre **OCCT (Arga)**.
- Paridad acero/cobre: `collect_dxf_nest`, ENGRAVE_ONESHOT, cobre XCAF, STEP universal sin camas.
- Escape hatch debug: `ARGA_CREAR_STEPS_MOTOR=freecad` / `ARGA_EXPORT_3D_MOTOR=freecad`.
- Candado actualizado: `test_crear_steps_motor_choice.py`.

### 2026-08-21e — Crear STEPs: elegir FreeCAD u OCCT
- Misma pregunta que Exportar 3D (`FreeCAD` / `OCCT (Arga)`).
- `procesar_ruta_nesting(..., motor_3d=)` propaga la elección.
- Default del diálogo: OCCT (recomendado nests 1:1).
- Candado: `test_crear_steps_motor_choice.py`.

### 2026-08-21d — Release completo + Crear STEPs/buzón en el .exe
- Regla: cerrar ANS antes de `--release`; smoke 100% o repetir
  (`.cursor/rules/release-complete-packaging.mdc`).
- Crear STEPs: empaquetar `CAD (OCCT)/engine` (`--paths` + hidden-import
  `engine.step_paths`) — fallaba ModuleNotFound al elegir ruta en frozen.
- Buzón: Outlook vía win32com o PowerShell oculto (`-WindowStyle Hidden -Sta`);
  ya no deja consola PS abierta.
- Candado: `test_release_engine_step_paths_packaged.py`.

### 2026-08-21c — FreeCAD STEP: barra % por carpeta
- Causa: FreeCAD no reporta avance; el diálogo quedaba en STEP 0/N aunque
  ya hubiera `.step` en disco (solo `mensaje=` sin `step_done`).
- Fix: `contar_cad_vigentes` + `progress_cb` tras cada DXF en
  `freecad_runner`; `lanzar_freecad_robotica` acumula `step_done_base`.
- Candado: `test_freecad_step_progress_folder.py`.

### 2026-08-21b — Crear STEPs: OCCT join LINE/ARC (S.W.O 27)
- Causa: FreeCAD SKIP OUTER:0 con DXF nest 1:1 (LINE/ARC/CIRCLE en CUT_*).
- Fix: despachador/Crear STEPs usa OCCT por defecto (une bordes en memoria;
  **no reescribe DXF**). Override: `ARGA_CREAR_STEPS_MOTOR=freecad`.
- Candado: `test_crear_steps_occt_line_arc.py`.

### 2026-08-21a — Complemento feedstock STEP (MVP)
- Switch Config Global `step_feedstock_enabled` (OFF default) → 3er botón en FILES.
- Busca `.stp/.step` dentro de `AutoDXF/` (raíz o `STEP/`); escribe DXF en
  `AutoDXF/FROM_STEP/` (capas `IV_*`); carga PARTS sin mezclar DXF Inventor.
- OCCT: solo placas planas de espesor constante (sin unfold de doblez aún).
- Candado: `test_step_feedstock_mvp.py`.

### 2026-08-20q — PARTS: Material y Calibre editables
- Combos en tabla Piezas con catálogo ANS (`parts_catalog`: materiales
  canónicos + calibres Herinox/`arga_gauge_snap` + stock).
- Persistencia en `datos_partes_actuales` / `editable_inputs_*` (sin renombrar DXF).
- Candado: `test_parts_catalog_editable.py`.

### 2026-08-20p — Release: AutoDXF Cal/SM + Crear STEPs
- AutoDXF: Cal ANS por SM si empata; si Thickness mal → geo + `[REVISAR SM]`.
- Crear STEPs en cinta Salida (FreeCAD/despachador) + step_paths sin Cama A/B.
- Candados: gauge parity + step_universal_sin_camas.

### 2026-08-20m — Crear STEPs: 1 STEP plano (sin Cama A/B)
- El despachador heredaba Cama A+B con offsets robot; ANS ya usa
  `STEP_UNIVERSAL_SIN_CAMAS` (1 STEP/DXF, coords 1:1, carpeta STEP plana).
- `despachador_nocturno` + Crear STEPs alineados; candado
  `test_step_universal_sin_camas_despachador.py`.

### 2026-08-20l — Reanudar sync con contraseña DYT361
- Evita PO/ContPAQ accidentales en S.W.O.: pide la misma clave operativa
  (gaps/STEPS) antes de reintentar VSM/ContPAQ/reporte.

### 2026-08-20k — Crear STEPs desde DXF (cinta Salida)
- Botón **Crear STEPs** bajo Ver STEP: modos *Desde nesteo* (cliente→job→W.O./S.W.O.)
  y *Desde ruta* (carpeta NESTING, como despachador nocturno).
- Reutiliza `despachador_nocturno.procesar_ruta_nesting` (FreeCAD) para acero y cobre.
- `engine/step_paths`: listar W.O./S.W.O. con DXF convertibles (sin exigir STEP previo).

### 2026-08-20o — AutoDXF: sin SM/flat → exporta + carpeta [REVISAR SM]
- Ya no OMISO/excluye: DXF sale igual; carpeta/archivo marcados `[REVISAR SM]`
  para que ingeniería corrija Sheet Metal sin perder el flat.

### 2026-08-20n — AutoDXF: Flat Pattern roto = OMISO (no Cal inventado)
- CBOX Cal 3.5 / segmentos Cal 2: geometría tomaba cotas con SM/flat roto.
- Ahora: Flat Pattern unhealthy → no exporta; pliegues sin empatar → SM.

### 2026-08-20m — AutoDXF: SM Thickness vs geometría deben empatar
- Compara ambos (±0.01" o 3%). Cal DXF = medida geométrica precisa + snap.
- Si no empatan: AVISO y Cal por geometría (no por SM default Cal 11).

### 2026-08-20l — AutoDXF: espesor geométrico real (no SM default Cal 11)
- Causa: solo leía `smDef.Thickness` → lug 1\" y flanges 0.25/0.375/0.5
  caían en `Cal 0.1196 A 36`.
- Ahora: placa plana mide caras paralelas / bbox; si discrepa del param SM,
  usa geometría y luego snap Herinox.

### 2026-08-20k — AutoDXF: Acero Suave/Forjado/Genérico → A 36
- Inventor librería ES no está en Herinox; salían carpetas `Cal 0.1196 Acero, Suave`.
- Alias + ANS: Suave/Forjado/Genérico/Acero… → A 36 / CARBONO.

### 2026-08-20j — GIGA motor: cualquier Cal 11 Galv (no solo 0.11811)
- Detector + UI: 0.1196 / 0.11811 / gauge 11 + GALV activan el motor.
- Texto del switch ya no dice “solo 0.11811_GALVANIZADO”.

### 2026-08-20i — Todos los calibres: snap CAD→Herinox (materia bruta)
- No solo Cal 11: steel/inox/al ±offsets CAD → mismo decimal que Herinox.
- Módulo `arga_gauge_snap` + canonicar en import DXF y al armar clave de nest.
- AutoDXF: aliases G90/HDG/ZINC/CRS/HRS; exactos ≥3/16 (no 0.0625).
- Candado ampliado: todos los gauges + Cal 14/16/inox/al.

### 2026-08-20h — AutoDXF iLogic: no tumbar Inventor + Galv antes de A36
- Galvanizado se detecta **antes** que A 36 (antes `A 36 GALV` → `A 36`).
- Sin `Dictionary`/`For Each KeyValuePair`: tablas con arrays fijos.
- `FormatThicknessForArga` + lectura de espesor en Try/Catch (fallback CAD/`0`).
- Cadena: Herinox Cal 11 → `0.1196` → DXF `Cal 0.1196` → ANS mismo grupo.

### 2026-08-20g — AutoDXF 2.0: Cal DXF empatado a Herinox/ANS
- Antes: iLogic escribía espesor CAD crudo (`0.11811`) → nest separado de
  placas Herinox Cal 11 (`0.1196`).
- Ahora: `FormatThicknessForArga` usa la misma tabla que `herinox_sync`;
  Galvanizado en nombre (ANS `GALVANIZADO`). JSON
  `AutoDXF 2.0/arga_gauge_equivalences.json` + candado
  `test_autodxf_gauge_parity.py`.

### 2026-08-20f — GIGA: close_pair solape ya no tumba el nest (faltan 34)
- Causa real (log 10:59): `close_stacked_vfm_pairs` dejaba solape 101×102;
  `reparar_separacion_minima_hoja` ignoraba `solape_metal` (expulsadas=0);
  SIM integrity rechazaba la hoja → EMPAQUE-STOP con 34 pendientes.
- close_pair: verificar post-apply y revertir si solape/gap < kerf.
- Repair: expulsar/reubicar también en `solape_metal`; force-strip en safe-empaque.
- Retry GIGA prueba todas las placas altas, no solo la pick_giga.
- Candados: close sin solape; repair expulsa solape VFM. ANS C++ no en esta PC.

### 2026-08-20e — GIGA: no cortar nest con VFM/GS pendientes (faltan N)
- Causa: cola “placa chica” + torre de *todos* los pares → STOP parcial e
  inventario incompleto (poka 34 faltantes).
- Torre acotada a 2 pares/hoja; resto de I después de inyección.
- Con VFM pendientes no se activa cola de placas cortas; retry placa alta.
- Candados order/cola/pick. Recompilar `algorithm_cpp`.

### 2026-08-20d — GIGA: torres VFM cuando ya no hay estructurales
- Sin HFM/SIVC/SIHC/VS: el pool ordena pares 101/102 primero (torre) y
  después inyección de chicos — como captura P10.
- Con estructurales: se mantiene 1 par + barras + patio (no 4 I al 29%).
- `close_stacked_vfm_pairs` otra vez en el motor tras el MC.
- Candados order + pack torre (≥3 VFM en P1 solo-VFM+GS).

### 2026-08-20c — GIGA Fase B: densificar huecos como Mudar
- Facing ya no exige pool: jala también del patio de la misma hoja.
- Barrido denso en tira + hasta 3 pases por hoja (presupuesto ~12 s).
- Pase global al cerrar el grupo: mueve GS/BKT de hojas pobres a pasillos
  VFM abiertos (equivalente a Mudar masivo, tope 90 s).
- Candados: patio→pasillo sin pool; cross-sheet H61-style (≥6 GS).

### 2026-08-20b — Cotas CAD a milésima de pulgada
- Tabla nest y PARTS muestran L/W a 0.001\" (ya no 77.37 vs 77.38 por redondeo a 2 decimales).
- Cotas de tabla preferen AABB del DXF fuente (cacheado); misma ruta ⇒ mismo número.
- Candado `test_plasma_cotas_cad_precision`. ANS C++ no está en esta PC.

### 2026-08-20a — Plasma: Mudar no dobla L/W en la tabla
- 77.37×21.68 ya es el stock compensado; al Mudar la tabla no debe pintar 77.50×21.81.
- Sellar `plasma_fuente_ya_compensada` en incremental/packer/lote plasma; la tabla no suma 2×offset si hay `ruta_plasma`.
- Candado muda pieza ya compensada. ANS C++ no está en esta PC.

### 2026-08-19u — Plasma: visor y Mudar usan el offset vigente
- PARTS/nest ya no reusan un DXF Plasma Compensated viejo (0.0125\") solo porque el archivo exista.
- Al mudar, el pack recarga el DXF de 0.0625\" por lado y la pieza colocada conserva ruta + flags plasma (si no, el visor volvía al DXF base).
- Candados sidecar + transferencia. ANS C++ no está en esta PC.

### 2026-08-19t — Plasma: 0.0625\" por lado en todos los calibres
- Compensación unificada: 1/16\" por lado (fino y grueso). Ya no 0.0125\" / 0.250\".
- Sidecar guarda `offset_mm` para no reusar DXF compensados con la regla vieja.
- Candado `test_plasma_offset_todos_calibres`. ANS C++ no está en esta PC.

### 2026-08-19q — GIGA: no quemar 5 min/placa en pases vacíos
- Void-first escaneaba 70+ I (~66 s, filled=0). Solo el par de esta hoja.
- Grilla 96×48 en bahías (~74 s, moved=0). Esquinas+BLF.
- Combinado 36\"+48\" en serie. Solo la placa más alta.
- Retazos <12\" sin void/pasillo.
- Máx. 32 invitados por hueco (no 400 fallos × 70 s). Candado satura 12 GS.

### 2026-08-19s — GIGA: revertir cupo 2 I (H69–H99 al 14%)
- El hold 1×101+1×102 dejó ~30 placas 120×48 con solo el par.
- Invitados chicos (GS/RLG) primero en bahía, no HFM que no cabe.
- Cargo repartido a todas las I (no vaciar el pool en las primeras).
- Candado planta: motor real 120×48, GS/RLG en bahía, no hoja solo-par.
- Fix `others` indefinido que tumbaba host_bays.

### 2026-08-19r — GIGA: no más placas de 4 I al 29%
- Nest real BOARD 6: P11–P24 = 4 I vacías; P9 patio 127 (GS/304).
- Prefill reparte cargo a todas las I (cavidades en caché) y el MC
  solo ve 1×101+1×102; el resto de I espera con invitados.

### 2026-08-19p — GIGA: AutoDXF de toda la carpeta TANKS/GIGA
- Recorrido UNC (sin COBRE): hosts Cal 11 Galv = VFM-20-101/102 en BOARD 4,
  GIGABOARD5, BOARD 6, BOARD 11 y Fluidstack metal. Misma geometría
  (78.35×12.24 / 11.19, bahías 8.77" + 3.74", gutter 2.69" ilegal).
- HFM-10/12 son soleras (~97% AABB), no orificios. EQ/FCU/Cal 14/REV
  vacíos no son este motor.
- Fill de bahías/pasillo usa perfil I (no el token `-20`). Candado familia.

### 2026-08-19o — GIGA: saturar bahía (no 2 azules con aire)
- Round-robin 1/host dejaba orificios medio vacíos y 304 en el patio.
- Ahora cada bahía se llena hasta que no cabe más; orificios un poco
  más altos (~13\") también cuentan. Patio solo si el hueco sigue vacío.

### 2026-08-19n — GIGA: orificios desde el pool, no del patio
- Prefill round-robin a todos los VFM restantes (no solo 1×101+1×102).
- Post-MC jala GS/BKT de restos, no desordena líneas ya nesteadas.
- Candado: ≥3 VFM con cargo; pasillo usa `pool=`.

### 2026-08-19m — GIGA: grilla densa + bahías post-MC
- El fill del pasillo solo probaba 24 poses (paso 50 mm) y dejaba GS/304
  en el patio. Ahora grilla pieza+kerf, y un segundo pase mueve patio a
  las bahías 8.77\" del VFM. Candado ≥10 GS en hueco T-vs-T.

### 2026-08-19l — GIGA: bolsillo entre T enfrentadas
- El pasillo se medía por AABB (puntas de T). Con T contra T el hueco
  era ~0 y GS/BKT se iban al patio. Ahora: AABB conjunto − metal − kerf,
  bolsillos cuyo centro queda entre los dos VFM. Candado T-vs-T.

### 2026-08-19k — GIGA: no perder cargo (80 faltantes)
- Prefill sacaba invitados del pool; si el expand no los proyectaba, el
  pokayoke veía nest incompleto. Ahora el cargo no expandido vuelve a restos.
- Se quitó el cierre 180° del par (empujaba fuera de placa 36\").

### 2026-08-19j — GIGA: revertir cuota 2 I (H70–H99 vacías)
- Sacar I extra del MC llenó ~30 placas 120×36 a 19% con solo el par.
  Extra I vuelven al MC. Prefill sigue en 1×101+1×102.
- Cierre de par: 102 a 180° y acercar al 101 hasta kerf 0.150\".

### 2026-08-19i — PARTS: más ancho al listado
- Splitter ~55% lista (min 640px); detalle cede espacio. Nombres eliden al
  ancho real (no a 200px). Miniaturas 48px.

### 2026-08-19h — GIGA: 2 I/hoja + rectángulo de bahía
- Prefill solo 1×101+1×102; el resto de I no entra al MC (evita placas al 30%).
- Bahía = AABB inscrito con kerf, no C. HFM-10 cabe en bolsa 8.77\".
- Cargo de un VFM no colocado vuelve a restos. Candados quota/HFM/restos.

### 2026-08-19g — GIGA: void-first bahías VFM
- Prefill de bahías abiertas VFM-20 (AABB legal, kerf 0.150\") → `_void_cargo`
  → MC → expand. Sin Venom/canal post-MC (eso duplicaba tiempo sin densidad).
- Pase barato: hueco entre dos I apilados. Candado H+T tipo planta.

### 2026-08-19f — Build: LS-READY no es pip
- `auto_setup_dependencies` ya no intenta `pip install classification/dxf`.

### 2026-08-19e — GIGA: bahía 8.77\" (manual planta)
- El filtro solo veía tiras ≤5.7\" y se saltaba la bolsa 8.77\" junto a la T.
  Ahí caben BKT-304/153 y GS (como en ed. libre). Pase de canal también
  después del compact Lite. Candado bahía alta.

### 2026-08-19d — GIGA: canales de ala VFM
- Tras el pack mixto, pase dedicado: canales abiertos de VFM-20 (~3.7\")
  con kerf completo 0.150\". BKT ~3.00\" entra; GS ~3.61\" no. Jala de
  restos si cabe. Switch ON only. Candados canal/pool/gordo.

### 2026-08-19c — Switch Cal 11 Galv (Configuración Global)
- OFF (default): nest/renest de `0.11811_GALVANIZADO` con el motor del
  selector, igual que antes. ON: motor experimental giga_cal11_galv.
- Contraseña para activar, como cobre/Spark. Guardar y aplicar.

### 2026-08-19b — GIGA: un par I, luego llenar la L
- 4 I primero llenan el alto y dejan ~62% (metal I + patio). Eso no es techo:
  la L (arriba del par + derecha) y canales que sí caben con kerf suben DIR.
- Orden: 1×101 + 1×102, barras/BKT, resto de I al final. Fill cavidades ON.

### 2026-08-19a — GIGA: pool mixto, no zona 2 vacía
- El clip de zona 2 anclaba MC en (0,0); el patio derecho colocaba 0 y
  partía VFM/BKT (DIR 29% / 87 placas). Ahora: un pase MC, I-primero,
  anclaje al patio, combinado est+acc. Candado mixto en la misma hoja.

### 2026-08-18v — GIGA: empalme I + MC zona 2
- VFM-20-101/102 son perfiles en I (alma ~4", canales ~3.7"). Lite los
  apilaba como ladrillos (~56%). GS 3.61"+kerf no cabe en el canal.
- Packer: empalme analítico 101+102 (pestaña en canal) en tira izquierda;
  segundo MC en el rectángulo libre (~40"). Sin NFP de Burke.
- Candado: `test_empalme_i_mas_bajo_que_apilado`. Cerrar ANS y reabrir.

### 2026-08-18u — GIGA Cal 11: MC rápido, no NFP de Burke
- `giga_cal11_galv` con 540 piezas se iba a minutos (NFP Burke, UI muda).
- Ahora: 1 pase MC (kernel Lite) + orden de marcos + pasillos. Segundos.
- Hay que **cerrar ANS** y recompilar/cargar el `.pyd`.

### 2026-08-18t — Motor nativo GIGA Cal 11 Galv (`giga_cal11_galv`)
- `0.11811_GALVANIZADO` ya no es un overlay sobre Lite: es un motor
  compilado (`packer_giga_cal11.cpp` → `empaquetar_una_hoja_giga_cal11`).
  BLF+NFP de un pase, marcos VFM/HFM primero, luego pasillos.
- Sigue oculto: no selector, no compare. Renest de ese calibre: solo
  «Renestear». Kerf/margen de tabla 0.150 / 0.250.
- Candado: `test_giga_cal11_galv.py`. Recompilar `.pyd` y **cerrar ANS**.

### 2026-08-18s — Overlay oculto Cal 11 Galv (pasillos VFM)
- Superado por 2026-08-18t: el overlay Lite se reemplazó por motor nativo.

### 2026-08-18r — El registro mandaba 0.250−kerf/2 (Galv 4.85 mm)
- Preflight de 1 pieza ya salía a 4.85 mm: `engine_registry` restaba
  `kerf/2` al margen (0.175" con kerf 0.150; 0.125" → 3.3 mm en Cal 0.25).
  El pokayoke mide metal. Los tests al packer directo no pasaban por ahí.
- El packer recibe **0.250"**. Cerrar ANS y reabrir (cambio Python).

### 2026-08-18q — PO GAM SWO-022: observaciones ContPAQ > 60 chars
- El pedido de placas SÍ debe crearse sin lista de largos. InsertaPO falló
  HTTP 500 / SQL 8152: el detalle
  `9919-11CABINET/GIGA | 9919-14CABINET/GIGA | 9919-15CABINET/GIGA | SWO: S.W.O 22`
  (79 chars) iba a `COBSERVACIONES` (varchar ~60). CTEXTOEXTRA3 ya se recortaba.
- InsertaPO recorta ambos campos a 60. El PDF/correo sigue con el detalle completo.
- ANS no omite la PO por falta de MRL: las placas se compran igual.

### 2026-08-18p — SWO sin lista de largos no se congela
- SWO-022 (gabinetes 9919-11CABINET / 14 / 15) se colgó 10+ min al exportar:
  AutoDXF no existe junto a la SWO y `_buscar_carpeta_job_corporate` hacía
  `rglob` de todo `TANKS` en SMB. Los DXF ya estaban en disco; el commit de
  PQART esperaba esa búsqueda.
- Sin CSV de perfiles no es limitante: aviso y se omite MRL de largos.
  La PO GAM de **placas** sí se crea.
- Búsqueda corporativa solo 3 niveles (producto/cliente/job), nunca `rglob`.
- Candado: `test_export_sin_lista_largos.py` (rglob prohibido).

### 2026-08-18o — El nest nace a 0.250" de metal (sin empujar)
- 4.86 mm era el globo de kerf pegado a 0.250": el metal quedaba
  `0.250" − kerf/2`. Placa rectangular ahora tiene límite EXACTO para el
  metal (no Clipper, no globo). Compact/slide no baja de ese límite.
- Transfer WO usa AABB de metal contra la placa. Default margin de
  `empaquetar_una_hoja_mc` = tabla 0.250".
- Candado: Ultra + Lite + gravity ≥ 0.250" con SIVC-113.

### 2026-08-18n — Ultra de planta no usa ArgaNestCore (Galv 4.86 mm)
- El diálogo pokayoke de `0.105_GALVANIZADO` / `GENE-SIVC-40-40-113` a
  **4.86 mm** (tabla 0.250" = 6.35 mm) no era el packer `algorithm_cpp`
  (ese DXF sale legal ≈6.6 mm). ANS arrancaba con `ARGA_NEST_CORE=1` y
  el wrapper Ultra desviaba el pack de hoja a ArgaNestCore, que no tiene
  la TABLA GAPS placa→pieza.
- Ultra packea otra vez por `algorithm_cpp`. Default `ARGA_NEST_CORE=0`.
- Candado: `test_ultra_pack_not_core.py` (CORE=1 en env, metal ≥0.250").

### 2026-08-18l — Margen 0.250" se mide en el METAL, no en el buffer
- Galv 6.29 mm se repitió con el packer nuevo: AABB/slide usaban el
  polígono inflado (kerf + Simplify 0.1 mm). El pokayoke mide el metal.
- Coloca y compacta contra bounds de `poly` (metal). Sin empujar después.
- Candado: pico 0.08 mm a la izquierda queda ≥0.250".

### 2026-08-18k — Packer respeta 0.250" de placa (sin empujar después)
- Galv 0.105 a 6.29 mm del borde (tabla 6.35 mm) no se “arregla” empujando:
  el nest debe nacer legal. Causa: packer C++ devolvía 0.1 mm de margen
  (Clipper grow-back + AABB `margin-0.1`).
- Packers: margen exacto de tabla. Pokayoke AVISA `margen_placa`; no empuja.
- Candado: 6.29 mm sigue ilegal; gravity no baja de 0.250".

### 2026-08-18j — Nest bien desde el primer pase (no “corregir” después)
- El acomodo “culero” (diagonales, huecos, P28/P64 reinyectadas) era
  post-proceso: expel+reinject + densify + Venom 2º explore sobre un nest
  ya hecho, más Ultra renest a 30° sin parar.
- Renest Ultra: 90° (ortogonal), se detiene al nest completo. Pokayoke
  solo nudge (~mm). Sin densify/hole-fill/Venom al recalcular placa.
- Tabla sigue igual: pieza↔pieza ≥ calibre; placa→pieza 0.250".
- Candado: `permitir_expulsar=False` no reubica ni expulsa.

### 2026-08-18i — Renest ya no restaura el nest viejo
- Ultra sí armaba nido nuevo; pokayoke expulsaba 1 y el fallback DXF-en-pose
  **dejaba el acomodo anterior**. Por eso “empieza pero no cambia”.
- Expulsada se reinyecta junto al origen. Sin fallback al nest viejo.
- Renest Ultra ignora rotación 90° del caller (usa 30°).

- El “amontonado” era Ultra fast-first: 1 generación, rotación 90°, y el
  repair pegaba al bbox global (X=900). Eso no es nesteo.
- En renest Ultra usa 30°, ≥4 gens. Repair solo hop local / acordeón (~mm).
- Tabla: mínimo pieza↔pieza y 0.250" placa; el motor vuelve a compactar.

- El acomodo “culero” era ``[POKA-KERF-RELOC] idx=0 -> (900, 1214, 1427)``.
- La tabla solo pide ~0.5 mm extra (0.355→0.375). Se abre en acordeón o
  pegado al cluster; se quitó la grilla por toda la placa.

- El acomodo “culero” (piezas sueltas, 17–18% en 120") era Ultra fast-first +
  Venom OFF: el pulidor no corría y nadie juntaba al origen.
- `densificar_nido_en_placa` (band-close + gravedad a **toda** la placa) corre
  en todo renest, incluido DXF-en-pose. Tabla: nunca menos; más sí.
- Candado: 3 piezas a 1800 mm se juntan cerca del origen con kerf Cal 2.

- El espejo dejaba las 10 P64 en 18.3% con «0 mejoras» porque el repair
  **expulsaba 3** (columna trabada en el 0.250" de placa) y el fallback DXF
  restauraba el acomodo original.
- Ahora: cadena se abre aunque acerque al vecino; si no hay holgura, **reubica
  en el hueco de la misma placa**. Ultra además puntúa bbox más compacto.
- Candado: 4 P64 en columna que llena los 48".

- Contrato: **placa→pieza = 0.250" (nunca menos)**; **pieza→pieza ≥ tabla** (más sí, menos no).
- Ultra Cal 2 dejaba P64 a 0.355" pegadas al borde; el split simétrico empujaba
  la del rincón fuera del 0.250" y expulsaba 3 → “espejo no debería requerir hoja extra”.
- Nudge ahora gasta la holgura de quien mira al centro. Fallback DXF-en-pose ya no
  exige `autodxf_pendiente` y repara kerf sin expulsar. Candado 2×2 en el margen.

### 2026-08-18c — Espejo Cal 2: abrir par (0.355→0.375), no expulsar
- Renest/espejo Ultra dejó P64×P64 a 0.355" (tabla Cal 2 = 0.375"). El repair
  expulsaba 3 y el diálogo decía “no caben en la misma placa”.
- Nudge ahora prueba ejes + **abrir ambos lados**. Candado 2×2 P64.

### 2026-08-18b — TABLA GAPS al 100% (sin holgura 0.025")
- El pokayoke restaba ``tol=0.025"``: aceptaba 0.225" / 0.200" de margen cuando
  la tabla pide **0.250"**.
- Epsilon geométrico ``0.002"`` (~0.05 mm). Repair empuja al kerf **completo**.
- Candado: gap 0.240" y margen placa 0.200" fallan en Cal 0.5.

### 2026-08-18a — Repair kerf apunta al par geométrico (no homónimo)
- Diálogo “no pudo acomodar… P03”: Lite sí empaquetó 0.5; el repair buscaba
  `P13×P32` por **nombre** y movía las copias lejanas (qty 3 y 6). Tras 40
  rounds fallaba y **vaciaba** la hoja → grupo rechazado.
- Ahora elige el par **más cercano** por geometría; si el repair no cierra,
  conserva las piezas colocadas (no `marcar_pack_fault` vacío).
- Candado homónimos en `test_repair_kerf_nudge`. Paridad ANS C++.

### 2026-08-17q — Repair kerf empuja antes de expulsar (nest ya no agujereado)
- Causa del nest “culeriso” en 0.5: Lite dejaba ~0.183"; pokayoke **expulsaba
  13+9 piezas** y dejaba huecos (log `POKA-KERF-REPAIR`).
- `reparar_separacion_minima_hoja` ahora **empuja** el par ~mm hasta kerf tabla;
  solo expulsa si no hay espacio. Log `[POKA-KERF-NUDGE]`.
- Hole-fill mini-sheet: primer guest centrado en el orificio (no esquina BLF).
- Candado `test_repair_kerf_nudge`. Paridad ANS C++.

### 2026-08-17p — Distancia exacta ≥ kerf tabla en todos los movers
- Causa WO62176: `buffer(kerf/2)` + paso 2 mm dejaba metal a ~0.17" (tabla 0.250").
- Band-close `_collides`, hole-fill `_place_ok` / pared, Venom AI gravity+candidates,
  compact Lite: `distance()` metal↔metal ≥ kerf completo; paso fino ≤0.5 mm.
- Candado `test_exact_kerf_movers` (gap 0.17" rechazado). Paridad ANS C++.

### 2026-08-17o — Fix KeyError poly tras repair kerf (0.375 incompleto)
- Tras expulsar FPP/P08 por gap, se reinyectaban al pool **sin** ``poly`` →
  `SAFE-EMPAQUE-EXCEPTION: 'poly'` y el grupo quedaba con 35 faltantes.
- `_piezas_expulsadas_a_pool` normaliza a formato pack; inventario incompleto
  ya no descarta las hojas nestéadas. Candado `test_expulsadas_a_pool_poly`.

### 2026-08-17n — Nest 0.375 no tumba grupo por kerf en orificio
- Causa WO62176: Lite dejó `P05×FPP-PELSUE` a ~0.031" (tabla 0.250"); pokayoke vaciaba la 2ª placa y **borraba** el grupo aunque la 1ª placa (PLC082) ya estaba bien.
- Fix: `reparar_separacion_minima_hoja` expulsa guests/violadores y conserva hoja; si ya hay hojas OK, no `return error` del grupo.
- Candado `test_repair_kerf_hole_guest`. Paridad ANS C++.

### 2026-08-17m — Todos los movers respetan TABLA GAPS
- Contrato: **entre piezas** = kerf de calibre; **placa→pieza** = **0.250"** — motores, hole-fill, compact/band-close/gravity, pockets Venom, drag canvas, Ed. libre, renest/cambiar placa.
- Pokayoke relee tabla (UI 0.15 no gana a Cal 2 → 0.375). Renest usa `_gaps_tabla_para_renest`.
- Plate inset = margen tabla (no kerf/2). Candado `test_movers_respetan_tabla_gaps`. Paridad ANS C++.

### 2026-08-17l — Pokayoke kerf fail-closed (calibre 2 / APEX a piso)
- `validar_separacion_minima_hoja`: rechaza nests con gap pieza↔pieza &lt; kerf de tabla (tol 0.025").
- Enganche en `_safe_empaquetar_una_hoja_mc` + post-compact. Log `[KERF-TABLA]` / `[POKA-KERF-FAIL]`.
- Defaults UI margen → 0.250". Candado `test_kerf_calibre2_pokayoke` (0.15 vs 0.375).

### 2026-08-17k — Lite void-first + Pedir largos + gaps planta
- Void-first Lite (orificios antes del MC) + expand cargo.
- Pedir/No de Nesteo de largos: persistencia en `_config/largos_mrl_exclusiones.json`; recalcular plan ya no borra marcas.
- Defaults `CUT_GAP_RULES` alineados a foto planta (0.250" entre piezas desde Cal 0.250"). Candados gaps + `test_largos_pedir_persist`.

### 2026-08-17j — Lite hole-fill = orificio como mini-placa

- Meta: acomodo manual (varios guests por anillo, kerf completo).
- `pack_cavities_as_mini_sheets`: BLF dentro del orificio shrinkeado;
  reempaca los ya dentro + mete outsiders que quepan.
- Fill 1× al final del renest (no en cada shot). Candado ≥3 guests/anillo.
- Paridad ANS C++.

### 2026-08-17i — Lite hole-fill denser + kerf full en bridas

- 2 pases: compactar a esquina → meter más guests → strip pack.
- Orificios cerrados: kerf **completo** guest↔guest y guest↔anillo
  (antes half a pared por open_profile engañoso en anillos).
- Grilla más fina; sembrar occupants ya dentro. Candado mide gaps.
- Paridad ANS C++.

### 2026-08-17h — Lite hole-fill también en renest placa/calibre

- Causa: renest pasa `mc_iterations` → Lite “shot único” salía sin
  compact/hole-fill; el bloque `[RENEST-VENOM]` no-op con Venom OFF.
- Fix: `_lite_apply_post_pack` en shot único; `apply_lite_hole_fill` en
  renest placa (`_mixin_nesting_calc`), `recalcular_hoja_full` y redistribución.
- Candado ampliado. Paridad ANS C++.

### 2026-08-17g — Lite hole-fill (nest-in-hole barato)

- Post-pase Lite reusa `fill_host_cavities`: guests de la misma hoja
  entran en orificios grandes del host (bridas / anillos).
- Default ON (`ARGA_LITE_HOLE_FILL=1`); opt-out `=0`. Independiente de Venom.
- Hooks: fin de `empaquetar_una_hoja_arga_lite`, post-densify y batch final.
- Candado `test_lite_hole_fill_brida.py`. Paridad ANS C++.
- Build: sin módulos nuevos (`venom_hole_fill` ya empaquetado).

### 2026-08-14s — Switch cobre: forzar DXF+STEP sin RTZCU

- Configuración Global: switch **Forzar cobre DXF+STEP** (password DyT al
  activar). Persiste en `_config/nest_runtime.json` (`cu_force_dxf_step`).
- ON: desactiva sin_gap / RTZCU / Amada vertical en nest; todas las barras
  cobre van con gap y exportan DXF + STEP. Aplica al nestear/renestear.
- Env override: `ARGA_CU_FORCE_DXF_STEP=0|1`. Candado
  `test_cu_force_dxf_step.py`. Paridad ANS C++.
- Build: sin módulos nuevos; reutiliza `nest_runtime_prefs` ya empaquetado.

### 2026-08-14r — Buzón: vista previa de capturas

- Debajo de la lista de adjuntos se muestra la imagen seleccionada.
- Al pegar/capturar se selecciona sola y se ve antes de abrir Outlook.

### 2026-08-14q — Buzón: pegar captura Shift+Win+S

- En la descripción del Buzón, Ctrl+V con imagen del portapapeles la agrega
  como adjunto (`captura_portapapeles_N.png`). También hay **PEGAR CAPTURA**.
- Sin Power Automate / canal avanzado.

### 2026-08-14p — Buzón solo Outlook (sin canal Premium)

- Se quitó **CANAL AVANZADO** / Power Automate del diálogo: no hay licencia.
- El Buzón solo abre Outlook con destino, texto, contexto y adjuntos.

### 2026-08-14o — Buzón vía Outlook (sin Premium)

- Power Automate HTTP resultó Premium; el envío diario ya no depende de eso.
- **BUZÓN → ABRIR EN OUTLOOK** crea un borrador con destino
  `jose_rosales@grupoarga.com`, descripción, contexto técnico y capturas
  adjuntas (COM Outlook / PowerShell). El usuario solo pulsa Enviar.
- Adjuntos se materializan en `_tmp/buzon/<timestamp>/` para que no se borren
  al cerrar el diálogo. Docs actualizadas. Paridad `ANS C++`.

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

### 2026-08-14p — export rectilíneo con escalones + cantidades PLACA/NEST/REQ

**Export (bloqueador, GENE-OP-1010-211):** el log decía `outer=1 → OK` y
luego fallaba la validación con 48 LINE duplicados y delta 8.13 mm vs
0.64 esperado. El perfil es un LWPOLYLINE 100 % ortogonal con notches;
`_ring_is_rectilinear` exigía "vértices en el borde del bbox" y lo
rechazaba. El export caía a `export_ring_native`, inventaba ARC de
~26 mm y escribía el contorno ~8 veces. Fix: rectilinear = segmentos
horizontales/verticales; `_outer_export_line_exact` sin tope de 8
vértices; bbox de ARC en la validación usa el sweep real (no el círculo
completo).

**Cantidades (no era pérdida):** PARTS muestra TOTAL QTY del lote
(X3 → 6); la tabla CAD solo contaba la placa activa (H1 → 2). Las otras
4 están en otras placas.

Candados: `test_plasma_export_rectilineo_escalones.py`. Build sin cambios.

### 2026-08-14q — separación pieza-pieza reprobaba nests válidos

Con los ARC/duplicados de `2026-08-14p` ya resueltos, GENE-OP-1010-211
seguía abortando el export: `separación pieza-pieza 3.8 mm < kerf nest
7.6 mm` → `plasma: sin contorno exportable desde el nest`. Eran dos
defectos sumados en `validate_plasma_piece`:

1. La separación se medía entre **bounding boxes** axis-aligned. Ese
   perfil tiene escalones y el nest entrelaza sus dos instancias, así
   que las cajas se enciman aunque el metal conserve el kerf completo.
2. Se comparaba contra el **kerf pelado del nest**. Al compensar, cada
   contorno crece `off` hacia afuera, así que la separación real entre
   contornos de corte es `kerf - 2*off` por construcción: exigir `kerf`
   reprobaba todo nest compensado correcto.

Fix: `piece_clearance_record` guarda bbox **y** el contorno
discretizado; el bbox queda solo como filtro barato y el veredicto lo da
la distancia real entre contornos (`_segments_min_distance_mm`, exacta
porque en segmentos disjuntos el mínimo cae en un extremo). El umbral
pasa a `kerf - 2*off`.

**UI de la placa:** la columna vuelve a ser `Cant.` con lo colocado en
esa placa (el desglose `PLACA / NEST / REQ` de `14p` se revierte: no era
lo pedido). Las columnas `L (in)` / `W (in)` medían el polígono del nest
(sin compensar) y por eso decían 42.48 donde PARTS decía 42.51; ahora
suman el desfase por lado y empatan con la pieza tal como se corta.

Candado: `test_plasma_separacion_piezas.py`. Build sin cambios.

### 2026-08-14r — el validador de plasma inventaba kerf y margen (CAUSA RAÍZ)

Tras `14q` el export de H1 (W.O. 44 X3) **seguía** abortando:
`separación pieza-pieza 3.8 mm < mínimo 7.0 mm`. El nest estaba bien; el
que estaba mal era el juez.

`sheet_info` (el dict que `exporter.py` pasa a `validate_plasma_piece`)
llevaba `length`/`width`/`material`/`thickness` pero **no** `kerf_usado`
ni `margin_usado`. El validador caía a defaults hardcodeados:

- kerf → `or 0.3` (0.30"). La TABLA GAPS DE CORTE fija para cal 14
  (0.0747") kerf **0.150"**. Exigía el doble y reprobaba nests válidos.
- margen → `or 0.15`. La tabla fija **0.250"**. Este default era *laxo*,
  así que además habría dejado pasar violaciones reales de margen.

Números del caso, verificados contra el log: cal 0.0747 → kerf 0.150"
= 3.81 mm; mínimo entre contornos compensados = 3.81 − 2(0.318) =
**3.17 mm**; separación real medida = **3.80 mm** → pasa. Con el 0.30"
inventado el mínimo salía 6.98 mm → reprobaba.

Fixes: `sheet_info` propaga `kerf_usado`/`margin_usado` de la hoja; el
validador ya **no inventa** ninguno de los dos (si falta el dato, omite
el chequeo en vez de fabricar un umbral); el margen a placa se compara
contra `margen − off`, porque el contorno compensado se acerca al borde
por construcción igual que se acerca a su vecino. La línea `HOJA` del log
ahora imprime `kerf=` y `margen=` para diagnosticar esto de inmediato.

Candado: `test_plasma_separacion_piezas.py` (incluye los números exactos
de H1 y afirma que el default viejo reprobaba, más que sin kerf real no
se inventa umbral). Build sin cambios.

**Dimensiones de la tabla:** la pieza llega a la escena marcada
`plasma_compensada_manual` pero **sin** `plasma_offset_mm_manual` (se
pierde en el empaquetado; el dump de keys del export lo confirma). Por
eso el arreglo de `14q` no movía el L/W: `off_pieza` quedaba en 0. Ahora
si la pieza está marcada y el mm falta, se recalcula con
`compute_plasma_offset_mm(calibre)` — la misma regla del export — y la
tabla vuelve a empatar con PARTS (42.51 × 40.83).

### 2026-08-17 — plasma: margen/kerf finales y esquinas nativas

Auditoría del archivo real `GIGA FLUIDSTACK - METAL - REV1.arganest`,
H1 / PLC033:

- La tabla estaba correctamente persistida: Cal 0.0747 → kerf `0.150"` y
  margen `0.250"`.
- Pero las piezas del resultado eran perfiles **base** rotados y llevaban la
  bandera plasma por una heurística errónea que comparaba W/H sin normalizar
  la rotación de 90°. El export les agregaba el offset después del nest:
  el CUT_OUTER acababa a 6.017 mm del borde, menor que el margen final
  configurado de 6.350 mm.

Fix: el renesteo de compensación preserva metadata explícito por pieza
(`plasma_compensada_manual`, `plasma_offset_mm_manual`,
`plasma_fuente_ya_compensada`) desde el perfil ya compensado que recibe el
packer; el fallback legacy compara dimensiones ordenadas, por lo que una
rotación no se confunde con offset. La validación de export exige ahora el
**kerf y margen completos** de tabla sobre CUT_OUTER final (ya no descuenta
el offset). Un `.arganest` legacy base+offset se rechaza fail-closed y el
diálogo explica que hay que renestear con la compensación activa, en vez de
mentir `sin contorno exportable`.

**Esquinas:** el DXF H1 exportado contenía `CUT_OUTER: 170 LINE, 0 ARC`.
La causa era una rama que, al reconocer el perfil fuente rectilíneo, forzaba
LINE también sobre los puntos de los joins redondos del OFFSET. Ahora la ruta
específica escribe los segmentos rectos como LINE y convierte sólo los
tripletes consecutivos cuyo radio coincide con el desfase a ARC nativos; no
usa el detector general que antes podía inventar un ARC gigante de ~26 mm y
duplicar el anillo. Candados actualizados:
`test_plasma_export_rectilineo_escalones.py` exige ARC nativo sin LINE
duplicados; `test_plasma_separacion_piezas.py` cubre el H1 legacy y la
persistencia/rotación. Regresiones 27/27. Build sin cambios: no hay módulos,
assets ni imports dinámicos nuevos.

### 2026-08-17f — SWITCH PATCH: arco mayor (bulge>1) ya no espeja el barreno

En PARTS el compensado de SWITCH PATCH 1 “descomponía” la pieza: la muesca
parecía arriba, el agujero verde bajaba y el texto MARK quedaba dentro del
corte. Causa: Processed guarda el `CUT_INNER` como LWPOLYLINE con bulge ≈9.4
(arco reflejo). `_polyline_edges` calculaba el centro con
`h = +sqrt(r²-(c/2)²)` siempre al lado del signo del bulge; en |θ|>180° el
centro va al otro lado (`h = (c/2)/tan(θ/2)` firmado). El wire OCCT partía
del agujero ya espejado y el validador no lo veía porque muestrea ese mismo
wire. También se ignoran LINE de longitud cero al cerrar la polilínea.
`offset2d-v8-bulge-arco-reflejo` invalida el cache. Candado
`test_plasma_switch_patch_muesca.py`.

### 2026-08-17e — SOLERA JACKING PAD: bbox anisotrópico del inglete

PARTS rechazaba triángulos reales con
`PLASMA: el contorno compensado no mide lo esperado (… vs …)`.
La compuerta exigía `AABB_out == AABB_src + 2·offset`, válida solo en
rectángulos alineados con join redondo. Con lados inclinados + inglete el
bbox crece anisotrópico (una punta empuja un eje por encima y el otro puede
quedar ligeramente por debajo) aunque el offset geométrico sea correcto.

`validate_offset_bbox_growth` sustituye la igualdad estricta: exige
crecimiento ~`2·|δ|` con holgura de proyección y techo de inglete
(`_MITER_LIMIT`). Candado `test_plasma_bbox_triangulo_inglete.py`.

### 2026-08-17d — release zip: buzón, gaps, runtime y paridad build

- Versiona en git el lote pendiente del ANS: buzón de soporte, tabla de
  gaps (`cut_gaps_table`), nest runtime / NvidiaSpark, switch cobre
  DXF+STEP, candados asociados y ajustes de cobre/fixtura en UI.
- `tools/build_arga_exe.py` + `config.py`: `cut_gaps_table.json` entra a
  `HIDDEN_IMPORTS`/`CRITICAL`/`defaults/_config` y al seed del data_dir,
  junto a `nest_runtime.json`, para que el .exe de release arranque con
  la tabla oficial de kerf/margen.

### 2026-08-17c — el nest ya inyecta Plasma Compensated y el offset conserva puntas

`2026-08-17b` corrigió la mitad del problema: la rama que clona `Plasma
Compensated` 1:1 existía, pero **nunca corría en la app**.

- `nesting_engine/exporter.py` armaba el placement plasma sólo con `ruta` y
  `compensated_plasma_source`, y `export_plasma_placement` exige
  `plasma_fuente_ya_compensada` + `ruta_plasma`. Un `.arganest` reabierto llega
  sin esas llaves, así que el export recalculaba el offset y escribía el anillo
  muestreado: por eso un `ARC` del compensado aparecía como docenas de LINE en
  el DXF de corte. El test previo pasaba porque fijaba las llaves a mano.
- Nuevo puente `plasma_dxf_export.resolver_fuente_plasma()`: resuelve (y
  regenera si el sidecar quedó viejo) el DXF de `Plasma Compensated` desde
  `ruta` + offset, y devuelve las llaves del placement con
  `plasma_offset_mm=0`. Lo usan los dos armadores de placas (chapa y retazo
  cobre), que antes decidían la fuente por separado.
- `BRepOffsetAPI_MakeOffset` **ignora** `GeomAbs_Intersection`: seguía
  redondeando cada esquina convexa con radio = offset. Se agregó inglete
  analítico en `plasma_occt_offset`: se detecta el filete por radio |delta| y
  centro sobre un vértice vivo del origen, y se recorta contra los tramos
  vecinos (recta∩recta, recta∩círculo, círculo∩círculo) con límite de inglete
  8×offset. La validación de distancia admite la punta hasta
  `delta / cos(giro/2)` sólo en esos vértices, así que sigue rechazando bultos.
- `offset2d-v7-inglete-analitico` invalida los compensados con esquinas
  redondeadas.

Candado `test_plasma_inyecta_compensado_y_esquinas_vivas.py`: una pieza sin
`ruta_plasma` debe resolver el compensado, el DXF final debe traer el `ARC`
nativo (≤5 LINE), sólo la curva del origen conserva bulge, las tres esquinas
rectas caen en su punta a inglete, y ningún armador de placas puede volver a
decidir la fuente por su cuenta. Regresiones 28/28. Build sin cambios: sólo
módulos ya empaquetados.

### 2026-08-17b — geometría de Plasma Compensated es la fuente final

Corrección de semántica solicitada por planta:

- Una esquina `LINE → LINE` (cuadrado, rectángulo, punta) **no se redondea**
  al compensar. Los fallbacks Clipper2 y GEOS pasan de `Round` a `Miter`, y
  OCCT intenta `GeomAbs_Intersection` antes del join Arc. Un radio sólo
  sobrevive si ya era un `ARC`/bulge en el DXF fuente.
- El export plasma final antes ignoraba `ruta_plasma` y volvía a calcular el
  offset desde `ruta` original. Eso explicaba por qué el archivo dentro de
  `Plasma Compensated` no se parecía al DXF final. Ahora, si la pieza fue
  anidada desde `plasma_fuente_ya_compensada`, el export clona ese DXF
  compensado **1:1** en la matriz de placement; no reaplica offset ni aplana
  ARC/bulges.
- Antes de inyectar, `asegurar_dxf_plasma_compensado` verifica el sidecar de
  versión. `offset2d-v6-miter-sharp-corners` invalida el cache anterior para
  regenerar archivos creados con joins Round.

Candado ampliado: `test_plasma_export_rectilineo_escalones.py` exige que
OP-1010-211 no gane ARC en sus puntas y crea un fixture donde un ARC de
`Plasma Compensated` debe llegar al DXF final sin alteración. Regresiones
27/27. Build sin cambios: se modificaron módulos existentes, sin assets,
binarios ni imports dinámicos nuevos.

### 2026-08-14o — export plasma de flat patterns + rotación que trasladaba la msp

Dos bugs independientes reportados en la misma sesión de planta (job 62177).

**1. Export plasma reventaba en toda pieza de chapa (BLOQUEADOR).**
`<PIEZA>_PLASMA: plasma: sin contorno exportable desde el nest`.
`_plasma_desfase_clase` comparaba la capa por **igualdad exacta** contra
`{"CUT_OUTER","OUTER","CORTE_EXTERNO","IV_OUTER"}`, pero Inventor exporta
los flat patterns como `IV_OUTER_PROFILE` / `IV_INTERIOR_PROFILES`. Ninguna
clasificaba → `by_clase["outer"]` vacío → `stats["outer"] == 0` → el export
abortaba, aunque el nest sí reconocía la pieza porque
`geometry_parser._clasificar_capa` compara por **subcadena**. Dos caminos
calculando lo mismo distinto (regla 9 de `AGENTS.md`).
Fix: `_plasma_desfase_clase` pasa a match por subcadena alineado con el
nest, con orden mark → inner → outer. Se mantienen fuera a propósito la
capa por defecto `"0"` (el nest la trata como contorno; en plasma trae
marcos y notas) y las capas de marcado. El cambio es un superconjunto
estricto del comportamiento anterior: sólo clasifica capas que antes caían
en `None`.
Verificado con el DXF real: `outer=1, inner=7`, salida normalizada a
`CUT_OUTER`/`CUT_INNER`, crecimiento exterior `0.635 mm` = `2 × 0.3175`
(y agujeros encogiendo lo mismo).
Nota: el guard contra doble compensación (`ya_comp → off_export = 0.0` en
`exporter.py`) sigue intacto; la ruta que fallaba es la que aplica el
desfase sobre el DXF **original** de Inventor.

**2. `rotate_modelspace` trasladaba la pieza al rotar.**
ezdxf usa vectores fila (`v' = v @ M`), así que en `A @ B` se aplica **A
primero**. La matriz estaba compuesta como `translate(+c) @ rotate @
translate(-c)`, o sea al revés: rotaba bien pero dejaba la msp trasladada.
La traslación era **invisible** porque `fit_view` reencuadra — hasta que se
empezó a superponer `outer_rings` (calculado con `rotar_punto`, correcto) y
aparecieron **dos contornos rojos separados** en el visor. Afectaba a las
dos rutas del loader (LINE+ARC y LWPOLYLINE) y a ambas copias de la
función (`dxf_qt_renderer.py` y `dxf_ezdxf_draw.py`).
Fix: `Matrix44.chain(translate(-c), z_rotate, translate(+c))`.
Medido: box 6.53×3.57 a 90° pasaba de `(-8.62,1.48,-5.05,8.01)` (mal) a
`(1.48,-1.48,5.05,5.05)` (bien, = lo que da `rotar_punto`).
Esto corrige de paso el candado `14n`, que comparaba anillo contra msp y
por tanto era **circular**: ambos podían estar mal de la misma forma.

Candados:
- `tests/native/test_plasma_export_capas_inventor.py` (nuevo): clasificación
  de capas Inventor + históricas; capas que NO deben cortarse (`0`, `IV_BEND`,
  `IV_TANGENT`, marcado) siguen fuera; repro del export con flat pattern
  sintético exigiendo `outer>0`, capas normalizadas y magnitud del desfase
  (`+2·off` afuera, `−2·off` en agujeros).
- `test_visor_plasma_alineacion.py` reforzado: cubre también LWPOLYLINE y
  añade un check **absoluto** —rotar sobre el centro debe conservar el
  centro— en vez de sólo comparar anillo contra msp.
- Ambos verificados como fallando contra el código viejo antes de fijarlos.
- Regresiones 24/24 en ambos proyectos.

Build: sin cambios necesarios (no hay módulos, binarios ni assets nuevos;
sólo ediciones a archivos ya empaquetados y tests, que no entran al `.exe`).

### 2026-08-14n — énfasis plasma alineado y label cosmético

Dos regresiones destapadas por el fix `14m` (activar `outer_rings` para
LINE+ARC):

1. **Doble rotación del anillo.** `_agregar_shapes_desde_line_arc`
   sampleaba las entidades DESPUÉS de que `rotate_modelspace` ya las había
   rotado en la msp, y luego aplicaba `rotar_punto` otra vez. La pieza se
   pintaba en la rotación pedida (render_modelspace) y el OUTER rojo en el
   doble (emphasize) → aparecía desplazado y espejado tras ROTAR 90°.
   Fix: eliminar el `rotar_punto` — las entidades ya vienen transformadas.
2. **Label plasma gigantesco.** `set_plasma_overlay` y
   `emphasize_plasma_outers` construían un `QGraphicsSimpleTextItem` con
   `font.setPointSize(10)` sin `ItemIgnoresTransformations`. Los 10 pt se
   interpretaban en unidades de escena (pulgadas), así que la etiqueta
   "COMPENSADA +0.0125\"" salía de ~10 in de alto, inflaba el sceneRect y
   `fit_view` hacía zoom-out extremo → la pieza quedaba diminuta.
   Fix: un helper `_agregar_label_plasma` con el flag cosmético; el texto
   se pinta siempre a la misma cantidad de píxeles y no altera el fit.

Verificación end-to-end con GENE-BKT-101:
- Rotación 0/90/180/270 → bbox del anillo == bbox real de la msp (delta 0
  en las 4 rotaciones).
- Label no altera `fit_view`; la pieza mantiene su tamaño esperado.

Candado `tests/native/test_visor_plasma_alineacion.py`:
- DXF sintético Inventor, ejecuta las 4 rotaciones y compara bboxes.
- AST checks: `_agregar_label_plasma` existe y usa
  `ItemIgnoresTransformations`; `set_plasma_overlay` y
  `emphasize_plasma_outers` delegan al helper (fuente de verdad única).
- Regresiones 23/23 en ambos proyectos.

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
