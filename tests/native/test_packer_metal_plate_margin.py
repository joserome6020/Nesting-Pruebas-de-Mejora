#!/usr/bin/env python
"""Candado: el packer C++ coloca el METAL ≥ 0.250\" de placa (no el buffer de kerf)."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

MARGIN_IN = 0.250
MARGIN_MM = MARGIN_IN * 25.4
SPIKE_MM = 0.08  # vértice que Clipper/simplify suele comerse


def _spike_piece() -> dict:
    ring = [
        (0.0, 0.0),
        (-SPIKE_MM, 50.0),
        (0.0, 100.0),
        (200.0, 100.0),
        (200.0, 0.0),
        (0.0, 0.0),
    ]
    area = 200.0 * 100.0
    return {
        "nombre": "SPIKE-SIVC",
        "area": area,
        "calibre": "0.105",
        "material": "GALVANIZADO",
        "rings": [ring],
        "marks": [],
    }


def _min_xy(hoja: dict) -> tuple[float, float]:
    minx = miny = 1e9
    for p in hoja.get("piezas") or []:
        for ring in p.get("poligonos") or []:
            for pt in ring:
                minx = min(minx, float(pt[0]))
                miny = min(miny, float(pt[1]))
    return minx, miny


def main() -> int:
    from modules.nesting_engine import algorithm_cpp as cpp

    piezas = [_spike_piece()]
    w, h = 3048.0, 1524.0
    hoja, _restos, *_ = cpp.empaquetar_una_hoja_svgnest_ultra(
        piezas,
        w,
        h,
        0.15,
        MARGIN_IN,
        "OPTIMIZAR LARGO Y ANCHO",
        "INFERIOR IZQUIERDA",
        None,
        4,
        1,
        90.0,
        False,
        1,
        None,
    )
    assert hoja.get("piezas"), "Ultra no colocó la pieza"
    minx, miny = _min_xy(hoja)
    assert minx + 1e-6 >= MARGIN_MM, f"Ultra metal X={minx:.4f} < 0.250in ({MARGIN_MM:.4f})"
    assert miny + 1e-6 >= MARGIN_MM, f"Ultra metal Y={miny:.4f} < 0.250in ({MARGIN_MM:.4f})"

    hoja_b, _r2 = cpp.empaquetar_una_hoja_base(
        piezas,
        w,
        h,
        0.15,
        MARGIN_IN,
        "OPTIMIZAR LARGO Y ANCHO",
        "INFERIOR IZQUIERDA",
        None,
    )
    assert hoja_b.get("piezas"), "Base/Lite no colocó la pieza"
    minx_b, miny_b = _min_xy(hoja_b)
    assert minx_b + 1e-6 >= MARGIN_MM, f"Base metal X={minx_b:.4f} < 0.250in"
    assert miny_b + 1e-6 >= MARGIN_MM, f"Base metal Y={miny_b:.4f} < 0.250in"

    print(
        f"PACKER_METAL_PLATE_MARGIN PASS ultra=({minx:.3f},{miny:.3f}) "
        f"base=({minx_b:.3f},{miny_b:.3f}) min={MARGIN_MM:.3f}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
