"""Benchmark OCCT A+B (chunked + 1 build) vs FreeCAD W.O. 34 (~110 s)."""
from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "CAD (OCCT)"))

from engine.dxf_to_step import (  # noqa: E402
    export_dxf_to_step_robot_camas,
    thickness_mm_from_dxf_name,
)

DXF = Path(
    r"C:\Users\jose_rosales\OneDrive - grupoarga.com\Escritorio\Nesteos Locales"
    r"\PRODUCTO_TEST\CLIENTE_TEST\2000 kva de prueba\MODEL CORE FILES"
    r"\W.O. 34 X1\ARGA MODEL CORE\NESTING\ROBOT LASER + MINI NEST\DXF"
    r"\NESTING_0.375_W.O. 34 X1-H3.dxf"
)
OUT = ROOT / "_logs" / "bench_occt_wo34"
FC_REF_SEC = 110.0  # FreeCAD A+B medido en W.O. 34 (10:28:00 → 10:29:50)


def main() -> int:
    if not DXF.is_file():
        print(f"DXF no encontrado: {DXF}")
        return 1
    OUT.mkdir(parents=True, exist_ok=True)
    out_a = OUT / "Cama_A" / f"{DXF.stem}.step"
    out_b = OUT / "Cama_B" / f"{DXF.stem}.step"
    out_a.parent.mkdir(parents=True, exist_ok=True)
    out_b.parent.mkdir(parents=True, exist_ok=True)

    thk = thickness_mm_from_dxf_name(DXF.name)
    print(f"DXF: {DXF.name}")
    print(f"THK: {thk} mm")
    print(f"OUT: {OUT}")
    print(f"FreeCAD ref (W.O.34 A+B): ~{FC_REF_SEC:.0f} s")
    print("---")

    t0 = time.perf_counter()
    info = export_dxf_to_step_robot_camas(
        DXF,
        out_a,
        out_b,
        thk_mm=thk,
        material="STEEL",
        mark_mode="ENGRAVE",
    )
    wall = time.perf_counter() - t0

    print(f"OCCT build:   {info.get('sec_build')} s")
    print(f"OCCT write A: {info.get('sec_write_a')} s  ({info.get('bytes_a', 0)/1e6:.1f} MB)")
    print(f"OCCT write B: {info.get('sec_write_b')} s  ({info.get('bytes_b', 0)/1e6:.1f} MB)")
    print(f"OCCT total:   {info.get('sec_total')} s  (wall {wall:.1f} s)")
    print(f"marks={info.get('mark_segs')} solids={info.get('solids')} bbox={info.get('anchor_bbox')}")
    ratio = wall / FC_REF_SEC if FC_REF_SEC else 0.0
    print(f"vs FreeCAD:   {ratio:.2f}x ({'más lento' if ratio > 1 else 'más rápido'})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
