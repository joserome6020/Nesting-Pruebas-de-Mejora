"""Candado SWO-076: metal a 0.225\"/0.230\" debe subir a ≥0.250\" de placa.

Causa real: holgura 0.5 mm en Ultra + ALIGN_TOL 8 mm en export dejaban
CUT_OUTER a 5.715 mm (0.225\") del canto. La tabla pide 0.250\" = 6.35 mm.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from shapely.geometry import box

from modules.dxf_export.validate import validate_plasma_piece
from modules.nesting_engine.nest_poka_yoke import (
    reparar_separacion_minima_hoja,
    validar_separacion_minima_hoja,
)

MARGIN_IN = 0.250
MARGIN_MM = MARGIN_IN * 25.4
PLATE_W = 240.0 * 25.4
PLATE_H = 48.0 * 25.4
SHORT_225 = 0.225 * 25.4  # 5.715 mm — valor medido en H2/H3
SHORT_230 = 0.2305 * 25.4  # ~H6/H7


def _hoja(min_mm: float) -> dict:
    pw, ph = 50.0 * 25.4, 40.0 * 25.4
    poly = box(min_mm, min_mm, min_mm + pw, min_mm + ph)
    return {
        "placa_w": PLATE_W,
        "placa_h": PLATE_H,
        "kerf_usado": 0.15,
        "margin_usado": MARGIN_IN,
        "clave": "0.1875_A 36",
        "piezas": [
            {
                "nombre": "SWO076-SEG",
                "poly": poly,
                "poligonos": [list(poly.exterior.coords)],
                "shift_x": min_mm,
                "shift_y": min_mm,
            }
        ],
    }


def main() -> int:
    for tag, short in (("0.225", SHORT_225), ("0.2305", SHORT_230)):
        hoja = _hoja(short)
        ok0, det0 = validar_separacion_minima_hoja(hoja, 0.15, margin_in=MARGIN_IN)
        assert ok0 is False and "margen_placa" in det0, (tag, ok0, det0)

        ok1, det1, expelled = reparar_separacion_minima_hoja(
            hoja, 0.15, margin_in=MARGIN_IN, permitir_expulsar=False
        )
        assert ok1 is True, (tag, ok1, det1)
        assert not expelled, (tag, expelled)
        b = hoja["piezas"][0]["poly"].bounds
        assert b[0] + 1e-6 >= MARGIN_MM, (tag, b[0], MARGIN_MM)
        assert b[1] + 1e-6 >= MARGIN_MM, (tag, b[1], MARGIN_MM)

    # Export validator: 0.225" debe fallar (ya no hay holgura 0.5 mm).
    class _E:
        def __init__(self, x0, y0, x1, y1):
            self.dxftype = lambda: "LINE"
            self.dxf = type(
                "D",
                (),
                {
                    "layer": "CUT_OUTER",
                    "start": type("P", (), {"x": x0, "y": y0})(),
                    "end": type("P", (), {"x": x1, "y": y1})(),
                },
            )()

    # Rectángulo mínimo en 0.225" — valida bbox vía entidades.
    m = SHORT_225
    pw, ph = 100.0, 80.0
    ents = [
        _E(m, m, m + pw, m),
        _E(m + pw, m, m + pw, m + ph),
        _E(m + pw, m + ph, m, m + ph),
        _E(m, m + ph, m, m),
    ]
    sheet = {
        "length": PLATE_W,
        "width": PLATE_H,
        "kerf_usado": 0.15,
        "margin_usado": MARGIN_IN,
    }
    # nest outer alineado al mismo sitio (offset=0) para no disparar DXF≠nest
    nest_poly = box(m, m, m + pw, m + ph)
    issues = validate_plasma_piece(
        {
            "outer": list(nest_poly.exterior.coords),
            "part_name": "SEG",
        },
        ents,
        offset_mm=0.0,
        sheet=sheet,
        all_piece_bounds=[],
    )
    assert any("margen placa" in str(i) for i in issues), issues

    print("MARGEN_PLACA_0_225_NUDGE PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
