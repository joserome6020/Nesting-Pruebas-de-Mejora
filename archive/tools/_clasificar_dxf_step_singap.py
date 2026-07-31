"""Clasifica los DXF de una carpeta NESTING: cuales seran STEP y cuales son sin_gap.

Criterio (mismo que produccion / macro FreeCAD):
  - Un DXF de cobre genera STEP si tiene contornos cerrados en la capa CUT_CU.
  - Un DXF sin_gap (barras pegadas, vertical) NO tiene CUT_CU -> se queda como DXF.
"""
from __future__ import annotations

import glob
import os
import sys

import ezdxf

NESTING = sys.argv[1] if len(sys.argv) > 1 else (
    r"C:\Users\jose_rosales\OneDrive - grupoarga.com\Escritorio\Nesteos Locales"
    r"\GIGA\GIGA FLUIDSTACK - COBRE\MODEL CORE FILES\W.O. 16 X1"
    r"\ARGA MODEL CORE\NESTING"
)

CUT_TYPES = ("LWPOLYLINE", "POLYLINE", "LINE", "ARC", "CIRCLE", "ELLIPSE", "SPLINE")


def _find_dxf_dirs(nesting: str) -> list[str]:
    dirs = []
    for nombre in sorted(os.listdir(nesting)):
        base = os.path.join(nesting, nombre)
        if not os.path.isdir(base):
            continue
        if "CAMA LASER" not in nombre.upper():
            continue  # solo cobre / cama laser produce STEP de barra
        dxf_dir = os.path.join(base, "DXF")
        if os.path.isdir(dxf_dir):
            dirs.append(dxf_dir)
        elif glob.glob(os.path.join(base, "*.dxf")):
            dirs.append(base)
    return dirs


def _layers_con_geometria(path: str) -> dict[str, int]:
    doc = ezdxf.readfile(path)
    conteo: dict[str, int] = {}
    for e in doc.modelspace():
        if e.dxftype() not in CUT_TYPES:
            continue
        capa = str(e.dxf.layer or "").upper()
        conteo[capa] = conteo.get(capa, 0) + 1
    return conteo


def main() -> None:
    if not os.path.isdir(NESTING):
        print(f"[X] No existe la carpeta NESTING:\n    {NESTING}")
        raise SystemExit(1)

    dxf_dirs = _find_dxf_dirs(NESTING)
    if not dxf_dirs:
        print("[X] No se encontraron familias CAMA LASER (cobre) con DXF.")
        raise SystemExit(1)

    total_step = 0
    total_singap = 0
    total_error = 0
    singap_files: list[str] = []
    step_files: list[str] = []

    for dxf_dir in dxf_dirs:
        fam = os.path.basename(os.path.dirname(dxf_dir)) or os.path.basename(dxf_dir)
        archivos = sorted(glob.glob(os.path.join(dxf_dir, "*.dxf")))
        print(f"\n=== FAMILIA: {fam} ===")
        print(f"    carpeta: {dxf_dir}")
        print(f"    DXF encontrados: {len(archivos)}")
        for path in archivos:
            nombre = os.path.basename(path)
            try:
                capas = _layers_con_geometria(path)
            except Exception as e:
                total_error += 1
                print(f"    [ERR ] {nombre}  ({e})")
                continue
            tiene_cut_cu = capas.get("CUT_CU", 0) > 0
            if tiene_cut_cu:
                total_step += 1
                step_files.append(nombre)
                print(f"    [STEP] {nombre}  (CUT_CU={capas.get('CUT_CU')})")
            else:
                total_singap += 1
                singap_files.append(nombre)
                capas_txt = ", ".join(f"{k}:{v}" for k, v in sorted(capas.items())) or "sin geometria"
                print(f"    [GAP-] {nombre}  (sin CUT_CU | {capas_txt})")

    print("\n" + "=" * 60)
    print("RESUMEN")
    print("=" * 60)
    print(f"  DXF que seran STEP (con CUT_CU) : {total_step}")
    print(f"  DXF sin_gap (vertical, sin STEP): {total_singap}")
    if total_error:
        print(f"  DXF ilegibles                  : {total_error}")
    print(f"  TOTAL DXF                        : {total_step + total_singap + total_error}")

    if singap_files:
        print("\n  --- sin_gap (se quedan como DXF) ---")
        for n in singap_files:
            print(f"    {n}")


if __name__ == "__main__":
    main()
