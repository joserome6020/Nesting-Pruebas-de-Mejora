"""Audita piezas CUT_CU en DXF vs conteo esperado."""
from __future__ import annotations

import re
import sys
from collections import defaultdict
from pathlib import Path

import ezdxf

DXF = Path(
    r"C:\Users\jose_rosales\OneDrive - grupoarga.com\Escritorio\Nesteos Locales"
    r"\GIGA\GIGA FLUIDSTACK - COBRE\MODEL CORE FILES\W.O. 3 X1\ARGA MODEL CORE"
    r"\NESTING\CAMA LASER SIN MINI NEST\DXF\NESTING_0.25_W.O. 3 X1-H1.dxf"
)
STEP = Path(
    r"C:\Users\jose_rosales\OneDrive - grupoarga.com\Escritorio\Nesteos Locales"
    r"\GIGA\GIGA FLUIDSTACK - COBRE\MODEL CORE FILES\W.O. 3 X1\ARGA MODEL CORE"
    r"\NESTING\CAMA LASER SIN MINI NEST\STEP\W.O. 3 X1-H1.step"
)


def bbox_pts(pts):
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    return min(xs), min(ys), max(xs), max(ys)


def main() -> int:
    if not DXF.is_file():
        print(f"DXF no encontrado: {DXF}")
        return 1

    doc = ezdxf.readfile(str(DXF))
    msp = doc.modelspace()

    by_layer: dict[str, int] = defaultdict(int)
    for e in msp:
        by_layer[str(e.dxf.layer)] += 1

    print("=== ENTITIES BY LAYER ===")
    for k, v in sorted(by_layer.items()):
        print(f"  {k}: {v}")

    plate_bb = None
    for e in msp.query("LWPOLYLINE"):
        if str(e.dxf.layer).upper() == "PLATE" and e.closed:
            pts = [(float(x), float(y)) for x, y, *_ in e.get_points("xy")]
            plate_bb = bbox_pts(pts)
            break

    if plate_bb:
        pw = (plate_bb[2] - plate_bb[0]) / 25.4
        ph = (plate_bb[3] - plate_bb[1]) / 25.4
        print(f"\nPLATE: {pw:.2f}\" x {ph:.2f}\"")

    # CUT_CU closed polylines
    cu_polys = []
    for e in msp.query("LWPOLYLINE"):
        if str(e.dxf.layer).upper() == "CUT_CU" and e.closed:
            pts = [(float(x), float(y)) for x, y, *_ in e.get_points("xy")]
            cu_polys.append(pts)

    print(f"\nCUT_CU closed LWPOLYLINE: {len(cu_polys)}")

    plate_area = 0.0
    if plate_bb:
        plate_area = (plate_bb[2] - plate_bb[0]) * (plate_bb[3] - plate_bb[1])

    pieces = []
    for i, pts in enumerate(cu_polys, 1):
        bb = bbox_pts(pts)
        area = (bb[2] - bb[0]) * (bb[3] - bb[1])
        if plate_area and area > plate_area * 0.95:
            print(f"  poly#{i}: PLATE-sized (skip) area={area:.0f}")
            continue
        length_in = (bb[2] - bb[0]) / 25.4
        pieces.append((i, bb, length_in))
        print(
            f"  poly#{i}: x=[{bb[0]:.1f},{bb[2]:.1f}] L={length_in:.2f}\" "
            f"area={area:.0f}"
        )

    print(f"\nPiece contours from closed CUT_CU polys: {len(pieces)}")

    # CUT_CU insert/block instances
    inserts = [e for e in msp if e.dxftype() == "INSERT" and str(e.dxf.layer).upper() == "CUT_CU"]
    print(f"CUT_CU INSERT blocks: {len(inserts)}")

    # CUT_OUTER closed (laser)
    co = sum(
        1
        for e in msp.query("LWPOLYLINE")
        if str(e.dxf.layer).upper() == "CUT_OUTER" and e.closed
    )
    print(f"CUT_OUTER closed polys: {co}")

    # Count edges on CUT_CU (fragmented geometry)
    n_line = n_arc = n_poly_open = 0
    for e in msp:
        if str(e.dxf.layer).upper() != "CUT_CU":
            continue
        t = e.dxftype()
        if t == "LINE":
            n_line += 1
        elif t == "ARC":
            n_arc += 1
        elif t == "LWPOLYLINE" and not e.closed:
            n_poly_open += 1
    print(f"CUT_CU primitives: LINE={n_line} ARC={n_arc} open_LWPOLY={n_poly_open}")

    if STEP.is_file():
        text = STEP.read_text(encoding="utf-8", errors="ignore")
        solids = len(re.findall(r"MANIFOLD_SOLID_BREP", text, re.I))
        print(f"\nSTEP MANIFOLD_SOLID_BREP: {solids}")
    else:
        print(f"\nSTEP no encontrado: {STEP}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
