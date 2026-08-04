#!/usr/bin/env python
"""Smoke: compact-lite (band-close + remnant backfill gate)."""
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
    os.environ["ARGA_NEST_COMPACT"] = "1"
    os.environ["ARGA_NEST_VENOM"] = "0"
    os.environ["ARGA_NEST_BAND_CLOSE"] = "0"

    from modules.nesting_engine import compact_lite
    from modules.nesting_engine import venom_band_close as vbc

    assert compact_lite.compact_enabled() is True
    # COMPACT implica band-close
    assert vbc.band_close_enabled() is True

    placa_w = 120.0 * 25.4
    placa_h = 48.0 * 25.4

    # Tres paneles abajo (como P1) + aire arriba para un panel mediano.
    big_h = 32.1 * 25.4
    big_w = 34.7 * 25.4
    row = [
        _piece("GS-BIG-1", _rect(10, 10, 10 + big_w, 10 + big_h)),
        _piece("GS-BIG-2", _rect(10 + big_w + 20, 10, 10 + 2 * big_w + 20, 10 + big_h)),
        _piece("GS-BIG-3", _rect(10 + 2 * big_w + 40, 10, 10 + 3 * big_w + 40, 10 + big_h)),
    ]
    # Columnas con pasillo (~120 mm) para band-close
    col_gap = [
        _piece("C-A1", _rect(50, 50, 200, 200)),
        _piece("C-A2", _rect(50, 220, 200, 370)),
        _piece("C-B1", _rect(350, 50, 500, 200)),  # gap ~150 mm en X
        _piece("C-B2", _rect(350, 220, 500, 370)),
    ]

    hoja_cols = {
        "placa_w": placa_w,
        "placa_h": placa_h,
        "kerf_usado": 0.15,
        "piezas": col_gap,
    }
    gap_before = min(p["poly"].bounds[0] for p in col_gap if p["nombre"].startswith("C-B")) - max(
        p["poly"].bounds[2] for p in col_gap if p["nombre"].startswith("C-A")
    )
    assert gap_before > 100.0, gap_before

    stats = compact_lite.apply_band_compact(hoja_cols, engine_id="test")
    gap_after = min(
        float(p["poly"].bounds[0]) for p in hoja_cols["piezas"] if p["nombre"].startswith("C-B")
    ) - max(
        float(p["poly"].bounds[2]) for p in hoja_cols["piezas"] if p["nombre"].startswith("C-A")
    )
    print("BAND", stats, "GAP", round(gap_before, 1), "->", round(gap_after, 1))
    assert gap_after < gap_before - 40.0, f"band compact failed: {gap_before}->{gap_after}"

    # Backfill: hoja con 3 big + leftover que cabe arriba
    med_w = 20.2 * 25.4
    med_h = 7.97 * 25.4
    leftover = [_piece("GS-MED", _rect(0, 0, med_w, med_h))]
    hoja_p1 = {
        "placa_w": placa_w,
        "placa_h": placa_h,
        "kerf_usado": 0.15,
        "piezas": [copy_piece(p) for p in row],
    }

    n_before = len(hoja_p1["piezas"])
    rest = compact_lite.densify_sheet(
        hoja_p1,
        leftover,
        w_placa=placa_w,
        h_placa=placa_h,
        kerf=0.15,
        margin=0.0,
        mc_iterations=1,
        clave="test",
        engine_id="test",
    )
    n_after = len(hoja_p1["piezas"])
    print("BACKFILL", n_before, "->", n_after, "rest", len(rest))
    # Ideal: mete el mediano; si C++ no disponible, al menos no rompe.
    try:
        from modules.nesting_engine import algorithm_cpp  # noqa: F401

        assert n_after > n_before or len(rest) < len(leftover), (
            f"expected backfill progress, n={n_before}->{n_after} rest={len(rest)}"
        )
    except ImportError:
        print("SKIP backfill assert (no algorithm_cpp)")

    os.environ["ARGA_NEST_COMPACT"] = "0"
    os.environ["ARGA_NEST_BAND_CLOSE"] = "0"
    assert compact_lite.compact_enabled() is False
    assert vbc.band_close_enabled() is False

    print("COMPACT_LITE PASS")
    return 0


def copy_piece(p):
    import copy

    return copy.deepcopy(p)


if __name__ == "__main__":
    raise SystemExit(main())
