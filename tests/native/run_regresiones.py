#!/usr/bin/env python
"""Regresiones de logica de negocio: cada bug corregido deja aqui su candado.

No requiere el core C++ compilado ni conexion a PostgreSQL: son pruebas puras,
pensadas para correrse SIEMPRE antes de cerrar una sesion o publicar un build.

    py -3.14 tests\\native\\run_regresiones.py

Al corregir un bug, agrega su test a REGRESIONES con el caso real que lo motivo.
Un bug sin candado aqui es un bug que va a volver.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]

REGRESIONES = [
    (
        "test_correo_po_nombre_reporte.py",
        "2026-08-31b - PO ContPAQ: nombreReporte vacío = correo no enviado (GAM 13040)",
    ),
    (
        "test_mrl_swo_plan_referencia.py",
        "2026-08-31 - SWO MRL: validar con el mismo plan del modal (no regenerar 15→14)",
    ),
    (
        "test_step_mark_fast_defaults.py",
        "2026-08-28f - STEP planta: PIECE_ONESHOT multi-tool (no chunks lentos)",
    ),
    (
        "test_limpiar_nesting_previo.py",
        "2026-08-28e - reexport WO/SWO limpia NESTING previo (sin DXF huérfanos)",
    ),
    (
        "test_visor_dense_lwpoly.py",
        "2026-08-26 - visor: LWPOLY densas (SPLINE procesadas) sin bloquear UI",
    ),
    (
        "test_processed_layers_spline.py",
        "2026-08-26 - AutoDXF Processed Files: SPLINE Inventor → CUT_OUTER/CUT_INNER",
    ),
    (
        "test_dxf_local_staging.py",
        "2026-08-25 - DXF UNC/red → %TEMP% antes de ezdxf/OCCT (Crear STEPs estable)",
    ),
    (
        "test_occt_inner_per_piece.py",
        "2026-08-27/25 - OCCT: huecos anillo + CUT_INNER solo de su pieza (H4)",
    ),
    (
        "test_crear_steps_motor_choice.py",
        "2026-08-24 - Crear STEPs / Export 3D: OCCT único en UI (FreeCAD solo env legacy)",
    ),
    (
        "test_release_engine_step_paths_packaged.py",
        "2026-08-21d - Crear STEPs: engine.step_paths empaquetado en Release (CAD OCCT)",
    ),
    (
        "test_freecad_step_progress_folder.py",
        "2026-08-21c - FreeCAD STEP: barra % por conteo de .step vigentes en carpeta",
    ),
    (
        "test_crear_steps_occt_line_arc.py",
        "2026-08-21b - Crear STEPs OCCT une LINE/ARC nest; DXF 1:1 intacto (S.W.O 27)",
    ),
    (
        "test_step_feedstock_mvp.py",
        "2026-08-21 - complemento STEP→DXF en AutoDXF/FROM_STEP (prefs + discover + placa)",
    ),
    (
        "test_step_universal_sin_camas_despachador.py",
        "2026-08-20m - Crear STEPs/despachador: 1 STEP plano sin Cama A/B ni offsets",
    ),
    (
        "test_export_sin_lista_largos.py",
        "2026-08-05a/06b/18p - job/SWO sin CSV/AutoDXF no tumba export; sin rglob TANKS",
    ),
    (
        "test_job_nombre_vsm.py",
        "2026-09-03d - VSM/espacios + SWO-058 job 25432 ATC vs TANKS elige AutoDXF con piezas",
    ),
    (
        "test_renest_galv_incompleto.py",
        "2026-08-06d - renest de calibre Galv incompleto recupera piezas desde PARTS",
    ),
    (
        "test_transfer_cross_wo_identidad.py",
        "2026-08-07a - mudar pieza WO→WO no pierde identidad tras desacoplar deepcopy",
    ),
    (
        "test_wo_gemelas_sin_replica.py",
        "2026-08-07b - WOs independientes: sin réplica automática a gemelas / aliasing",
    ),
    (
        "test_transfer_pool_doble_descuento.py",
        "2026-08-07c - muda cross-WO no descuenta 2× del pool ni borra placa del donante",
    ),
    (
        "test_largos_swo_factor.py",
        "2026-08-05b - largos de una SWO se multiplican por WO (X3+X3+X3+X2), no por lote_k",
    ),
    (
        "test_largos_mapa_comercial.py",
        "2026-08-06a - mapa comercial 480->240 no debe omitir piezas (SWO-003 ANG037)",
    ),
    (
        "test_amada_fixtura_catalogo.py",
        "2026-08-06e - Amada elige Fixtura 2 (28.95) cuando es la más justa",
    ),
    (
        "test_amada_export_sin_fixtura.py",
        "2026-08-27 - AMADA/FIXTURA: barrenos fuente inch→mm, LIMMAX≠A4, join +10\" sin MARK",
    ),
    (
        "test_cyptube_vertical_split.py",
        "2026-08-28 - CyPTube vertical: DXF _Corte/_Marcaje + JSON A_mm=ancho+0.2",
    ),
    (
        "test_export_dxf_count_estimate.py",
        "2026-09-01 - barra export DXF: total real (1 Corte si cu_sin_marcaje)",
    ),
    (
        "test_cyptube_bridge.py",
        "2026-09-01/09-04 - CypTube Modo B + python_exe usable en ANS .exe (no frozen)",
    ),
    (
        "test_ui_scale.py",
        "2026-09-01 - UI scale: factor≤1 sobre 1920x1080; diálogos caben en pantalla",
    ),
    (
        "test_swo_autodxf_item_match.py",
        "2026-08-28d - SWO: DXF exacto (TAPA≠TAPA 2) + reproceso conserva W.O.__",
    ),
    (
        "test_amada_validacion_barrenos.py",
        "2026-08-26 - Amada ESP. PARTS: solo valida ancho 5\" (barrenos/largo fuera por ahora)",
    ),
    (
        "test_auto_setup_dependencies_internal_modules.py",
        "2026-08-13 - build no trata `arga_nest_core`/`engine` como paquetes pip",
    ),
    (
        "test_ezdxf_thread_lock.py",
        "2026-08-13 - race audit+render ezdxf crashea .exe en Py3.14; EZDXF_LOCK serializa",
    ),
    (
        "test_cut_gaps_table.py",
        "2026-08-13d - tabla de gaps por calibre conserva kerf/margen y bloquea calibres sin regla",
    ),
    (
        "test_plate_margin_constant.py",
        "2026-08-13g - placa→pieza es 0.250 in final y no suma medio kerf",
    ),
    (
        "test_nvidia_spark_prefs.py",
        "2026-08-13e - NvidiaSpark inicia Local; exportar_a_servidor persiste en nest_runtime.json",
    ),
    (
        "test_largos_pedir_persist.py",
        "2026-08-17k - Pedir/No de Nesteo de largos persiste en disco y sobrevive recalcular plan",
    ),
    (
        "test_kerf_calibre2_pokayoke.py",
        "2026-08-17l - calibre 2 exige 0.375in; pokayoke rechaza nests a 0.15in (APEX/piso)",
    ),
    (
        "test_movers_respetan_tabla_gaps.py",
        "2026-08-17m - movers/renest/drag/band-close respetan TABLA GAPS (no UI 0.15 en cal 2)",
    ),
    (
        "test_repair_kerf_hole_guest.py",
        "2026-08-17n - pokayoke kerf repara expulsando guest en orificio; no tumba toda la hoja",
    ),
    (
        "test_repair_kerf_nudge.py",
        "2026-08-17q - repair kerf empuja 0.18→0.25in antes de expulsar (evita nest agujereado)",
    ),
    (
        "test_packer_metal_plate_margin.py",
        "2026-08-18l - packer C++ coloca el metal ≥0.250in de placa (no el buffer de kerf)",
    ),
    (
        "test_ultra_plate_fit_metal_not_kerf.py",
        "2026-09-03c - Ultra cupo en placa = metal+margen (no kerf ENTRE PIEZAS; PLC058 120×48)",
    ),
    (
        "test_expulsadas_a_pool_poly.py",
        "2026-08-17o - piezas expulsadas por kerf regresan al pool con poly (no KeyError)",
    ),
    (
        "test_exact_kerf_movers.py",
        "2026-08-17p - band-close/hole-fill usan distancia exacta ≥ kerf tabla (no buffer ~0.17\")",
    ),
    (
        "test_plasma_compensation_persistence.py",
        "2026-08-13f - mover, cambiar placa o renestear conserva compensación de PARTS",
    ),
    (
        "test_plasma_export_solo_sin_duplicar_laser.py",
        "2026-08-28g - piezas compensadas sin flag de hoja: solo Robot Plasma (no DXF láser duplicado)",
    ),
    (
        "test_renest_calibre_and_worker_window.py",
        "2026-08-14 - renest calibre define conteos y worker no abre consola CUI",
    ),
    (
        "test_plasma_offset_autodesk.py",
        "2026-08-14c - offset plasma tipo Autodesk: no deforma U/keyhole ni usa largest-wins",
    ),
    (
        "test_plasma_occt_offset.py",
        "2026-08-14g/i/j - OFFSET con cascada OCCT+Clipper2 (motor FreeCAD Path/CAM)",
    ),
    (
        "test_orientacion_corte_bloqueada.py",
        "2026-08-14e/i - BLOQUEAR ORIENTACIÓN fija vista PARTS y persiste con plasma",
    ),
    (
        "test_tabla_gaps_todos_los_motores.py",
        "2026-08-14k - TABLA GAPS DE CORTE: todos los motores usan 0.250 placa→pieza y kerf por calibre",
    ),
    (
        "test_visor_plasma_rotacion.py",
        "2026-08-14l - ROTAR 90° de pieza plasma-compensada conserva el énfasis rojo y '+X\"'",
    ),
    (
        "test_visor_line_arc_outer.py",
        "2026-08-14m - visor arma outer_rings desde LINE+ARC (piezas Inventor IV_OUTER_PROFILE)",
    ),
    (
        "test_visor_plasma_alineacion.py",
        "2026-08-14n - énfasis plasma alineado con la pieza en toda rotación + label cosmético",
    ),
    (
        "test_plasma_export_capas_inventor.py",
        "2026-08-14o - export plasma reconoce IV_OUTER_PROFILE/IV_INTERIOR_PROFILES (flat pattern chapa)",
    ),
    (
        "test_plasma_export_rectilineo_escalones.py",
        "2026-08-14p - perfiles rectilíneos con escalones no inventan ARC ni duplican LINE (OP-1010-211)",
    ),
    (
        "test_plasma_separacion_piezas.py",
        "2026-08-14q/r - kerf/margen reales de la hoja al validar plasma; separación por geometría real",
    ),
    (
        "test_plasma_inyecta_compensado_y_esquinas_vivas.py",
        "2026-08-17c - el nest inyecta Plasma Compensated 1:1 (arcos nativos) y el offset conserva puntas vivas",
    ),
    (
        "test_plasma_bbox_triangulo_inglete.py",
        "2026-08-17e - triángulo SOLERA JACKING PAD no se rechaza por AABB anisotrópico del inglete",
    ),
    (
        "test_plasma_switch_patch_muesca.py",
        "2026-08-17f - SWITCH PATCH: bulge>1 no espeja el barreno debajo de la muesca",
    ),
    (
        "test_cu_force_dxf_step.py",
        "2026-08-14s - switch cobre DXF+STEP fuerza gap, mata RTZCU/Amada nest y exporta STEP",
    ),
    (
        "test_cobre_sin_gap_marks.py",
        "2026-08-26 - cobre sin_gap (CyPTube) exporta capa MARK en el DXF",
    ),
    (
        "test_cyptube_esp_vertical_marks.py",
        "2026-08-26 - barra vertical ESP CyPTube: MARK sí, barrenos no",
    ),
    (
        "test_amada_vertical_exporta_mark.py",
        "2026-08-26 - barra vertical Amada: draw_marks=True (MARK sí, sin barrenos)",
    ),
    (
        "test_cu_sin_gap_escalon_sin_horizontales.py",
        "2026-08-26 - sin_gap escalón: DXF vertical + contorno fuente 1:1",
    ),
    (
        "test_lite_hole_fill_brida.py",
        "2026-08-17g - Lite hole-fill denso en brida + kerf completo guest↔guest/host",
    ),
    (
        "test_ultra_pack_not_core.py",
        "2026-08-18n - Ultra no empaca por ArgaNestCore (Galv SIVC-113 a 4.86 mm)",
    ),
    (
        "test_giga_cal11_galv.py",
        "2026-09-03 - Cal 11 Galv: zig-zag torres VFM (≥3 I) + pull 5ª",
    ),
    (
        "test_plasma_offset_todos_calibres.py",
        "2026-08-19t - offset plasma 0.0625\" por lado en todos los calibres",
    ),
    (
        "test_plasma_transfer_sigue_compensada.py",
        "2026-08-20a - mudar pieza plasma no dobla L/W (77.37≠77.50)",
    ),
    (
        "test_plasma_cotas_cad_precision.py",
        "2026-08-20b - cotas CAD a 0.001 in; misma ruta DXF ⇒ mismo L×W",
    ),
    (
        "test_autodxf_gauge_parity.py",
        "2026-08-20g - AutoDXF Cal DXF = decimal Herinox/ANS (0.11811→0.1196)",
    ),
    (
        "test_parts_catalog_editable.py",
        "2026-08-20q - PARTS Material/Calibre editables empatan catálogo nest",
    ),
]


def main() -> int:
    fallos: list[str] = []

    # Las consolas de Windows suelen venir en cp1252 y los avisos traen acentos.
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    entorno = dict(os.environ, PYTHONIOENCODING="utf-8")

    for archivo, motivo in REGRESIONES:
        ruta = Path(__file__).with_name(archivo)
        print(f"\n=== {archivo} ===")
        print(f"    {motivo}")
        if not ruta.is_file():
            print("    FALTA EL ARCHIVO")
            fallos.append(archivo)
            continue

        res = subprocess.run(
            [sys.executable, str(ruta)],
            cwd=str(RAIZ),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=entorno,
        )
        salida = (res.stdout or "").strip()
        if salida:
            print("\n".join(f"    {ln}" for ln in salida.splitlines()))
        if res.returncode != 0:
            print("    FAIL")
            print("\n".join(f"    {ln}" for ln in (res.stderr or "").strip().splitlines()))
            fallos.append(archivo)
        else:
            print("    PASS")

    print()
    if fallos:
        print(f"REGRESIONES FAIL ({len(fallos)}/{len(REGRESIONES)}): {', '.join(fallos)}")
        return 1
    print(f"REGRESIONES PASS ({len(REGRESIONES)}/{len(REGRESIONES)})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
