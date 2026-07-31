"""
Aceptacion Venom hole-fill: viga sintetica tipo VFM con orificios + chicos afuera.
PASS si apply_smart_polisher mete >=1 pieza en cavidad sin solapes graves.
"""
from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT))

from shapely.geometry import Polygon, box

from modules.nesting_engine import venom_ai
from modules.nesting_engine.geometry_parser import poligonos_desde_shapely
from modules.nesting_engine.venom_hole_fill import count_pieces_in_cavities, list_host_cavities


def _make_vfm_beam(x0: float, y0: float, L: float, W: float, holes: list[tuple[float, float, float, float]]) -> Polygon:
    """Rectangulo con huecos interiores (cerrados) — orificios de viga."""
    outer = box(x0, y0, x0 + L, y0 + W)
    hole_polys = []
    for hx, hy, hw, hh in holes:
        hole_polys.append(box(x0 + hx, y0 + hy, x0 + hx + hw, y0 + hy + hh))
    poly = outer
    for h in hole_polys:
        poly = poly.difference(h)
    if hasattr(poly, "geoms"):
        poly = max(poly.geoms, key=lambda g: g.area)
    return poly


def _piece(nombre: str, poly: Polygon) -> dict:
    return {
        "nombre": nombre,
        "area": float(poly.area),
        "poly": poly,
        "poly_exact": poly,
        "poligonos": poligonos_desde_shapely(poly),
    }


def main() -> int:
    # Viga ~78" x 11" con 3 orificios grandes (mm)
    inch = 25.4
    beam = _make_vfm_beam(
        50 * inch,
        5 * inch,
        78.0 * inch,
        11.0 * inch,
        holes=[
            (8 * inch, 2.5 * inch, 12 * inch, 6 * inch),
            (30 * inch, 2.5 * inch, 12 * inch, 6 * inch),
            (52 * inch, 2.5 * inch, 12 * inch, 6 * inch),
        ],
    )

    # Chicos ~3.8" x 3.6" afuera (como en la captura), caben en orificios 12x6
    guests = []
    for i in range(6):
        g = box(2 * inch, (2 + i * 4) * inch, (2 + 3.84) * inch, (2 + i * 4 + 3.61) * inch)
        guests.append(_piece(f"GENE-35-0820-708#{i}", g))

    host = _piece("GENE-VFM-20-102", beam)
    hoja = {
        "placa_w": 120.0 * inch,
        "placa_h": 36.0 * inch,
        "kerf_usado": 0.118,
        "piezas": [host] + guests,
    }

    cavs = list_host_cavities(beam, open_profile=False)
    print(f"diag cavities={len(cavs)} areas_in2={[round(c.area/(inch*inch),1) for c in cavs]}")
    assert len(cavs) >= 3, "expected closed holes on synthetic VFM"

    before = count_pieces_in_cavities(hoja)
    print(f"before in_cav={before}")
    venom_ai.apply_smart_polisher(hoja, "accept_vfm_fill")
    after = count_pieces_in_cavities(hoja)
    filled = int(hoja.get("venom_fill_count") or 0)
    print(f"after in_cav={after} venom_fill_count={filled}")
    print(f"fill meta hosts={hoja.get('venom_fill_hosts')} cavities={hoja.get('venom_fill_cavities')}")

    # Overlap check rough
    from shapely.ops import unary_union

    polys = []
    for p in hoja["piezas"]:
        poly = p.get("poly_exact") or p.get("poly")
        if poly is not None:
            polys.append(poly)
    overlaps = 0
    for i in range(len(polys)):
        for j in range(i + 1, len(polys)):
            inter = polys[i].intersection(polys[j])
            if getattr(inter, "area", 0) > 1.0:
                overlaps += 1
    print(f"pairwise_overlaps_area>1={overlaps}")

    ok = (after >= 1 or filled >= 1) and overlaps == 0
    print("PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
