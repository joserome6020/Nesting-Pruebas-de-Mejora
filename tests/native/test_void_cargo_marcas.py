#!/usr/bin/env python
"""Candado: piezas metidas en orificios (Lite void-first) llegan a la hoja con ``marcas``.

Caso real 2026-10-01 (Southwest, 0.375_A 36): ``expand_void_cargo_onto_hoja``
agregaba piezas pack-ready sin clave ``marcas``; al promover la hoja RTZ el
manager hacía ``p_clon['marcas']`` → KeyError y el lote quedaba incompleto.
Además el marcaje del cargo no se movía con la pieza.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


def main() -> int:
    from shapely import affinity
    from shapely.geometry import LineString, Polygon, box

    from modules.nesting_engine.geometry_parser import poligonos_desde_shapely
    from modules.nesting_engine.venom_hole_fill import (
        expand_void_cargo_onto_hoja,
        prefill_voids_in_pool,
    )

    host_poly = Polygon(
        [(0, 0), (1000, 0), (1000, 1000), (0, 1000)],
        [[(100, 100), (900, 100), (900, 900), (100, 900)]],
    )
    host = {
        "nombre": "HOST",
        "poly": host_poly,
        "poly_exact": host_poly,
        "poligonos": poligonos_desde_shapely(host_poly),
        "area": float(host_poly.area),
        "material": "A 36",
    }
    guest_poly = box(0, 0, 200, 100)
    guest_mark = LineString([(20, 50), (180, 50)])
    guest = {
        "nombre": "GUEST",
        "poly": guest_poly,
        "poly_exact": guest_poly,
        "poligonos": poligonos_desde_shapely(guest_poly),
        "marks_exact": guest_mark,
        "marks": guest_mark,
        "area": float(guest_poly.area),
        "material": "A 36",
    }

    mc_pool, stats = prefill_voids_in_pool([host, guest], 9.525, engine_id="arga_lite")
    assert int(stats.get("filled") or 0) == 1, stats
    host_mc = next(p for p in mc_pool if p.get("nombre") == "HOST")
    assert host_mc.get("_void_cargo"), "host sin cargo"

    placed_poly = affinity.translate(host_poly, 2000, 500)
    hoja = {
        "piezas": [
            {
                "nombre": "HOST",
                "poligonos": poligonos_desde_shapely(placed_poly),
                "marcas": [],
                "area": float(placed_poly.area),
            }
        ],
        "area_usada": float(placed_poly.area),
    }
    n = expand_void_cargo_onto_hoja(hoja, mc_pool, engine_id="arga_lite")
    assert n == 1, n

    cargo = [p for p in hoja["piezas"] if p.get("nombre") == "GUEST"]
    assert len(cargo) == 1, hoja["piezas"]
    g = cargo[0]
    assert "marcas" in g, "cargo sin clave 'marcas' (KeyError en promoción RTZ)"
    assert g["marcas"], "cargo perdió el marcaje"

    g_poly = g["poly_exact"]
    assert placed_poly.exterior.distance(g_poly) >= 0, g_poly
    assert Polygon(placed_poly.interiors[0]).contains(g_poly), g_poly.bounds
    for ring in g["marcas"]:
        ln = LineString(ring)
        assert g_poly.buffer(1e-6).contains(ln), (ln.bounds, g_poly.bounds)

    # El manager promueve piezas de hoja RTZ con .get (no KeyError).
    src = (ROOT / "modules" / "nesting_engine" / "manager.py").read_text(encoding="utf-8")
    assert "if p_clon['marcas']:" not in src
    print("VOID_CARGO_MARCAS PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
