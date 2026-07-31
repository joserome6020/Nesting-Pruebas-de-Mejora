"""Repara CUT_CU fragmentado en DXF cobre y valida conteo STEP."""
from __future__ import annotations

import math
import re
import shutil
import sys
from pathlib import Path

import ezdxf

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

SRC_DXF = Path(
    r"C:\Users\jose_rosales\OneDrive - grupoarga.com\Escritorio\Nesteos Locales"
    r"\GIGA\GIGA FLUIDSTACK - COBRE\MODEL CORE FILES\W.O. 3 X1\ARGA MODEL CORE"
    r"\NESTING\CAMA LASER SIN MINI NEST\DXF\NESTING_0.25_W.O. 3 X1-H1.dxf"
)
OUT_DIR = ROOT / "_logs" / "h1_step_fix_test"


def _seg_bbox(seg):
    x1, y1, x2, y2 = seg
    return min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2)


def _infer_piece_x_ranges(segments, plate_bb, tol=0.5):
    """Cortes verticales interiores + extremos de barra → rangos X por pieza."""
    xmin_p, ymin_p, xmax_p, ymax_p = plate_bb
    cuts = {round(xmin_p, 3), round(xmax_p, 3)}
    bar_h = ymax_p - ymin_p
    for x1, y1, x2, y2 in segments:
        if abs(x1 - x2) > tol:
            continue
        x = (x1 + x2) * 0.5
        ymin = min(y1, y2)
        ymax = max(y1, y2)
        if ymax - ymin >= bar_h * 0.85:
            cuts.add(round(x, 3))
    xs = sorted(cuts)
    ranges = []
    for a, b in zip(xs, xs[1:]):
        if b - a > 1.0:
            ranges.append((a, b))
    return ranges


def _ring_from_segments(segments, x0, x1, y0, y1):
    """Rectángulo cerrado del envelope de segmentos en el rango (cobre largos)."""
    return [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]


def repair_cut_cu_polylines(dxf_path: Path) -> int:
    doc = ezdxf.readfile(str(dxf_path))
    msp = doc.modelspace()

    plate_bb = None
    for e in msp.query("LWPOLYLINE"):
        if str(e.dxf.layer).upper() == "PLATE" and e.closed:
            pts = [(float(x), float(y)) for x, y, *_ in e.get_points("xy")]
            xs = [p[0] for p in pts]
            ys = [p[1] for p in pts]
            plate_bb = (min(xs), min(ys), max(xs), max(ys))
            break
    if not plate_bb:
        raise RuntimeError("sin PLATE")

    segments = []
    to_delete = []
    for e in msp:
        if str(e.dxf.layer).upper() != "CUT_CU":
            continue
        if e.dxftype() == "LINE":
            segments.append(
                (
                    float(e.dxf.start.x),
                    float(e.dxf.start.y),
                    float(e.dxf.end.x),
                    float(e.dxf.end.y),
                )
            )
            to_delete.append(e)
        elif e.dxftype() == "LWPOLYLINE":
            if e.closed:
                return len(list(msp.query("LWPOLYLINE")))
            to_delete.append(e)

    for e in to_delete:
        msp.delete_entity(e)

    ranges = _infer_piece_x_ranges(segments, plate_bb)
    y0, y1 = plate_bb[1], plate_bb[3]
    n = 0
    for xa, xb in ranges:
        segs = [s for s in segments if xa - 0.5 <= _seg_bbox(s)[0] and _seg_bbox(s)[2] <= xb + 0.5]
        if not segs:
            continue
        ring = _ring_from_segments(segs, xa, xb, y0, y1)
        msp.add_lwpolyline(ring, dxfattribs={"layer": "CUT_CU", "closed": True})
        n += 1

    doc.saveas(str(dxf_path))
    return n


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    test_dxf = OUT_DIR / "NESTING_0.25_W.O. 3 X1-H1.dxf"
    shutil.copy2(SRC_DXF, test_dxf)

    n = repair_cut_cu_polylines(test_dxf)
    print(f"CUT_CU polylines reparadas: {n}")

    from freecad_runner import ejecutar_macro_freecad

    step_dir = OUT_DIR / "step"
    step_dir.mkdir(exist_ok=True)
    ok = ejecutar_macro_freecad(
        str(test_dxf.parent),
        str(step_dir),
        6.35,
        "TR",
        0,
        0,
        0,
        material="CU",
    )
    print(f"FreeCAD OK={ok}")

    log = step_dir / "_logs" / "freecad_macro.log"
    if log.is_file():
        for line in log.read_text(encoding="utf-8", errors="ignore").splitlines():
            if "OUTER:" in line or "STEP IPT" in line or "SKIP" in line:
                print(line)

    step = step_dir / "W.O. 3 X1-H1.step"
    if step.is_file():
        text = step.read_text(encoding="utf-8", errors="ignore")
        solids = len(re.findall(r"MANIFOLD_SOLID_BREP", text, re.I))
        print(f"STEP solids: {solids}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
