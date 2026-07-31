"""Diagnóstico geometría VFM: orificios cerrados vs cavidades abiertas AABB."""
from __future__ import annotations

import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from shapely.geometry import Polygon, box

from modules.nesting_engine.algorithm_bridge import _rings_from_shapely_polygon
from modules.nesting_engine.sim_lab import SimPieceEntry, build_pieces_from_entries

DXF_DIR = (
    r"\\192.168.2.80\Users\Administrator\Desktop\Grupo Arga Metals"
    r"\ARGA METALS CORPORATE SYSTEM\TANKS\GIGA\GIGA FLUIDSTACK"
    r"\MODEL CORE FILES\AutoDXF\Processed Files"
)
IN2 = 25.4 * 25.4


def main() -> int:
    files = [n for n in os.listdir(DXF_DIR) if n.upper().startswith("GENE-VFM-20-101") and n.lower().endswith(".dxf")]
    if not files:
        print("no VFM dxf")
        return 1
    ruta = os.path.join(DXF_DIR, files[0])
    print("file:", files[0])
    piezas, errs = build_pieces_from_entries([SimPieceEntry(ruta=ruta, qty=1, nombre="GENE-VFM-20-101")])
    print("errs:", errs)
    poly = piezas[0]["poly"]
    print("type:", poly.geom_type, "area_in2:", round(poly.area / IN2, 2))
    print("bounds_in:", [round(x / 25.4, 2) for x in poly.bounds])
    rings = _rings_from_shapely_polygon(poly)
    print("nrings:", len(rings))
    areas = sorted((Polygon(r).area / IN2 for r in rings[1:]), reverse=True)
    print("top15 hole_in2:", [round(a, 3) for a in areas[:15]])
    print("holes>=5in2:", sum(1 for a in areas if a >= 5), ">=1:", sum(1 for a in areas if a >= 1), ">=0.5:", sum(1 for a in areas if a >= 0.5))

    minx, miny, maxx, maxy = poly.bounds
    aabb = box(minx, miny, maxx, maxy)
    free = aabb.difference(poly)
    print("aabb_in2:", round(aabb.area / IN2, 2), "free_in2:", round(free.area / IN2, 2), "free:", free.geom_type)
    geoms = [free] if free.geom_type == "Polygon" else list(getattr(free, "geoms", []))
    geoms = sorted(geoms, key=lambda g: g.area, reverse=True)
    for i, g in enumerate(geoms[:10]):
        b = [round(x / 25.4, 2) for x in g.bounds]
        w = b[2] - b[0]
        h = b[3] - b[1]
        print(f"  cav[{i}] area_in2={g.area/IN2:.2f} wh={w:.2f}x{h:.2f} bounds={b}")

    # BKT fit?
    from shapely.geometry import box as bbox

    bkt = bbox(0, 0, 3.03 * 25.4, 2.57 * 25.4)
    gs = bbox(0, 0, 3.84 * 25.4, 3.61 * 25.4)
    print("bkt 3.03x2.57 area", round(bkt.area / IN2, 2))
    print("gs  3.84x3.61 area", round(gs.area / IN2, 2))
    for i, g in enumerate(geoms[:10]):
        ok_bkt = g.area >= bkt.area and min(g.bounds[2] - g.bounds[0], g.bounds[3] - g.bounds[1]) >= 2.5 * 25.4 * 0.9
        print(f"  cav[{i}] maybe_fit_bkt_rough={ok_bkt}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
