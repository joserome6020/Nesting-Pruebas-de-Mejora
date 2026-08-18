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
        "test_export_sin_lista_largos.py",
        "2026-08-05a/06b/18p - job/SWO sin CSV/AutoDXF no tumba export ni dispara PO; sin rglob TANKS",
    ),
    (
        "test_job_nombre_vsm.py",
        "2026-08-06c - GIGA BOARD 5 ↔ GIGABOARD5 resuelve carpeta VSM con AutoDXF",
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
        "test_lite_hole_fill_brida.py",
        "2026-08-17g - Lite hole-fill denso en brida + kerf completo guest↔guest/host",
    ),
    (
        "test_ultra_pack_not_core.py",
        "2026-08-18n - Ultra no empaca por ArgaNestCore (Galv SIVC-113 a 4.86 mm)",
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
