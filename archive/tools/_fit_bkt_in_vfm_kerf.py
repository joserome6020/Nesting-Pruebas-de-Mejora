"""¿Cabe algún BKT de H34 en cavidades VFM con kerf 0.3 legal?"""
from __future__ import annotations

import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from shapely.affinity import rotate, translate
from shapely.geometry import Polygon, box
from shapely.ops import unary_union

from modules.nesting_engine.sim_lab import SimPieceEntry, build_pieces_from_entries

DXF = (
    r"\\192.168.2.80\Users\Administrator\Desktop\Grupo Arga Metals"
    r"\ARGA METALS CORPORATE SYSTEM\TANKS\GIGA\GIGA FLUIDSTACK"
    r"\MODEL CORE FILES\AutoDXF\Processed Files"
)
IN = 25.4
KERF = 0.3


def find(item: str) -> str:
    for n in os.listdir(DXF):
        if n.upper().startswith(item.upper()) and n.lower().endswith(".dxf"):
            return os.path.join(DXF, n)
    raise FileNotFoundError(item)


def piece_poly(name: str) -> Polygon:
    pcs, _ = build_pieces_from_entries([SimPieceEntry(ruta=find(name), qty=1, nombre=name)])
    p = pcs[0]
    poly = p.get("poly")
    if poly is not None and not poly.is_empty:
        return poly
    rings = p["poligonos"]
    return Polygon(rings[0], rings[1:] if len(rings) > 1 else None)


def open_cavs(host: Polygon):
    free = box(*host.bounds).difference(host)
    geoms = [free] if free.geom_type == "Polygon" else list(getattr(free, "geoms", []) or [])
    out = []
    for g in geoms:
        if g.is_empty or g.area < 5 * IN * IN:
            continue
        if g.area > host.envelope.area * 0.85:
            continue
        # Encoger kerf (igual que make_void_limit): shrink = kerf completo por borde
        sh = g.buffer(-KERF * IN, join_style=2)
        if sh.is_empty:
            continue
        parts = [sh] if sh.geom_type == "Polygon" else list(sh.geoms)
        for p in parts:
            if p.is_empty or p.area < 1 * IN * IN:
                continue
            minx, miny, maxx, maxy = p.bounds
            out.append(
                {
                    "raw": g,
                    "lim": p,
                    "w": (maxx - minx) / IN,
                    "h": (maxy - miny) / IN,
                    "a": p.area / (IN * IN),
                }
            )
    return out


def try_fit(piece: Polygon, lim: Polygon, step_in: float = 0.25) -> bool:
    """BLF grid: ¿existe pose 0/90 donde piece ⊂ lim?"""
    for ang in (0, 90):
        pr = rotate(piece, ang, origin="centroid")
        minx, miny, maxx, maxy = pr.bounds
        pw, ph = maxx - minx, maxy - miny
        lx, ly, LX, LY = lim.bounds
        if pw > (LX - lx) + 1e-6 or ph > (LY - ly) + 1e-6:
            continue
        # recentrar a origen de pieza
        pr0 = translate(pr, -minx, -miny)
        x = lx
        while x + pw <= LX + 1e-6:
            y = ly
            while y + ph <= LY + 1e-6:
                cand = translate(pr0, x, y)
                if lim.contains(cand) or lim.buffer(1e-6).contains(cand):
                    return True
                y += step_in * IN
            x += step_in * IN
    return False


def main() -> int:
    host = piece_poly("GENE-VFM-20-101")
    print(f"VFM bbox={(host.bounds[2]-host.bounds[0])/IN:.2f}x{(host.bounds[3]-host.bounds[1])/IN:.2f}\"")
    cavs = open_cavs(host)
    print(f"cavidades legales (kerf {KERF}\"): {len(cavs)}")
    for i, c in enumerate(cavs):
        print(f"  cav[{i}] lim AABB {c['w']:.2f}x{c['h']:.2f}\" area={c['a']:.1f} in²")

    bkts = [
        "GENE-BKT-321",
        "GENE-BKT-294",
        "GENE-BKT-304",
        "GENE-BKT-306",
    ]
    for name in bkts:
        p = piece_poly(name)
        minx, miny, maxx, maxy = p.bounds
        print(f"\n{name} bbox={(maxx-minx)/IN:.3f}x{(maxy-miny)/IN:.3f}\"")
        fits_any = False
        for i, c in enumerate(cavs):
            ok = try_fit(p, c["lim"], step_in=0.5)
            print(f"  vs cav[{i}]: {'FIT' if ok else 'NO'}")
            fits_any = fits_any or ok
        print(f"  → legal cavity fit: {fits_any}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
