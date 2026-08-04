#!/usr/bin/env python
"""Smoke: cierre de bandas entre filas (ARGA_NEST_BAND_CLOSE)."""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


def _rect(x0, y0, x1, y1):
    from shapely.geometry import box

    return box(x0, y0, x1, y1)


def _piece(nombre, poly):
    return {
        "nombre": nombre,
        "poly": poly,
        "poly_exact": poly,
        "area": float(poly.area),
        "poligonos": [[[float(x), float(y)] for x, y in poly.exterior.coords]],
    }


def main() -> int:
    os.environ["ARGA_NEST_COMPACT"] = "0"  # aislar: solo BAND_CLOSE
    os.environ["ARGA_NEST_BAND_CLOSE"] = "1"
    os.environ["ARGA_NEST_CORRIDOR_FILL"] = "0"

    from modules.nesting_engine import venom_band_close as vbc

    assert vbc.band_close_enabled() is True

    placa_w = 120.0 * 25.4
    placa_h = 48.0 * 25.4

    # Dos filas de piezas con ~150 mm de aire entre ellas (como P4).
    row1 = [
        _piece("P-A1", _rect(50, 100, 400, 250)),
        _piece("P-A2", _rect(450, 110, 800, 240)),
        _piece("P-A3", _rect(850, 100, 1200, 250)),
    ]
    # Fila 2 empieza en y=400 → gap ~150 mm respecto maxy fila1 (~250)
    row2 = [
        _piece("P-B1", _rect(50, 400, 400, 550)),
        _piece("P-B2", _rect(450, 410, 800, 540)),
        _piece("P-B3", _rect(850, 400, 1200, 550)),
    ]

    hoja = {
        "placa_w": placa_w,
        "placa_h": placa_h,
        "kerf_usado": 0.0,
        "piezas": row1 + row2,
    }

    gap_before = min(p["poly"].bounds[1] for p in row2) - max(
        p["poly"].bounds[3] for p in row1
    )
    assert gap_before > 100.0, gap_before

    stats = vbc.close_inter_band_gaps(hoja, engine_id="test")
    print("STATS", stats)

    gap_after = min(
        float(p["poly"].bounds[1]) for p in hoja["piezas"] if p["nombre"].startswith("P-B")
    ) - max(
        float(p["poly"].bounds[3]) for p in hoja["piezas"] if p["nombre"].startswith("P-A")
    )
    print("GAP", round(gap_before, 1), "->", round(gap_after, 1))
    assert stats.get("bands_y", 0) >= 1 or gap_after < gap_before - 20.0, stats
    assert gap_after < gap_before - 40.0, f"expected significant close, got {gap_before}->{gap_after}"

    os.environ["ARGA_NEST_BAND_CLOSE"] = "0"
    os.environ["ARGA_NEST_COMPACT"] = "0"
    assert vbc.band_close_enabled() is False

    print("BAND_CLOSE PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
