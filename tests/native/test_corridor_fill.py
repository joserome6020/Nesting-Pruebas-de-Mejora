#!/usr/bin/env python
"""Smoke: relleno de pasillos entre hosts (ARGA_NEST_CORRIDOR_FILL)."""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


def _rect(x0: float, y0: float, x1: float, y1: float):
    from shapely.geometry import box

    return box(x0, y0, x1, y1)


def _piece(nombre: str, poly, *, host_hint: bool = False) -> dict:
    return {
        "nombre": nombre,
        "poly": poly,
        "poly_exact": poly,
        "area": float(poly.area),
        "poligonos": [[[float(x), float(y)] for x, y in poly.exterior.coords]],
    }


def main() -> int:
    os.environ["ARGA_NEST_CORRIDOR_FILL"] = "1"

    from modules.nesting_engine import venom_hole_fill as vhf

    assert vhf.corridor_fill_enabled() is True

    # Placa ~120" x 48" en mm; dos marcos apilados con pasillo; BKTs a la derecha.
    placa_w = 120.0 * 25.4
    placa_h = 48.0 * 25.4
    # Hosts: barras horizontales largas (área host).
    h1 = _rect(50, 200, 2000, 450)
    h2 = _rect(50, 650, 2000, 900)
    # Gap Y ≈ 200..650 → pasillo ~200 mm de alto
    g1 = _rect(2200, 100, 2350, 220)
    g2 = _rect(2400, 100, 2550, 220)
    g3 = _rect(2600, 100, 2750, 220)

    hoja = {
        "placa_w": placa_w,
        "placa_h": placa_h,
        "kerf_usado": 0.0,
        "piezas": [
            _piece("GENE-WFM-20-101", h1),
            _piece("GENE-WFM-20-101", h2),
            _piece("GENE-BKT-240", g1),
            _piece("GENE-BKT-219", g2),
            _piece("GENE-BKT-229", g3),
        ],
    }

    # Detección de hosts
    assert vhf._is_host(h1, hoja["piezas"][0]), "WFM must count as host"

    # Corridor pockets extractables
    from shapely.ops import unary_union

    hosts = [
        {"idx": 0, "poly": h1, "p": hoja["piezas"][0]},
        {"idx": 1, "poly": h2, "p": hoja["piezas"][1]},
    ]
    occ = unary_union([h1, h2, g1, g2, g3])
    free = _rect(1, 1, placa_w - 1, placa_h - 1).difference(occ)
    pockets = vhf._corridor_pockets_between_hosts(hosts, free, kerf_half=0.5)
    assert pockets, "expected at least one corridor pocket between stacked hosts"
    print("POCKETS", len(pockets), "areas", [round(p.area, 1) for p in pockets[:3]])

    n = vhf.fill_sheet_free_pockets(hoja, engine_id="test")
    print("MOVED", n, "corridor", hoja.get("venom_corridor_fill"))
    assert int(hoja.get("venom_corridor_fill") or 0) >= 1 or n >= 1, (
        "expected at least one guest relocated into corridor"
    )

    # Al menos un BKT con centroid Y entre los dos hosts
    y_lo, y_hi = 450.0, 650.0
    in_gap = 0
    for p in hoja["piezas"]:
        if "BKT" not in str(p.get("nombre") or "").upper():
            continue
        cy = float(p["poly"].centroid.y)
        if y_lo - 5 <= cy <= y_hi + 5:
            in_gap += 1
    print("IN_GAP", in_gap)
    assert in_gap >= 1, "expected BKT centroid inside vertical corridor"

    # Opt-out
    os.environ["ARGA_NEST_CORRIDOR_FILL"] = "0"
    assert vhf.corridor_fill_enabled() is False

    print("CORRIDOR_FILL PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
