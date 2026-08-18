#!/usr/bin/env python
"""Candado: reparar hoja con gap < tabla: guest sin espacio se expulsa (host queda)."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


def main() -> int:
    from shapely.geometry import box, Polygon

    from modules.nesting_engine.nest_poka_yoke import (
        reparar_separacion_minima_hoja,
        validar_separacion_minima_hoja,
    )

    # Orificio tan justo que el guest no puede alejarse a 0.250" → expulsar guest.
    outer = box(20, 20, 520, 520)
    hole = box(120, 120, 200, 200)
    host = Polygon(outer.exterior.coords, [hole.exterior.coords])
    guest = box(121.0, 121.0, 199.0, 199.0)
    hoja = {
        "placa_cal": "0.375",
        "placa_w": 3000.0,
        "placa_h": 2000.0,
        "piezas": [
            {
                "nombre": "P05",
                "poly": host,
                "area": float(host.area),
                "poligonos": [list(host.exterior.coords)],
            },
            {
                "nombre": "FPP-PELSUE",
                "poly": guest,
                "area": float(guest.area),
                "poligonos": [list(guest.exterior.coords)],
            },
        ],
    }
    ok0, det0 = validar_separacion_minima_hoja(hoja, 0.25, margin_in=0.25)
    assert ok0 is False, det0

    ok, det, expelled = reparar_separacion_minima_hoja(hoja, 0.25, margin_in=0.25)
    assert ok is True, (ok, det, [p.get("nombre") for p in expelled])
    assert any(p.get("nombre") == "FPP-PELSUE" for p in expelled), expelled
    assert all(p.get("nombre") != "FPP-PELSUE" for p in hoja["piezas"])
    assert any(p.get("nombre") == "P05" for p in hoja["piezas"])

    print("REPAIR_KERF_HOLE PASS", det)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
