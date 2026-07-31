"""Prueba: STEP de una hoja CAMA LASER cobre (sin capa Plate) via FreeCAD.

Verifica el fix del macro: sin PLATE, la placa se arma como compound de TODAS
las piezas (no solo la mayor), evitando 'perdida de piezas'.
"""
from __future__ import annotations

import os
import sys
import glob
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from freecad_runner import ejecutar_macro_freecad

BASE = (
    r"C:\Users\jose_rosales\OneDrive - grupoarga.com\Escritorio\Nesteos Locales"
    r"\GIGA\GIGA FLUIDSTACK - COBRE\MODEL CORE FILES\W.O. 16 X1\ARGA MODEL CORE"
    r"\NESTING\CAMA LASER SIN MINI NEST"
)
DXF_DIR = os.path.join(BASE, "DXF")
TARGET = "NESTING_0.25_W.O. 16 X1-H11.dxf"


def main():
    dxf_path = os.path.join(DXF_DIR, TARGET)
    if not os.path.isfile(dxf_path):
        print(f"[SKIP] No existe {dxf_path}")
        return

    out_dir = tempfile.mkdtemp(prefix="step_h11_")
    print(f"DXF   = {dxf_path}")
    print(f"STEP  -> {out_dir}")

    ok = ejecutar_macro_freecad(
        DXF_DIR,
        out_dir,
        0.25 * 25.4,
        "TR",
        0.0,
        0.0,
        0.0,
        material="CU",
        dxf_filter=lambda p: os.path.basename(p) == TARGET,
    )

    steps = glob.glob(os.path.join(out_dir, "*.step")) + glob.glob(
        os.path.join(out_dir, "*.STEP")
    )
    print(f"\nok={ok} | STEP generados={len(steps)}")
    for s in steps:
        print(f"  STEP -> {s} ({os.path.getsize(s)} bytes)")

    macro_log = os.path.join(out_dir, "_logs", "freecad_macro.log")
    if os.path.isfile(macro_log):
        print("\n--- cola freecad_macro.log ---")
        with open(macro_log, encoding="utf-8", errors="ignore") as fh:
            lines = fh.readlines()
        for ln in lines[-25:]:
            print(ln.rstrip())

    if steps:
        print("\n[OK] STEP generado correctamente para CAMA LASER cobre.")
    else:
        print("\n[FALLA] No se genero STEP.")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
