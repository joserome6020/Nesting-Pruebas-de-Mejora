"""Prueba unitaria: bracket DENTRO del orificio de una anfitriona.

Falla si in_holes == 0 (el bug de VFM). No usa DXF de red.
"""
from __future__ import annotations

import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from shapely.geometry import Polygon  # noqa: E402

from modules.nesting_engine.engine_registry import empaquetar_una_hoja_detalle  # noqa: E402
from modules.nesting_engine.engines.types import PackSheetRequest  # noqa: E402
from modules.nesting_engine.nest_engine_context import (  # noqa: E402
    reset_active_engine_id,
    set_active_engine_id,
)


IN = 25.4


def _host() -> dict:
    # Placa-ish host 40" x 14" with one 12" x 8" interior hole
    outer = [(0, 0), (40 * IN, 0), (40 * IN, 14 * IN), (0, 14 * IN), (0, 0)]
    hole = [
        (8 * IN, 3 * IN),
        (20 * IN, 3 * IN),
        (20 * IN, 11 * IN),
        (8 * IN, 11 * IN),
        (8 * IN, 3 * IN),
    ]
    poly = Polygon(outer, [hole])
    return {
        "nombre": "HOST-VFM",
        "poly": poly,
        "area": float(poly.area),
        "calibre": "11",
        "material": "TEST",
        "marks": None,
    }


def _bracket(i: int) -> dict:
    # 3" x 2.5" like BKT-369
    w, h = 3.0 * IN, 2.5 * IN
    poly = Polygon([(0, 0), (w, 0), (w, h), (0, h), (0, 0)])
    return {
        "nombre": f"BKT-SMALL#{i}",
        "poly": poly,
        "area": float(poly.area),
        "calibre": "11",
        "material": "TEST",
        "marks": None,
    }


def _count_in_holes(hoja: dict) -> tuple[int, int]:
    hosts = []
    for p in hoja.get("piezas") or []:
        rings = p.get("poligonos") or []
        if str(p.get("nombre") or "").startswith("HOST") and len(rings) >= 2:
            hosts.append([Polygon(r) for r in rings[1:] if r and len(r) >= 3])
    inside = outside = 0
    for p in hoja.get("piezas") or []:
        if str(p.get("nombre") or "").startswith("HOST"):
            continue
        rings = p.get("poligonos") or []
        if not rings:
            continue
        c = Polygon(rings[0]).centroid
        hit = any(h.contains(c) for holes in hosts for h in holes)
        if hit:
            inside += 1
        else:
            outside += 1
    return inside, outside


def main() -> int:
    piezas = [_host()] + [_bracket(i) for i in range(6)]
    w_mm = 120.0 * IN
    h_mm = 48.0 * IN
    token = set_active_engine_id("arga_base")
    try:
        result = empaquetar_una_hoja_detalle(
            PackSheetRequest(
                piezas=piezas,
                w_placa=w_mm,
                h_placa=h_mm,
                kerf_override=0.1,
                margin_override=0.15,
                opt_override="OPTIMIZAR LARGO Y ANCHO",
                corner_override="INFERIOR IZQUIERDA",
                mc_iterations=1,
            ),
            engine_id="arga_base",
        )
    finally:
        reset_active_engine_id(token)

    hoja = result.hoja or {}
    n = len(hoja.get("piezas") or [])
    inside, outside = _count_in_holes(hoja)
    print(f"colocadas={n}  in_holes={inside}  out={outside}  error={result.error!r}")
    if inside < 1:
        print("FAIL: ninguna pieza entró al orificio")
        return 1
    print("PASS: orificio rellenado")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
